"""卡片第一批同步回归；写入仅限临时库和测试夹具构造的私有许可。"""

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import math
import os
from pathlib import Path
import sqlite3
import tempfile
from threading import Barrier
from time import monotonic
import unittest
from unittest.mock import patch

from bill_analysis.sample import DATASET_ID
from bill_analysis.service import AnalysisService
from bill_analysis.storage import Dataset, SpendingRecord, import_dataset
from card_management import authorization
from card_management.sample import CARD_A, CARD_B, CARD_LOST, CARD_SPARE, card_samples, initialize_card_demo
from card_management.service import CardService
from card_management.storage import CardCommand, CardError, import_card_samples, plan_transition, validate_card
from card_management.transitions import apply_temporary_action
from database.db import init_db
from security.daily_limit import business_day, outgoing_cents
from security.risk_engine import RiskEngine


class CardManagementTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "cards.db"
        # 初始化器不允许向进程配置的正式目标写样本，因此用另一未创建路径作为保护目标。
        env = patch.dict(os.environ, {"BANK_DB_PATH": str(Path(self.temp.name) / "protected.db")})
        env.start(); self.addCleanup(env.stop)
        initialize_card_demo(self.path)
        self.conn = sqlite3.connect(self.path)
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.addCleanup(self.conn.close)
        self.service = CardService(self.path, account_id="1001")

    def assert_code(self, code, callback):
        with self.assertRaises(CardError) as caught:
            callback()
        self.assertEqual(caught.exception.code, code)

    def command(self, **changes):
        return replace(CardCommand("1001", CARD_A, "temporary_freeze", 0, "test-action-one"), **changes)

    @contextmanager
    def permit_for(self, command, *, database=None, expires_at=None, shared=None):
        """只存在于测试：不是生产许可发放器，也不是对HTTP确认的替代。"""
        permit = shared or authorization._CardPermit(str(Path(database or self.path).resolve()), command, expires_at if expires_at is not None else monotonic() + 30)
        token = authorization._active.set(permit)
        try:
            yield permit
        finally:
            authorization._active.reset(token)

    def apply_in_test(self, command):
        with self.permit_for(command):
            return apply_temporary_action(self.path, command)

    def state(self, card_id=CARD_A):
        return self.service.get_card(card_id)["card"]

    def original_tables(self):
        return {table: self.conn.execute("SELECT * FROM " + table + " ORDER BY 1").fetchall() for table in ("accounts", "bills", "audit_logs", "analysis_datasets", "spending_records")}

    def test_list_detail_and_effective_status(self):
        result = self.service.list_cards()
        self.assertEqual(len(result["cards"]), 3)
        self.assertEqual(self.state()["effective_status"], "active")
        self.assertEqual(self.state(CARD_SPARE)["effective_status"], "temporarily_frozen")
        self.assertEqual(self.state(CARD_LOST)["effective_status"], "reported_lost")
        self.assertFalse(self.state(CARD_LOST)["usable_in_demo"])

    def test_account_scoped_queries_hide_foreign_cards(self):
        other = CardService(self.path, account_id="1002")
        self.assertEqual(len(other.list_cards()["cards"]), 1)
        self.assert_code("CARD_NOT_FOUND", lambda: other.get_card(CARD_A))
        self.assert_code("CARD_NOT_FOUND", lambda: self.service.get_card(CARD_B))

    def test_missing_account_card_and_operation(self):
        self.assert_code("ACCOUNT_NOT_FOUND", lambda: CardService(self.path, account_id="missing").list_cards())
        self.assert_code("CARD_NOT_FOUND", lambda: self.service.get_card("card-unknown"))
        self.assert_code("OPERATION_NOT_FOUND", lambda: self.service.get_operation("unknown"))

    def test_explicit_consumption_mapping_for_each_account(self):
        result = self.service.card_for_consumption("oct20-a")
        self.assertTrue(result["linked"])
        self.assertEqual(result["card"]["card_id"], CARD_A)
        self.assertEqual(result["payment_instrument_ref"], "demo-instrument-wallet-a")
        other = CardService(self.path, account_id="1002").card_for_consumption("oct11")
        self.assertEqual(other["card"]["card_id"], CARD_B)

    def test_record_and_dataset_ownership_checked_before_mapping(self):
        self.assert_code("DATASET_NOT_FOUND", lambda: self.service.card_for_consumption("oct11", dataset_id="sample-spending-1002-v1"))
        self.assert_code("RECORD_NOT_FOUND", lambda: self.service.card_for_consumption("oct11"))

    def custom_record(self, reference):
        dataset = Dataset("unmapped", "1001", "v1", "2026-09-01", "2026-10-01")
        record = SpendingRecord("record-one", "1001", "2026-09-10T04:00:00.000Z", 123, "merchant", "模拟商户", "other", reference)
        import_dataset(self.conn, dataset, [record])

    def test_missing_mapping_does_not_guess_by_name_or_tail(self):
        self.custom_record("demo-instrument-unmapped")
        result = self.service.card_for_consumption("record-one", dataset_id="unmapped")
        self.assertFalse(result["linked"])
        self.assertEqual(result["reason_code"], "NO_MAPPING")
        self.assertIsNone(result["card"])

    def test_no_payment_reference_is_explicit(self):
        self.custom_record(None)
        result = self.service.card_for_consumption("record-one", dataset_id="unmapped")
        self.assertFalse(result["linked"])
        self.assertEqual(result["reason_code"], "NO_PAYMENT_INSTRUMENT")

    def test_ambiguous_dataset_not_automatically_selected(self):
        self.custom_record(None)
        self.assert_code("DATASET_AMBIGUOUS", lambda: self.service.card_for_consumption("oct20-a"))

    def test_pure_state_rules_do_not_write_database(self):
        card = card_samples()[0][0]
        before = self.path.read_bytes()
        after = plan_transition(card, self.command())
        self.assertEqual(after, {"temporary_frozen": True, "loss_status": "normal", "version": 1})
        self.assertEqual(self.path.read_bytes(), before)

    def test_successful_freeze_and_unfreeze_create_real_receipts(self):
        frozen = self.apply_in_test(self.command())
        self.assertTrue(self.state()["temporary_frozen"])
        self.assertEqual(self.state()["version"], 1)
        unfrozen = self.apply_in_test(self.command(operation="temporary_unfreeze", expected_version=1, action_id="test-action-two"))
        self.assertFalse(self.state()["temporary_frozen"])
        self.assertEqual(self.state()["version"], 2)
        self.assertNotEqual(frozen["operation_id"], unfrozen["operation_id"])
        self.assertEqual(unfrozen["before"]["version"], 1)
        self.assertEqual(unfrozen["after"]["version"], 2)

    def test_repeated_freeze_is_rejected_without_new_receipt(self):
        self.apply_in_test(self.command())
        self.assert_code("ALREADY_FROZEN", lambda: self.apply_in_test(self.command(expected_version=1, action_id="repeat")))
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM card_operations").fetchone()[0], 1)

    def test_unfreezing_normal_card_is_rejected(self):
        self.assert_code("NOT_TEMPORARILY_FROZEN", lambda: self.apply_in_test(self.command(operation="temporary_unfreeze")))
        self.assertEqual(self.state()["version"], 0)

    def test_temporary_unfreeze_can_never_remove_loss_status(self):
        before = self.state(CARD_LOST)
        self.assert_code("CARD_REPORTED_LOST", lambda: self.apply_in_test(self.command(card_id=CARD_LOST, operation="temporary_unfreeze")))
        self.assertEqual(self.state(CARD_LOST), before)

    def test_lost_card_cannot_be_managed_as_normal_card(self):
        self.assert_code("CARD_REPORTED_LOST", lambda: self.apply_in_test(self.command(card_id=CARD_LOST)))

    def test_report_loss_and_cancel_loss_have_no_execution_path(self):
        before = self.path.read_bytes()
        for operation in ("report_loss", "cancel_loss_report"):
            self.assert_code("VERIFICATION_REQUIRED", lambda: self.apply_in_test(self.command(operation=operation)))
        self.assertEqual(self.path.read_bytes(), before)

    def test_version_conflict_cannot_overwrite(self):
        self.assert_code("VERSION_CONFLICT", lambda: self.apply_in_test(self.command(expected_version=9)))
        self.assertEqual(self.state()["version"], 0)

    def test_version_integer_limits_fail_without_overflow_or_partial_import(self):
        from card_management.storage import MAX_VERSION
        card = card_samples()[0][0]
        self.assert_code("INVALID_DATA", lambda: validate_card(replace(card, version=MAX_VERSION + 1)))
        self.assert_code("INVALID_ARGUMENTS", lambda: self.apply_in_test(self.command(expected_version=MAX_VERSION + 1)))
        self.assert_code("VERSION_LIMIT", lambda: plan_transition(replace(card, version=MAX_VERSION), self.command(expected_version=MAX_VERSION)))

    def test_missing_database_cannot_be_created_even_with_test_permit(self):
        missing = Path(self.temp.name) / "not-created.db"
        with self.permit_for(self.command(), database=missing):
            self.assert_code("CARD_TRANSACTION_FAILED", lambda: apply_temporary_action(missing, self.command()))
        self.assertFalse(missing.exists())

    def test_write_checks_card_ownership_and_missing_card(self):
        self.assert_code("CARD_NOT_FOUND", lambda: self.apply_in_test(self.command(account_id="1002")))
        self.assert_code("CARD_NOT_FOUND", lambda: self.apply_in_test(self.command(card_id="card-missing")))

    def test_unapproved_direct_call_does_not_even_open_database(self):
        with patch("card_management.transitions.sqlite3.connect", side_effect=AssertionError("must not open")) as connect:
            self.assert_code("AUTHORIZATION_REQUIRED", lambda: apply_temporary_action(self.path, self.command()))
            self.assertEqual(connect.call_count, 0)
        self.assertEqual(self.state()["version"], 0)

    def test_permit_binding_rejects_changed_fields(self):
        original = self.command()
        for changes in ({"account_id": "1002"}, {"card_id": CARD_SPARE}, {"action_id": "changed"}, {"expected_version": 1}, {"operation": "temporary_unfreeze"}):
            with self.subTest(changes=changes), self.permit_for(original):
                self.assert_code("AUTHORIZATION_REQUIRED", lambda: apply_temporary_action(self.path, replace(original, **changes)))

    def test_permit_cannot_be_used_for_another_database(self):
        with self.permit_for(self.command(), database=Path(self.temp.name) / "different.db"):
            self.assert_code("AUTHORIZATION_REQUIRED", lambda: apply_temporary_action(self.path, self.command()))

    def test_expired_or_nonfinite_permit_fails_closed(self):
        for expires in (monotonic() - 1, math.inf, math.nan):
            with self.permit_for(self.command(), expires_at=expires):
                self.assert_code("AUTHORIZATION_REQUIRED", lambda: apply_temporary_action(self.path, self.command()))

    def test_permit_is_checked_again_after_write_lock(self):
        with self.permit_for(self.command(), expires_at=monotonic() + 30) as permit, patch("card_management.transitions.monotonic", return_value=monotonic() + 60):
            self.assert_code("AUTHORIZATION_EXPIRED", lambda: apply_temporary_action(self.path, self.command()))
            self.assertTrue(permit.used)
        self.assertEqual(self.state()["version"], 0)

    def test_one_permit_is_consumed_once(self):
        with self.permit_for(self.command()):
            apply_temporary_action(self.path, self.command())
            self.assert_code("AUTHORIZATION_REQUIRED", lambda: apply_temporary_action(self.path, self.command()))

    def test_receipt_replay_does_not_reexecute_state_change(self):
        original = self.apply_in_test(self.command())
        replay = self.apply_in_test(self.command())
        self.assertEqual(original["operation_id"], replay["operation_id"])
        self.assertTrue(replay["replayed"])
        self.assertEqual(self.state()["version"], 1)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM card_operations").fetchone()[0], 1)

    def test_action_id_cannot_be_rebound(self):
        self.apply_in_test(self.command())
        self.assert_code("ACTION_ID_CONFLICT", lambda: self.apply_in_test(self.command(card_id=CARD_SPARE, operation="temporary_unfreeze")))
        self.assertTrue(self.state(CARD_SPARE)["temporary_frozen"])

    def test_operation_lookup_is_account_scoped(self):
        receipt = self.apply_in_test(self.command())
        self.assertEqual(self.service.get_operation(self.command().action_id)["operation_id"], receipt["operation_id"])
        self.assert_code("OPERATION_NOT_FOUND", lambda: CardService(self.path, account_id="1002").get_operation(self.command().action_id))

    def test_same_card_concurrent_commands_cannot_both_use_old_version(self):
        barrier = Barrier(2)

        def worker(index):
            command = self.command(action_id=f"concurrent-{index}")
            with self.permit_for(command):
                barrier.wait(timeout=3)
                try:
                    apply_temporary_action(self.path, command)
                    return "completed"
                except CardError as exc:
                    return exc.code

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(worker, range(2)))
        self.assertCountEqual(results, ["completed", "VERSION_CONFLICT"])
        self.assertEqual(self.state()["version"], 1)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM card_operations").fetchone()[0], 1)

    def test_same_permit_concurrently_claimed_only_once(self):
        barrier = Barrier(2)
        shared = authorization._CardPermit(str(self.path.resolve()), self.command(), monotonic() + 30)

        def worker(_):
            with self.permit_for(self.command(), shared=shared):
                barrier.wait(timeout=3)
                try:
                    apply_temporary_action(self.path, self.command())
                    return "completed"
                except CardError as exc:
                    return exc.code

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(worker, range(2)))
        self.assertCountEqual(results, ["completed", "AUTHORIZATION_REQUIRED"])

    def test_concurrent_freeze_unfreeze_cannot_restore_using_old_version(self):
        barrier = Barrier(2)

        def worker(operation):
            command = self.command(operation=operation, action_id=operation)
            with self.permit_for(command):
                barrier.wait(timeout=3)
                try:
                    apply_temporary_action(self.path, command)
                    return (operation, "completed")
                except CardError as exc:
                    return (operation, exc.code)

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = dict(pool.map(worker, ("temporary_freeze", "temporary_unfreeze")))
        self.assertEqual(results["temporary_freeze"], "completed")
        self.assertIn(results["temporary_unfreeze"], ("VERSION_CONFLICT", "NOT_TEMPORARILY_FROZEN"))
        self.assertTrue(self.state()["temporary_frozen"])
        self.assertEqual(self.state()["version"], 1)

    def test_failed_receipt_insert_rolls_back_card_state(self):
        self.conn.execute("CREATE TRIGGER fail_receipt BEFORE INSERT ON card_operations BEGIN SELECT RAISE(ABORT,'forced'); END")
        self.conn.commit()
        self.assert_code("CARD_TRANSACTION_FAILED", lambda: self.apply_in_test(self.command()))
        self.assertFalse(self.state()["temporary_frozen"])
        self.assertEqual(self.state()["version"], 0)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM card_operations").fetchone()[0], 0)

    def test_failure_after_commit_preserves_receipt_and_never_automatically_retries(self):
        real_connect = sqlite3.connect

        class AfterCommitFailure(sqlite3.Connection):
            def commit(self):
                super().commit()
                raise sqlite3.OperationalError("after commit")

        def connect(*args, **kwargs):
            return real_connect(*args, **kwargs, factory=AfterCommitFailure)

        with patch("card_management.transitions.sqlite3.connect", side_effect=connect):
            self.assert_code("EXECUTION_UNKNOWN", lambda: self.apply_in_test(self.command()))
        self.assertEqual(self.state()["version"], 1)
        self.assertEqual(self.service.get_operation(self.command().action_id)["result_status"], "completed")
        self.assert_code("AUTHORIZATION_REQUIRED", lambda: apply_temporary_action(self.path, self.command()))
        self.assertTrue(self.apply_in_test(self.command())["replayed"])
        self.assertEqual(self.state()["version"], 1)

    def test_failure_before_commit_rolls_back_but_is_reported_conservatively(self):
        real_connect = sqlite3.connect

        class BeforeCommitFailure(sqlite3.Connection):
            def commit(self):
                raise sqlite3.OperationalError("before commit")

        with patch("card_management.transitions.sqlite3.connect", side_effect=lambda *args, **kwargs: real_connect(*args, **kwargs, factory=BeforeCommitFailure)):
            self.assert_code("EXECUTION_UNKNOWN", lambda: self.apply_in_test(self.command()))
        self.assertEqual(self.state()["version"], 0)
        self.assert_code("OPERATION_NOT_FOUND", lambda: self.service.get_operation(self.command().action_id))

    def test_clock_reversal_blocks_without_state_change(self):
        with patch("card_management.transitions.now_stamp", return_value="2020-01-01T00:00:00.000Z"):
            self.assert_code("CLOCK_ERROR", lambda: self.apply_in_test(self.command()))
        self.assertEqual(self.state()["version"], 0)

    def test_operation_ids_and_action_ids_are_unique(self):
        self.apply_in_test(self.command())
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute("INSERT INTO card_operations SELECT * FROM card_operations")
        self.conn.rollback()

    def test_only_one_receipt_can_represent_a_card_version(self):
        self.apply_in_test(self.command())
        names = [row[1] for row in self.conn.execute("PRAGMA table_info(card_operations)")]
        row = dict(zip(names, self.conn.execute("SELECT * FROM card_operations").fetchone()))
        row.update(operation_id="different-operation", action_id="different-action")
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute("INSERT INTO card_operations(" + ",".join(names) + ") VALUES(" + ",".join("?" for _ in names) + ")", [row[name] for name in names])
        self.conn.rollback()

    def test_mapping_is_immutable_in_database_and_importer(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute("UPDATE card_instrument_links SET card_id=? WHERE account_id=?", (CARD_SPARE, "1001"))
        self.conn.rollback()
        cards, links = card_samples()
        self.assert_code("SEED_CONFLICT", lambda: import_card_samples(self.conn, cards, [replace(links[0], card_id=CARD_SPARE)]))
        self.assertEqual(self.service.card_for_consumption("oct20-a")["card"]["card_id"], CARD_A)

    def test_mapping_composite_foreign_key_prevents_cross_account_links(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute("INSERT INTO card_instrument_links VALUES(?,?,?)", ("1001", "demo-instrument-wrong", CARD_B))
        self.conn.rollback()

    def test_receipts_are_append_only(self):
        self.apply_in_test(self.command())
        for sql in ("UPDATE card_operations SET executed_at='bad'", "DELETE FROM card_operations"):
            with self.assertRaises(sqlite3.IntegrityError):
                self.conn.execute(sql)
            self.conn.rollback()

    def test_database_disallows_direct_loss_status_update(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute("UPDATE cards SET loss_status='reported',version=version+1,temporary_frozen=1 WHERE card_id=?", (CARD_A,))
        self.conn.rollback()
        self.assertEqual(self.state()["loss_status"], "normal")

    def test_reimport_preserves_current_state_version_and_receipts(self):
        self.apply_in_test(self.command())
        before = self.path.read_bytes()
        result = initialize_card_demo(self.path)
        self.assertEqual(result["cards_inserted"], 0)
        self.assertEqual(self.state()["version"], 1)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM card_operations").fetchone()[0], 1)
        self.assertEqual(self.path.read_bytes(), before)

    def test_import_conflict_does_not_reset_identity(self):
        cards, links = card_samples()
        self.assert_code("SEED_CONFLICT", lambda: import_card_samples(self.conn, [replace(cards[0], last_four="9999")], links))
        self.assertEqual(self.state()["last_four"], "0101")

    def test_failed_seed_import_rolls_back_all_new_cards(self):
        cards, _ = card_samples()
        rows = [replace(cards[0], card_id="card-new-one"), replace(cards[0], card_id="card-new-two", account_id="missing")]
        self.assert_code("IMPORT_FAILED", lambda: import_card_samples(self.conn, rows, []))
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM cards WHERE card_id LIKE 'card-new-%'").fetchone()[0], 0)

    def test_full_pan_and_invalid_status_are_rejected(self):
        card = card_samples()[0][0]
        for changes in ({"last_four": "6222021234567890"}, {"display_name": "6222021234567890"}, {"card_id": "card-6222021234567890"}, {"temporary_frozen": 1}, {"loss_status": "unknown"}, {"version": True}):
            self.assert_code("INVALID_DATA", lambda: validate_card(replace(card, **changes)))
        columns = {r[1] for r in self.conn.execute("PRAGMA table_info(cards)")}
        self.assertTrue({"card_number", "pan", "cvv", "pin", "password"}.isdisjoint(columns))

    def test_reads_never_change_database_bytes(self):
        before = self.path.read_bytes()
        self.service.list_cards(); self.service.get_card(CARD_A); self.service.card_for_consumption("oct20-a")
        self.assertEqual(self.path.read_bytes(), before)
        with self.service._read() as conn:
            with self.assertRaises(sqlite3.OperationalError):
                conn.execute("UPDATE accounts SET balance_cents=1")

    def test_missing_database_or_schema_does_not_auto_initialize(self):
        missing = Path(self.temp.name) / "missing.db"
        self.assert_code("CARD_DATABASE_UNAVAILABLE", lambda: CardService(missing, account_id="1001").list_cards())
        self.assertFalse(missing.exists())
        old = Path(os.environ["BANK_DB_PATH"])
        init_db()
        self.assert_code("CARD_DATABASE_UNAVAILABLE", lambda: CardService(old, account_id="1001").list_cards())
        conn = sqlite3.connect(old)
        try:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM sqlite_master WHERE name='cards'").fetchone()[0], 0)
        finally:
            conn.close()

    def test_original_funds_analysis_hashes_and_daily_policy_unchanged(self):
        before = self.original_tables()
        analysis = AnalysisService(self.path, account_id="1001")
        summary = analysis.analyze_month(DATASET_ID, "2026-09")["summary"]
        self.apply_in_test(self.command())
        self.service.card_for_consumption("oct20-a")
        self.apply_in_test(self.command(operation="temporary_unfreeze", expected_version=1, action_id="unfreeze"))
        self.assertEqual(self.original_tables(), before)
        self.assertEqual(analysis.analyze_month(DATASET_ID, "2026-09")["summary"], summary)
        self.assertEqual(summary["amount"], "2140.49")
        self.assertEqual(analysis.analyze_range(DATASET_ID, "2026-10-01", "2026-10-21")["summary"]["amount"], "2737.01")
        self.assertEqual(outgoing_cents(self.conn, "1001", business_day(datetime(2026, 10, 20, tzinfo=timezone.utc))), 0)
        self.assertEqual(RiskEngine().evaluate("transfer_money", "500")["risk_level"], "yellow")
        self.assertEqual(RiskEngine().evaluate("transfer_money", "600", daily_outgoing="600")["risk_level"], "red")

    def test_existing_transfer_history_and_audit_remain_intact(self):
        self.conn.execute("UPDATE accounts SET balance_cents=balance_cents-60000 WHERE account_id='1001'")
        self.conn.execute("UPDATE accounts SET balance_cents=balance_cents+60000 WHERE account_id='1002'")
        self.conn.executemany("INSERT INTO bills(account_id,kind,amount_cents,counterparty_id,transfer_id,created_at) VALUES(?,?,?,?,?,?)", [
            ("1001", "transfer_out", 60000, "1002", "isolated-history", "2026-10-20T04:00:00.000Z"),
            ("1002", "transfer_in", 60000, "1001", "isolated-history", "2026-10-20T04:00:00.000Z"),
        ])
        self.conn.execute("INSERT INTO audit_logs(request_id,event_type,account_id,user_request,confirmation_status,execution_result) VALUES(?,?,?,?,?,?)", ("isolated-history", "tool_executed", "1001", "fixture", "confirmed", "success"))
        self.conn.commit()
        before = self.original_tables()
        self.apply_in_test(self.command())
        self.assertEqual(self.original_tables(), before)
        self.assertEqual(outgoing_cents(self.conn, "1001", business_day(datetime(2026, 10, 20, tzinfo=timezone.utc))), 60000)

    def test_card_state_changes_never_migrate_historical_consumption_link(self):
        before = self.service.card_for_consumption("sep01")
        self.apply_in_test(self.command())
        after = self.service.card_for_consumption("sep01")
        self.assertEqual(before["card"]["card_id"], after["card"]["card_id"])
        self.assertEqual(before["payment_instrument_ref"], after["payment_instrument_ref"])
        self.assertTrue(after["card"]["temporary_frozen"])

    def test_ordinary_dispatcher_has_no_card_write_registration(self):
        from agent.llm_agent import ARGUMENT_MODELS, execute_tool
        self.assertNotIn("temporary_freeze", ARGUMENT_MODELS)
        self.assertEqual(execute_tool("temporary_freeze", '{}', "1001")["code"], "UNKNOWN_TOOL")
        self.assertEqual(self.state()["version"], 0)

    def test_initializer_refuses_protected_formal_database(self):
        from database.db import PROJECT_DIR
        with patch("bill_analysis.sample.sqlite3.connect", side_effect=AssertionError("must not open")) as connect:
            self.assert_code("UNSAFE_TARGET", lambda: initialize_card_demo(PROJECT_DIR / "database/banking.db"))
            self.assertEqual(connect.call_count, 0)

    def test_invalid_command_and_query_identifiers(self):
        self.assert_code("INVALID_ARGUMENTS", lambda: apply_temporary_action(self.path, {"card_id": CARD_A}))
        self.assert_code("INVALID_ARGUMENTS", lambda: self.service.get_card("x' OR 1=1--"))
        self.assert_code("INVALID_ARGUMENTS", lambda: self.apply_in_test(self.command(expected_version=True)))
        self.assert_code("UNSUPPORTED_OPERATION", lambda: self.apply_in_test(self.command(operation="set_status")))


if __name__ == "__main__":
    unittest.main()
