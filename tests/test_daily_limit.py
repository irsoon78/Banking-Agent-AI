"""日累计风控：同步事务/资金 spy 回归与需本机运行的模拟模型 API 回归。"""

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime, timezone
from decimal import Decimal
import sqlite3
import subprocess
import sys
from threading import Barrier, Event
import unittest
from unittest.mock import patch
from uuid import uuid4

from api.responses import action_response_fields
from database.db import connection, init_db
from main import app, execute_saved_action
from security import AuditLogger, PendingActionManager, RiskEngine
from security.daily_limit import assess_operation, business_day, outgoing_cents
from security.transfer_grant import confirmed_transfer_scope
from tests.test_banking import DatabaseFixture, completion
from tools.account import get_balance
from tools.transfer import transfer_money, _write_transfer


NOW = datetime(2026, 10, 20, 4, tzinfo=timezone.utc)


class DailyPolicyTests(unittest.TestCase):
    def test_cumulative_threshold_and_invalid_totals(self):
        engine = RiskEngine()
        for total, amount, expected in ((0, "500", "yellow"), (0, "5000", "red"), ("500", "500", "yellow"),
                                        ("500", "500.01", "red"), ("600", "600", "red")):
            self.assertEqual(engine.evaluate("transfer", amount, daily_outgoing=total)["risk_level"], expected)
        for total in (None, True, -1, "bad", Decimal("NaN"), float("inf")):
            self.assertEqual(engine.evaluate("transfer", "500", daily_outgoing=total)["risk_level"], "red")

    def test_beijing_day_is_half_open_utc_interval(self):
        before = business_day(datetime(2026, 10, 19, 15, 59, 59, 999000, tzinfo=timezone.utc))
        after = business_day(datetime(2026, 10, 19, 16, tzinfo=timezone.utc))
        self.assertEqual(before["business_date"], "2026-10-19")
        self.assertEqual(after["business_date"], "2026-10-20")
        self.assertEqual(after["start_utc"], "2026-10-19T16:00:00.000Z")
        self.assertEqual(after["end_utc"], "2026-10-20T16:00:00.000Z")
        self.assertEqual(before["end_utc"], after["start_utc"])
        self.assertEqual(after["time_zone"], "Asia/Shanghai")

    def test_naive_or_invalid_clock_is_rejected(self):
        for value in (datetime(2026, 10, 20), "today", False):
            with self.assertRaises(ValueError):
                business_day(value)


class DailyTransactionTests(DatabaseFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.clock = patch("security.daily_limit.utc_now", return_value=NOW)
        self.clock.start()
        self.addCleanup(self.clock.stop)
        self.logger = AuditLogger()
        self.manager = self.new_manager()

    def new_manager(self, logger=True):
        return PendingActionManager(execute_saved_action, audit_logger=self.logger if logger else None,
                                    risk_evaluator=assess_operation)

    def prepare(self, amount="500", account="1001", manager=None):
        return (manager or self.manager).prepare_action(account, "transfer_money", {
            "to_account_id": "1002" if account == "1001" else "1001", "amount": amount})

    def confirm(self, pending, account="1001", manager=None):
        return (manager or self.manager).confirm_action(account, pending["action_id"])

    def total(self, account="1001", now=NOW):
        with connection() as conn:
            return outgoing_cents(conn, account, business_day(now))

    def seed(self, cents, stamp="2026-10-20T03:00:00.000Z", account="1001"):
        recipient = "1002" if account == "1001" else "1001"
        with connection() as conn:
            conn.execute("UPDATE accounts SET balance_cents=balance_cents-? WHERE account_id=?", (cents, account))
            conn.execute("UPDATE accounts SET balance_cents=balance_cents+? WHERE account_id=?", (cents, recipient))
            token = uuid4().hex
            conn.executemany("INSERT INTO bills(account_id,kind,amount_cents,counterparty_id,transfer_id,created_at) VALUES(?,?,?,?,?,?)",
                             [(account, "transfer_out", cents, recipient, token, stamp),
                              (recipient, "transfer_in", cents, account, token, stamp)])

    def test_first_500_pending_and_first_5000_red_never_write_funds(self):
        with patch("tools.transfer._write_transfer", wraps=_write_transfer) as funds:
            yellow, red = self.prepare(), self.prepare("5000")
            self.assertEqual(yellow["status"], "pending")
            self.assertEqual(red["status"], "require_verification")
            self.assertNotIn("action_id", red)
            self.assertEqual(funds.call_count, 0)
        self.assertEqual(self.total(), 0)

    def test_exact_1000_and_1000_point_01(self):
        self.seed(50000)
        self.assertEqual(self.prepare("500")["risk_level"], "yellow")
        self.assertEqual(self.prepare("500.01")["risk_level"], "red")
        self.assertEqual(self.total(), 50000)

    def test_500_plus_500_plus_500_third_is_blocked(self):
        with patch("tools.transfer._write_transfer", wraps=_write_transfer) as funds:
            for _ in range(2):
                self.assertEqual(self.confirm(self.prepare())["status"], "completed")
            third = self.prepare()
            self.assertEqual(third["status"], "require_verification")
            self.assertNotIn("action_id", third)
            self.assertEqual(funds.call_count, 2)
        self.assertEqual(self.total(), 100000)

    def test_all_tasks_prepared_early_still_cannot_split_limit(self):
        tasks = [self.prepare() for _ in range(3)]
        with patch("tools.transfer._write_transfer", wraps=_write_transfer) as funds:
            self.assertEqual([self.confirm(task)["status"] for task in tasks], ["completed", "completed", "require_verification"])
            self.assertEqual(funds.call_count, 2)
        self.assertEqual(self.total(), 100000)

    def test_two_600_tasks_second_confirmation_upgrades_risk_and_audit(self):
        first, second = self.prepare("600"), self.prepare("600")
        self.assertEqual(self.confirm(first)["status"], "completed")
        with patch("tools.transfer._write_transfer", wraps=_write_transfer) as funds:
            response = self.confirm(second)
            self.assertEqual(funds.call_count, 0)
        self.assertEqual(response["status"], "require_verification")
        self.assertEqual(response["decision"]["risk_level"], "red")
        self.assertEqual(response["decision"]["permission_action"], "require_verification")
        self.assertEqual(response["result"]["daily_limit"]["executed_cents"], 60000)
        self.assertEqual(response["result"]["daily_limit"]["projected_cents"], 120000)
        self.assertNotIn("transfer_id", response["result"])
        self.assertIsNone(self.manager.get_pending_action("1001", second["action_id"]))
        adapted = action_response_fields(response)
        self.assertEqual(adapted["error_code"], "VERIFICATION_REQUIRED")
        rows = self.logger.query("1001", request_id=response["request_id"])
        final_risk = next(row for row in rows if row["event_type"] == "risk_evaluated" and row["details"].get("stage") == "confirmation")
        self.assertEqual(final_risk["risk_level"], "red")
        final_permission = next(row for row in rows if row["event_type"] == "permission_decided" and row["details"].get("stage") == "confirmation")
        self.assertEqual(final_permission["permission_action"], "require_verification")
        outcome = next(row for row in rows if row["event_type"] == "tool_executed")
        self.assertEqual(outcome["execution_result"], "not_executed")
        self.assertEqual(outcome["risk_level"], "red")
        self.assertEqual(self.total(), 60000)

    def test_concurrent_same_account_confirmations_use_fresh_locked_total(self):
        tasks = [self.prepare("600") for _ in range(2)]
        barrier = Barrier(2)
        def executor(*args):
            barrier.wait(timeout=5)
            return execute_saved_action(*args)
        self.manager._executor = executor
        with patch("tools.transfer._write_transfer", wraps=_write_transfer) as funds, ThreadPoolExecutor(max_workers=2) as pool:
            responses = list(pool.map(self.confirm, tasks))
            self.assertEqual(sorted(item["status"] for item in responses), ["completed", "require_verification"])
            self.assertEqual(funds.call_count, 1)
        self.assertEqual(self.total(), 60000)

    def test_pending_cancel_and_failed_transfer_do_not_count(self):
        task = self.prepare()
        self.assertEqual(self.total(), 0)
        self.manager.cancel_action("1001", task["action_id"])
        self.assertEqual(self.total(), 0)
        pending = self.manager.prepare_action("1001", "transfer_money", {"to_account_id": "missing", "amount": "500"})
        response = self.confirm(pending)
        self.assertEqual(response["status"], "failed")
        self.assertEqual(response["result"]["code"], "ACCOUNT_NOT_FOUND")
        self.assertEqual(self.total(), 0)

    def test_insufficient_balance_does_not_count(self):
        with connection() as conn:
            conn.execute("UPDATE accounts SET balance_cents=100 WHERE account_id='1001'")
        response = self.confirm(self.prepare())
        self.assertEqual(response["result"]["code"], "INSUFFICIENT_FUNDS")
        self.assertEqual(self.total(), 0)

    def test_sql_failure_rolls_back_balances_bills_and_decision_transaction(self):
        task = self.prepare()
        with connection() as conn:
            conn.execute("CREATE TRIGGER fail_incoming BEFORE INSERT ON bills WHEN NEW.kind='transfer_in' BEGIN SELECT RAISE(ABORT,'test'); END")
        response = self.confirm(task)
        self.assertEqual(response["result"]["code"], "DATABASE_ERROR")
        self.assertEqual(self.total(), 0)
        self.assertEqual(get_balance("1001")["balance"], "10000.00")
        self.assertEqual(get_balance("1002")["balance"], "5000.00")
        self.assertEqual(self.confirm(task)["status"], "not_found")

    def test_post_commit_audit_failure_keeps_amount_without_repeat(self):
        task = self.prepare()
        with connection() as conn:
            conn.execute("CREATE TRIGGER fail_audit AFTER INSERT ON audit_logs WHEN NEW.event_type='tool_executed' BEGIN SELECT RAISE(ABORT,'test'); END")
        with self.assertLogs("security.audit", level="ERROR"), patch("tools.transfer._write_transfer", wraps=_write_transfer) as funds:
            response = self.confirm(task)
            self.assertEqual(response["status"], "completed")
            self.assertIn("audit_warning", response)
            self.assertEqual(self.confirm(task)["status"], "not_found")
            self.assertEqual(funds.call_count, 1)
        self.assertEqual(self.total(), 50000)
        self.assertEqual(self.prepare("500.01")["risk_level"], "red")

    def test_post_commit_response_failure_is_unknown_but_ledger_counts(self):
        task = self.prepare()
        def fail_response(*args):
            execute_saved_action(*args)
            raise RuntimeError("simulated response loss")
        self.manager._executor = fail_response
        with patch("tools.transfer._write_transfer", wraps=_write_transfer) as funds:
            self.assertEqual(self.confirm(task)["status"], "execution_unknown")
            self.assertEqual(self.confirm(task)["status"], "not_found")
            self.assertEqual(funds.call_count, 1)
        self.assertEqual(self.total(), 50000)

    def test_confirmation_rechecks_new_business_day(self):
        previous = datetime(2026, 10, 19, 15, 59, tzinfo=timezone.utc)
        with patch("security.daily_limit.utc_now", return_value=previous):
            task = self.prepare("600")
        self.seed(60000)  # 新业务日已发生的另一笔。
        response = self.confirm(task)
        self.assertEqual(response["status"], "require_verification")
        self.assertEqual(response["result"]["daily_limit"]["business_date"], "2026-10-20")
        self.assertEqual(self.total(), 60000)

    def test_previous_day_does_not_count_and_both_bills_use_same_execution_time(self):
        self.seed(50000, "2026-10-19T15:59:59.999Z")
        response = self.confirm(self.prepare())
        self.assertEqual(response["status"], "completed")
        with connection() as conn:
            rows = conn.execute("SELECT created_at FROM bills WHERE transfer_id=?", (response["result"]["transfer_id"],)).fetchall()
        self.assertEqual([row[0] for row in rows], ["2026-10-20T04:00:00.000Z"] * 2)
        self.assertEqual(self.total(), 50000)

    def test_lower_boundary_included_upper_excluded(self):
        for stamp in ("2026-10-19T15:59:59.999Z", "2026-10-19T16:00:00.000Z", "2026-10-20T15:59:59.999Z", "2026-10-20T16:00:00.000Z"):
            self.seed(100, stamp)
        self.assertEqual(self.total(), 200)

    def test_clock_is_read_after_waiting_for_write_lock(self):
        manager = self.new_manager(logger=False)
        previous = datetime(2026, 10, 19, 15, 59, 59, tzinfo=timezone.utc)
        current = [previous]
        attempting = Event()
        @contextmanager
        def traced_connection():
            with connection() as conn:
                conn.set_trace_callback(lambda sql: attempting.set() if sql == "BEGIN IMMEDIATE" else None)
                yield conn
        with patch("security.daily_limit.utc_now", side_effect=lambda: current[0]):
            task = self.prepare(manager=manager)
            with ThreadPoolExecutor(max_workers=1) as pool:
                with connection() as holder:
                    holder.execute("BEGIN IMMEDIATE")
                    with patch("tools.transfer.connection", traced_connection):
                        future = pool.submit(self.confirm, task, "1001", manager)
                        self.assertTrue(attempting.wait(3))
                        current[0] = NOW
                response = future.result(timeout=5)
        self.assertEqual(response["status"], "completed")
        self.assertEqual(response["result"]["daily_limit"]["business_date"], "2026-10-20")

    def test_more_than_100_rows_are_all_counted(self):
        for _ in range(101):
            self.seed(1)
        self.assertEqual(self.total(), 101)
        self.assertEqual(self.prepare("999")["risk_level"], "red")

    def test_account_isolation_incoming_and_opening_are_not_outgoing(self):
        self.seed(60000)
        self.assertEqual(self.total("1002"), 0)
        self.assertEqual(self.prepare("600", account="1002")["risk_level"], "yellow")
        self.assertEqual(self.prepare("600")["risk_level"], "red")

    def test_restart_and_repeated_confirmation(self):
        task = self.prepare()
        with patch("tools.transfer._write_transfer", wraps=_write_transfer) as funds:
            self.assertEqual(self.confirm(task)["status"], "completed")
            self.assertEqual(self.confirm(task)["status"], "not_found")
            self.assertEqual(funds.call_count, 1)
        init_db()
        restarted = self.new_manager()
        self.assertEqual(self.prepare("500.01", manager=restarted)["risk_level"], "red")
        self.assertEqual(self.total(), 50000)
        code = ("from datetime import datetime; import security.daily_limit as daily; "
                f"daily.utc_now=lambda:datetime.fromisoformat({NOW.isoformat()!r}); "
                "print(daily.assess_operation('1001','transfer_money',{'amount':'500.01'})['risk_level'])")
        result = subprocess.run([sys.executable, "-B", "-c", code], capture_output=True, text=True, check=True, timeout=15)
        self.assertEqual(result.stdout.strip(), "red")

    def test_query_clock_and_transaction_errors_block_without_funds(self):
        for target, error in (("security.daily_limit.outgoing_cents", sqlite3.OperationalError("test")),
                              ("security.daily_limit.utc_now", ValueError("test")),
                              ("tools.transfer.connection", sqlite3.OperationalError("test"))):
            task = self.prepare()
            with self.subTest(target=target), patch(target, side_effect=error), patch("tools.transfer._write_transfer", wraps=_write_transfer) as funds:
                response = self.confirm(task)
                self.assertEqual(response["status"], "failed")
                self.assertEqual(response["decision"]["risk_level"], "red")
                self.assertEqual(funds.call_count, 0)
            self.assertEqual(self.total(), 0)
        with patch("security.daily_limit.outgoing_cents", side_effect=sqlite3.OperationalError("test")):
            self.assertEqual(self.prepare()["status"], "require_verification")

    def test_malformed_ledger_is_not_silently_zero(self):
        self.seed(100)
        with connection() as conn:
            conn.execute("UPDATE bills SET created_at='invalid' WHERE kind='transfer_out'")
        self.assertEqual(self.prepare()["status"], "require_verification")

    def test_confirmation_decision_audit_uses_the_funds_connection(self):
        task = self.prepare()
        original = self.logger.log_event
        seen = []
        def checked(event, context, **kwargs):
            if event in {"risk_evaluated", "permission_decided"} and kwargs.get("details", {}).get("stage") == "confirmation":
                conn = kwargs.get("db_connection")
                self.assertIsNotNone(conn)
                self.assertTrue(conn.in_transaction)
                seen.append(conn)
            return original(event, context, **kwargs)
        with patch.object(self.logger, "log_event", side_effect=checked):
            self.assertEqual(self.confirm(task)["status"], "completed")
        self.assertEqual(len(seen), 2)
        self.assertIs(seen[0], seen[1])

    def test_transaction_audit_failure_rolls_back_before_funds(self):
        task = self.prepare()
        with connection() as conn:
            conn.execute("CREATE TRIGGER fail_recheck BEFORE INSERT ON audit_logs WHEN NEW.event_type='risk_evaluated' AND json_extract(NEW.details,'$.stage')='confirmation' BEGIN SELECT RAISE(ABORT,'test'); END")
        with patch("tools.transfer._write_transfer", wraps=_write_transfer) as funds:
            response = self.confirm(task)
            self.assertEqual(response["status"], "failed")
            self.assertEqual(funds.call_count, 0)
        self.assertEqual(self.total(), 0)

    def test_raw_tool_and_dispatcher_cannot_bypass_confirmation(self):
        from agent.llm_agent import execute_tool
        with patch("tools.transfer._write_transfer", wraps=_write_transfer) as funds:
            self.assertEqual(transfer_money("1001", "1002", "500")["code"], "SECURE_ENTRYPOINT_REQUIRED")
            self.assertEqual(execute_saved_action("1001", "transfer", {"to_account_id": "1002", "amount": "500"})["code"], "SECURE_ENTRYPOINT_REQUIRED")
            self.assertEqual(execute_tool("transfer_money", '{"to_account_id":"1002","amount":"500"}', "1001")["code"], "SECURE_ENTRYPOINT_REQUIRED")
            self.assertEqual(funds.call_count, 0)

    def test_one_time_grant_is_bound_to_exact_parameters(self):
        with confirmed_transfer_scope("1001", {"to_account_id": "1002", "amount": "500"}):
            self.assertFalse(transfer_money("1001", "1002", "600")["ok"])
            self.assertTrue(transfer_money("1001", "1002", "500")["ok"])
            self.assertEqual(transfer_money("1001", "1002", "500")["code"], "SECURE_ENTRYPOINT_REQUIRED")
        self.assertEqual(self.total(), 50000)


class DailyAPITests(DatabaseFixture, unittest.TestCase):
    # 不使用上一轮禁止全部资金执行的 sdk_client；本文件只操作自己的临时数据库。
    @contextmanager
    def sdk_client(self, responses):
        from tests.test_conversation import ConversationAPITests
        with patch("security.daily_limit.utc_now", return_value=NOW), ConversationAPITests.sdk_client(self, responses) as value:
            yield value

    def test_confirm_returns_latest_red_decision_and_no_receipt(self):
        with self.sdk_client([completion(), completion()]) as (client, requests):
            tasks = [client.post("/chat/secure", json={"message": "给账户1002转600元"}).json() for _ in range(2)]
            self.assertTrue(all(task["status"] == "pending" for task in tasks))
            self.assertEqual(client.post("/confirm", json={"action_id": tasks[0]["action_id"]}).json()["status"], "completed")
            with patch("tools.transfer._write_transfer", wraps=_write_transfer) as funds:
                response = client.post("/confirm", json={"action_id": tasks[1]["action_id"]}).json()
                self.assertEqual(funds.call_count, 0)
            self.assertEqual(response["status"], "require_verification")
            self.assertEqual(response["decision"]["risk_level"], "red")
            self.assertEqual(response["error_code"], "VERIFICATION_REQUIRED")
            self.assertNotIn("transfer_id", response["execution_result"])
            self.assertEqual(client.post("/confirm", json={"action_id": tasks[1]["action_id"]}).status_code, 404)

    def test_client_cannot_supply_daily_totals_or_execution_grant(self):
        for extra in ({"daily_outgoing": 0}, {"confirmed": True}, {"transfer_grant": True}):
            with self.sdk_client([]) as (client, requests):
                response = client.post("/chat/secure", json={"message": "给1002转500元", **extra})
                self.assertEqual(response.status_code, 422)
                self.assertEqual(requests, [])
