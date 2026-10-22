"""隔离卡片安全链；同步Agent夹具不需要事件循环，真实HTTP测试留给本机。"""

from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import replace
from functools import partial
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from pydantic import ValidationError
from openai.types.chat import ChatCompletion
from openai import APITimeoutError
import httpx

from agent.card_intents import resolve_card_request
from agent.llm_agent import AgentError, AgentPause, BankingAgent, execute_read_only_tool, execute_tool
from agent.sessions import SessionStore
from card_management.authorization import card_request_scope
from card_management.sample import CARD_A, CARD_B, CARD_SPARE, CARD_LOST, card_samples, initialize_card_demo
from card_management.service import CardService
from card_management.storage import CardError, import_card_samples
from main import (ActionRequest, ChatRequest, ConfirmRequest, SecureChatResponse, app, audited_execute_tool, cards, card_detail,
                  spending_record_card, card_operation, execute_saved_action, run_audited_chat,
                  run_audited_confirmation, secure_execute_tool)
from api.responses import APIError, ActionResponse
from security import AuditLogger, PendingActionManager, RiskEngine
from security.audit import AuditError
from security.daily_limit import assess_operation
from tests.test_banking import completion

CARD_LIST_QUERIES = (
    "帮我查询一下我名下有哪些银行卡，以及它们目前的状态。",
    "我有哪些银行卡？",
    "查看我的银行卡列表。",
    "看看我所有卡片现在是什么状态。",
)


class CardListIntentTests(unittest.TestCase):
    def test_natural_list_queries_do_not_require_a_card_id(self):
        for message in CARD_LIST_QUERIES:
            with self.subTest(message=message):
                result = resolve_card_request(message, "1001")
                self.assertEqual(result.tool, "list_cards")
                self.assertEqual(result.arguments, {})

    def test_single_and_multiple_explicit_card_targets_keep_their_rules(self):
        result = resolve_card_request(f"查看卡片{CARD_A}状态", "1001")
        self.assertEqual(result.tool, "get_card")
        self.assertEqual(result.arguments, {"card_id": CARD_A})
        self.assertIsNone(resolve_card_request(f"查看卡片{CARD_A}以及{CARD_SPARE}状态？", "1001").tool)

    def test_read_punctuation_cannot_whitelist_negation_hypotheses_or_writes(self):
        for message in ("冻结我的银行卡", "帮我挂失银行卡", "不要冻结银行卡", "如果冻结银行卡会怎样",
                        "不要查询我的银行卡列表？", "如果查询我的银行卡列表会怎样？",
                        f"查看我的银行卡列表，以及临时冻结{CARD_A}？", f"临时冻结{CARD_A}？"):
            with self.subTest(message=message):
                self.assertIsNone(resolve_card_request(message, "1001").tool)


class CardFixture:
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "independent.db"
        with patch.dict(os.environ, {"BANK_DB_PATH": str(Path(self.temp.name) / "protected.db")}):
            initialize_card_demo(self.path)
        env = patch.dict(os.environ, {"BANK_DB_PATH": str(self.path)})
        env.start(); self.addCleanup(env.stop)
        self.logger = AuditLogger()
        self.clock = 100.0
        self.manager = PendingActionManager(execute_saved_action, self.logger, risk_evaluator=assess_operation, clock=lambda: self.clock)
        self.sessions = SessionStore()
        with self.sessions.checkout("1001") as (session, _):
            self.cid = session.conversation_id
        self.service = CardService(self.path, account_id="1001")
        self.state = SimpleNamespace(pending_actions=self.manager, sessions=self.sessions, audit_logger=self.logger)
        self.request = SimpleNamespace(app=SimpleNamespace(state=self.state), url=SimpleNamespace(path="/test/cards"))

    def prepare(self, action="temporarily_freeze_card", card_id=CARD_A, cid=None, account="1001"):
        with card_request_scope(cid or self.cid):
            return self.manager.prepare_action(account, action, {"card_id": card_id})

    def confirm(self, pending, *, cid=None, confirmed=True):
        body = ConfirmRequest(account_id="1001", action_id=pending["action_id"], conversation_id=cid or self.cid, confirmed=confirmed)
        result = run_audited_confirmation(body, self.request, confirmed)
        ActionResponse.model_validate(result)
        return result

    def rows(self, table):
        with closing(sqlite3.connect(self.path)) as conn:
            return conn.execute("SELECT * FROM " + table + " ORDER BY 1").fetchall()

    def frozen(self, card=CARD_A):
        return self.service.get_card(card)["card"]["temporary_frozen"]

    def logs(self, action_id):
        return [r for r in self.logger.query("1001", 500) if r["action_id"] == action_id]


class CardIntegrationTests(CardFixture, unittest.TestCase):
    def test_cold_application_and_storage_imports_are_order_independent(self):
        before = self.path.read_bytes()
        for source in ("import main; main.app.openapi()", "import bill_analysis.storage; import card_management.service; import security"):
            child = subprocess.run([sys.executable, "-B", "-c", source], capture_output=True, text=True, timeout=30)
            self.assertEqual(child.returncode, 0, child.stderr)
        self.assertEqual(self.path.read_bytes(), before)

    def test_green_api_queries_have_real_results_and_audit(self):
        result = cards("1001", self.request)
        self.assertEqual(len(result["cards"]), 3)
        self.assertEqual(result["decisions"][0]["risk_level"], "green")
        self.assertEqual(card_detail("1001", CARD_A, self.request)["card"]["version"], 0)
        self.assertEqual(spending_record_card("1001", "oct20-a", self.request)["card"]["card_id"], CARD_A)
        self.assertEqual(result["audit_id"], result["request_id"])

    def test_freeze_then_unfreeze_both_require_confirmation(self):
        pending = self.prepare()
        self.assertEqual(pending["risk_level"], "yellow")
        self.assertEqual(pending["permission_action"], "pending_confirmation")
        self.assertEqual(pending["pending_action"]["params"], {"card_id": CARD_A, "expected_version": 0})
        self.assertFalse(self.frozen())
        result = self.confirm(pending)
        self.assertEqual(result["status"], "completed")
        self.assertTrue(self.frozen())
        self.assertNotIn("transfer_id", json.dumps(result))
        pending = self.prepare("remove_temporary_freeze")
        self.assertTrue(self.frozen())
        self.assertEqual(self.confirm(pending)["status"], "completed")
        self.assertFalse(self.frozen())
        self.assertEqual(len(self.rows("card_operations")), 2)

    def test_formal_loss_and_release_are_real_red_decisions(self):
        for action in ("report_card_loss", "cancel_card_loss_report"):
            result = self.prepare(action, CARD_LOST)
            self.assertEqual(result["status"], "require_verification")
            self.assertEqual(result["risk_level"], "red")
            self.assertEqual(result["permission_action"], "require_verification")
            self.assertNotIn("action_id", result)
            logs = [r for r in self.logger.query("1001", 500) if r["operation"] == action]
            self.assertTrue(any(r["event_type"] == "risk_evaluated" and r["risk_level"] == "red" for r in logs))
            self.assertTrue(any(r["event_type"] == "permission_decided" and r["confirmation_status"] == "verification_required" and r["execution_result"] == "not_executed" for r in logs))
        self.assertEqual(self.rows("card_operations"), [])
        self.assertEqual(self.service.get_card(CARD_LOST)["card"]["loss_status"], "reported")

    def test_cross_account_and_missing_card_are_rejected(self):
        for card in (CARD_B, "card-missing"):
            self.assertEqual(self.prepare(card_id=card)["result"]["code"], "CARD_NOT_FOUND")
        with self.assertRaises(APIError) as exc:
            card_detail("1002", CARD_A, self.request)
        self.assertEqual(exc.exception.status_code, 404)
        with self.assertRaises(APIError):
            spending_record_card("1002", "oct20-a", self.request)

    def test_missing_mapping_does_not_guess(self):
        # 新增独立测试记录引用但无映射；不改变演示样本及其校验值。
        with patch.object(CardService, "card_for_consumption", return_value={"ok": True, "linked": False, "card": None, "reason_code": "NO_MAPPING"}):
            self.assertFalse(spending_record_card("1001", "unlinked", self.request)["linked"])
        self.assertEqual(self.rows("card_operations"), [])

    def test_external_schema_cannot_set_version_account_or_authorization(self):
        for extra in ({"expected_version": 0}, {"account_id": "1002"}, {"risk_level": "yellow"}, {"authorized": True}, {"action_id": "forged"}):
            result = execute_tool("temporarily_freeze_card", json.dumps({"card_id": CARD_A, **extra}), "1001")
            self.assertEqual(result["code"], "INVALID_ARGUMENTS")
        with self.assertRaises(ValidationError):
            ConfirmRequest(action_id="fake", card_id=CARD_A)
        self.assertFalse(self.frozen())

    def test_forged_nonpermit_context_cannot_write(self):
        from card_management.authorization import _active
        token = _active.set({"authorized": True, "card_id": CARD_A})
        try:
            result = execute_saved_action("1001", "temporarily_freeze_card", {"card_id": CARD_A, "expected_version": 0})
        finally:
            _active.reset(token)
        self.assertEqual(result["code"], "AUTHORIZATION_REQUIRED")
        self.assertFalse(self.frozen())

    def test_direct_default_legacy_and_saved_executor_cannot_write(self):
        for action in ("temporarily_freeze_card", "remove_temporary_freeze", "report_card_loss", "cancel_card_loss_report"):
            raw = json.dumps({"card_id": CARD_A})
            self.assertFalse(execute_tool(action, raw, "1001")["ok"])
            self.assertFalse(execute_read_only_tool(action, raw, "1001")["ok"])
            self.assertEqual(audited_execute_tool(action, raw, "1001", self.logger)["code"], "LEGACY_WRITE_DISABLED")
        self.assertEqual(execute_saved_action("1001", "temporarily_freeze_card", {"card_id": CARD_A, "expected_version": 0})["code"], "AUTHORIZATION_REQUIRED")
        self.assertEqual(self.rows("card_operations"), [])

    def test_wrong_or_missing_session_does_not_consume_task(self):
        pending = self.prepare()
        for cid in (None, "a" * 32):
            body = ConfirmRequest(action_id=pending["action_id"], conversation_id=cid)
            result = run_audited_confirmation(body, self.request, True)
            self.assertEqual(result["error_code"], "SESSION_MISMATCH")
        self.assertIsNotNone(self.manager.get_pending_action("1001", pending["action_id"]))
        self.assertEqual(self.confirm(pending)["status"], "completed")

    def test_expired_session_cannot_authorize_even_known_task(self):
        pending = self.prepare()
        self.sessions._sessions.clear()
        with self.assertRaises(APIError) as exc:
            self.confirm(pending)
        self.assertEqual(exc.exception.error_code, "SESSION_NOT_FOUND")
        self.assertFalse(self.frozen())

    def test_cancel_then_repeat_confirm_cannot_execute(self):
        pending = self.prepare()
        self.assertEqual(self.confirm(pending, confirmed=False)["status"], "cancelled")
        with self.assertRaises(APIError):
            self.confirm(pending)
        self.assertFalse(self.frozen())

    def test_task_timeout_is_consumed_without_execution(self):
        pending = self.prepare()
        self.clock += 301
        self.assertEqual(self.confirm(pending)["error_code"], "ACTION_EXPIRED")
        self.assertIsNone(self.manager.get_pending_action("1001", pending["action_id"]))
        self.assertFalse(self.frozen())

    def test_repeated_and_parallel_confirm_execute_only_once(self):
        pending = self.prepare()
        def confirm(_):
            return self.manager.confirm_action("1001", pending["action_id"], conversation_id=self.cid)["status"]
        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertCountEqual(list(pool.map(confirm, range(2))), ["completed", "not_found"])
        self.assertEqual(len(self.rows("card_operations")), 1)

    def test_two_sessions_same_card_version_conflict(self):
        first = self.prepare()
        with self.sessions.checkout("1001") as (other, _):
            cid = other.conversation_id
        second = self.prepare(cid=cid)
        self.assertEqual(self.confirm(first)["status"], "completed")
        self.assertEqual(self.confirm(second, cid=cid)["error_code"], "VERSION_CONFLICT")
        self.assertEqual(len(self.rows("card_operations")), 1)

    def test_two_parallel_tasks_same_card_cannot_both_execute(self):
        a = self.prepare()
        b = self.prepare(cid="b" * 32)
        def confirm(item):
            pending, cid = item
            return self.manager.confirm_action("1001", pending["action_id"], conversation_id=cid)["status"]
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(confirm, [(a, self.cid), (b, "b" * 32)]))
        self.assertCountEqual(results, ["completed", "failed"])
        self.assertEqual(len(self.rows("card_operations")), 1)

    def test_existing_transfer_or_card_pending_cannot_be_replaced(self):
        with card_request_scope(self.cid):
            transfer = self.manager.prepare_action("1001", "transfer_money", {"to_account_id": "1002", "amount": "500"})
        self.assertEqual(self.prepare()["result"]["code"], "PENDING_EXISTS")
        self.assertEqual(self.manager.get_pending_action("1001", transfer["action_id"])["params"]["amount"], "500")
        self.manager.cancel_action("1001", transfer["action_id"])
        card = self.prepare()
        with card_request_scope(self.cid):
            second = self.manager.prepare_action("1001", "transfer_money", {"to_account_id": "1002", "amount": "500"})
        self.assertEqual(second["result"]["code"], "PENDING_EXISTS")
        self.assertIsNotNone(self.manager.get_pending_action("1001", card["action_id"]))

    def test_receipt_and_transaction_audit_are_linked(self):
        pending = self.prepare()
        result = self.confirm(pending)
        receipt = card_operation("1001", pending["action_id"], self.request)["card_operation"]
        self.assertEqual(receipt["operation_id"], result["result"]["card_operation"]["operation_id"])
        self.assertEqual(receipt["request_id"], result["request_id"])
        logs = self.logs(pending["action_id"])
        self.assertTrue(any(r["event_type"] == "risk_evaluated" and r["details"].get("stage") == "card_confirmation" for r in logs))
        committed = next(r for r in logs if r["event_type"] == "card_operation_committed")
        self.assertEqual(committed["details"]["operation_id"], receipt["operation_id"])
        self.assertEqual(committed["execution_result"], "success")

    def test_transaction_audit_failure_rolls_back_state_and_receipt(self):
        pending = self.prepare()
        real = self.logger.log_event
        def fail(event, *args, **kwargs):
            if event == "card_operation_committed":
                raise AuditError("fixture")
            return real(event, *args, **kwargs)
        with patch.object(self.logger, "log_event", side_effect=fail):
            result = self.confirm(pending)
        self.assertEqual(result["status"], "failed")
        self.assertFalse(self.frozen())
        self.assertEqual(self.rows("card_operations"), [])
        self.assertFalse(any(r["details"].get("stage") == "card_confirmation" for r in self.logs(pending["action_id"])))

    def test_pre_gate_audit_failure_does_not_claim_task(self):
        pending = self.prepare()
        with patch.object(self.logger, "log_event", side_effect=AuditError("fixture")):
            with self.assertRaises(AuditError):
                self.manager.confirm_action("1001", pending["action_id"], conversation_id=self.cid)
        self.assertIsNotNone(self.manager.get_pending_action("1001", pending["action_id"]))
        self.assertFalse(self.frozen())

    def test_commit_success_then_error_is_unknown_and_readable_without_retry(self):
        pending = self.prepare()
        connect = sqlite3.connect
        class LostResponse(sqlite3.Connection):
            def commit(self):
                super().commit()
                raise sqlite3.OperationalError("lost")
        with patch("card_management.transitions.sqlite3.connect", side_effect=lambda *a, **kw: connect(*a, **kw, factory=LostResponse)):
            result = self.confirm(pending)
        self.assertEqual(result["status"], "execution_unknown")
        self.assertTrue(self.frozen())
        self.assertEqual(card_operation("1001", pending["action_id"], self.request)["card_operation"]["result_status"], "completed")
        with self.assertRaises(APIError):
            self.confirm(pending)
        self.assertEqual(len(self.rows("card_operations")), 1)

    def test_post_execution_audit_failure_preserves_completed_result(self):
        pending = self.prepare()
        with patch.object(self.logger, "log_after_execution", return_value=False):
            result = self.confirm(pending)
        self.assertEqual(result["status"], "completed")
        self.assertIn("audit_warning", result)
        self.assertTrue(self.frozen())

    def test_latest_risk_policy_is_checked_in_transaction(self):
        pending = self.prepare()
        with patch.object(RiskEngine, "evaluate", return_value={"risk_level": "red", "reason": "fixture policy upgrade"}):
            result = self.confirm(pending)
        self.assertEqual(result["status"], "require_verification")
        self.assertEqual(result["decision"]["risk_level"], "red")
        self.assertFalse(self.frozen())
        self.assertEqual(self.rows("card_operations"), [])
        final = next(r for r in self.logs(pending["action_id"]) if r["event_type"] == "tool_executed")
        self.assertEqual((final["risk_level"], final["permission_action"], final["execution_result"]), ("red", "require_verification", "not_executed"))

    def test_no_change_to_funds_analysis_or_daily_limit(self):
        names = ("accounts", "bills", "analysis_datasets", "spending_records")
        before = {name: self.rows(name) for name in names}
        self.confirm(self.prepare())
        self.assertEqual(before, {name: self.rows(name) for name in names})
        self.assertEqual(assess_operation("1001", "transfer_money", {"amount": "500"})["risk_level"], "yellow")
        self.assertEqual(assess_operation("1001", "transfer_money", {"amount": "5000"})["risk_level"], "red")


def immediate(coroutine):
    """只运行不挂起的模拟协程，不创建loop；不是异步调度/HTTP传输测试。"""
    try:
        yielded = coroutine.send(None)
    except StopIteration as result:
        return result.value
    coroutine.close()
    raise AssertionError(f"fixture unexpectedly yielded: {type(yielded).__name__}")


class CardAgentTests(CardFixture, unittest.TestCase):
    def chat(self, message, *, tool=None, args=None, cid=None, allow=True, legacy=False, model_text="我已冻结了你的卡。", model_requests=None):
        async def create(**kwargs):
            if model_requests is not None:
                model_requests.append(kwargs)
            return ChatCompletion.model_validate(completion(tool, args or {}, model_text))
        async def inline(function, *a, **kw):
            return function(*a, **kw)
        executor = partial(audited_execute_tool, audit_logger=self.logger) if legacy else partial(secure_execute_tool, manager=self.manager, audit_logger=self.logger)
        agent = BankingAgent(SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create))), "mock", tool_executor=executor)
        with patch("agent.llm_agent.asyncio.to_thread", new=inline), card_request_scope(cid or self.cid):
            return immediate(agent.run(message, "1001", allow_transfer=allow))

    def test_natural_list_details_and_consumption_link(self):
        for message, tool, args in (("查询我的银行卡", "list_cards", {}), (f"查询卡片{CARD_A}状态", "get_card", {"card_id": CARD_A}),
                                    ("查看原始消费记录 oct20-a 对应的银行卡", "get_card_for_spending_record", {"record_id": "oct20-a"})):
            result = self.chat(message, tool=tool, args=args)
            self.assertEqual(result["status"], "completed")
            self.assertEqual(result["tool_results"][0]["tool"], tool)
            self.assertNotIn("我已冻结", result["reply"])
        self.assertEqual(self.rows("card_operations"), [])

    def test_natural_list_queries_use_green_real_tools_with_or_without_model_call(self):
        original = {name: self.rows(name) for name in ("cards", "card_operations", "accounts", "bills", "spending_records")}
        expected = self.service.list_cards()["cards"]
        with patch("card_management.transitions.apply_temporary_action") as card_write, patch("tools.transfer._write_transfer") as funds_write:
            for message in CARD_LIST_QUERIES:
                for tool in ("list_cards", None):
                    with self.subTest(message=message, model_tool=tool), self.logger.request_context("1001", message) as context:
                        result = self.chat(message, tool=tool, model_text="可以查询当前账户的银行卡。")
                        self.assertEqual(result["status"], "completed")
                        self.assertEqual(result["tool_results"][0]["tool"], "list_cards")
                        actual = result["tool_results"][0]["result"]
                        self.assertEqual(actual["account_id"], "1001")
                        self.assertEqual(actual["cards"], expected)
                        self.assertEqual(len(actual["cards"]), 3)
                        self.assertNotIn("action_id", result)
                        self.assertEqual(self.manager._pending, {})
                        events = self.logger.query("1001", request_id=context["request_id"])
                        self.assertTrue(any(e["event_type"] == "risk_evaluated" and e["operation"] == "list_cards" and e["risk_level"] == "green" for e in events))
                        self.assertTrue(any(e["event_type"] == "permission_decided" and e["permission_action"] == "allow" for e in events))
            self.assertEqual(card_write.call_count, 0)
            self.assertEqual(funds_write.call_count, 0)
        self.assertEqual(original, {name: self.rows(name) for name in original})

    def test_list_query_cannot_be_replaced_by_model_write_or_cross_account_args(self):
        for tool, args in (("temporarily_freeze_card", {"card_id": CARD_A}), ("report_card_loss", {"card_id": CARD_A}),
                           ("list_cards", {"account_id": "1002"})):
            with self.subTest(tool=tool):
                result = self.chat(CARD_LIST_QUERIES[0], tool=tool, args=args)
                self.assertIn(result["status"], {"needs_input", "failed"})
                self.assertEqual(self.manager._pending, {})
        self.assertEqual(self.rows("card_operations"), [])

    def test_ambiguous_writes_are_not_downgraded_to_green_lists(self):
        with patch("card_management.transitions.apply_temporary_action") as card_write, patch("tools.transfer._write_transfer") as funds_write:
            for message in ("冻结我的银行卡", "帮我挂失银行卡", "不要冻结银行卡", "如果冻结银行卡会怎样",
                            f"查询我的银行卡列表，以及临时冻结{CARD_A}？"):
                for tool, args in ((None, {}), ("list_cards", {}), ("temporarily_freeze_card", {"card_id": CARD_A})):
                    with self.subTest(message=message, tool=tool):
                        result = self.chat(message, tool=tool, args=args)
                        self.assertEqual(result["status"], "needs_input")
                        self.assertEqual(result["tool_results"], [])
                        self.assertEqual(self.manager._pending, {})
            self.assertEqual(card_write.call_count, 0)
            self.assertEqual(funds_write.call_count, 0)
        self.assertFalse(self.frozen())

    def test_natural_operation_result_query_uses_persistent_receipt(self):
        pending = self.prepare()
        self.confirm(pending)
        result = self.chat(f"查询卡片操作 {pending['action_id']} 结果", tool="get_card_operation", args={"action_id": pending["action_id"]})
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["tool_results"][0]["result"]["card_operation"]["action_id"], pending["action_id"])

    def test_no_tool_clear_request_uses_checked_fallback(self):
        result = self.chat(f"临时冻结{CARD_A}")
        self.assertEqual(result["status"], "pending")
        self.assertFalse(self.frozen())

    def test_natural_freeze_and_release_go_through_confirmation(self):
        pending = self.chat(f"请临时冻结卡片{CARD_A}", tool="temporarily_freeze_card", args={"card_id": CARD_A})
        self.assertEqual(pending["status"], "pending")
        self.confirm(pending)
        pending = self.chat(f"解除临时冻结{CARD_A}", tool="remove_temporary_freeze", args={"card_id": CARD_A})
        self.assertEqual(pending["status"], "pending")
        self.confirm(pending)
        self.assertFalse(self.frozen())

    def test_loss_and_release_never_substitute_temporary_operation(self):
        for phrase, tool in (("正式挂失", "report_card_loss"), ("解挂", "cancel_card_loss_report")):
            result = self.chat(phrase + CARD_A, tool=tool, args={"card_id": CARD_A})
            self.assertEqual(result["status"], "require_verification")
        mismatched = self.chat("挂失" + CARD_A, tool="temporarily_freeze_card", args={"card_id": CARD_A})
        self.assertEqual(mismatched["status"], "needs_input")
        self.assertEqual(self.rows("card_operations"), [])

    def test_ambiguous_negative_hypothetical_and_multi_target_do_not_prepare(self):
        requests = ["把我的卡冻结。", "我的卡好像被盗刷了。", "把这张卡停掉。", "帮我挂失。", "解除冻结。", "只是问问怎么冻结。", "不要冻结银行卡。",
                    f"如果临时冻结{CARD_A}会怎样", f"昨天说过临时冻结{CARD_A}", f"临时冻结{CARD_A}和{CARD_SPARE}", f"临时冻结{CARD_A}并转账500元"]
        for message in requests:
            with self.subTest(message=message):
                result = self.chat(message, tool="temporarily_freeze_card", args={"card_id": CARD_A})
                self.assertEqual(result["status"], "needs_input")
                self.assertEqual(self.manager._pending, {})
        self.assertFalse(self.frozen())

    def test_wrong_model_card_or_tool_is_rejected(self):
        self.assertEqual(self.chat(f"临时冻结{CARD_A}", tool="temporarily_freeze_card", args={"card_id": CARD_SPARE})["status"], "needs_input")
        self.assertEqual(self.chat(f"临时冻结{CARD_A}", tool="transfer_money", args={"to_account_id": "1002", "amount": "500"})["status"], "needs_input")
        self.assertEqual(self.manager._pending, {})

    def test_invalid_or_unknown_tool_does_not_fallback(self):
        self.assertEqual(self.chat(f"临时冻结{CARD_A}", tool="temporarily_freeze_card", args={"card_id": CARD_A, "authorized": True})["status"], "failed")
        self.assertEqual(self.chat(f"临时冻结{CARD_A}", tool="arbitrary", args={})["status"], "failed")
        self.assertEqual(self.manager._pending, {})

    def test_empty_model_text_cannot_prepare_card_task(self):
        result = self.chat(f"临时冻结{CARD_A}", model_text="")
        self.assertEqual(result["status"], "failed")
        self.assertEqual(self.manager._pending, {})

    def test_model_timeout_cannot_fallback_or_retry(self):
        calls = []
        async def create(**kwargs):
            calls.append(kwargs)
            raise APITimeoutError(request=httpx.Request("POST", "https://mock.invalid"))
        client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
        with self.assertRaises(AgentError):
            immediate(BankingAgent(client, "mock").run(f"临时冻结{CARD_A}", "1001"))
        self.assertEqual(len(calls), 1)
        self.assertEqual(self.manager._pending, {})

    def test_existing_pending_and_ordinary_confirmation_are_preserved(self):
        pending = self.prepare()
        self.assertEqual(self.chat(f"临时冻结{CARD_SPARE}", allow=False)["status"], "needs_input")
        for phrase in ("好的", "确认一下", "真的吗"):
            self.chat(phrase, allow=False)
            self.assertIsNotNone(self.manager.get_pending_action("1001", pending["action_id"]))
        self.assertFalse(self.frozen())

    def test_legacy_agent_cannot_prepare_card_operation(self):
        result = self.chat(f"临时冻结{CARD_A}", legacy=True)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(self.manager._pending, {})

    def test_real_chat_session_binds_pending_and_keeps_history(self):
        async def create(**kwargs):
            return ChatCompletion.model_validate(completion("temporarily_freeze_card", {"card_id": CARD_A}))
        async def inline(function, *a, **kw):
            return function(*a, **kw)
        self.state.secure_agent = BankingAgent(SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create))), "mock",
                                              partial(secure_execute_tool, manager=self.manager, audit_logger=self.logger))
        with patch("agent.llm_agent.asyncio.to_thread", new=inline):
            result = immediate(run_audited_chat(ChatRequest(message=f"临时冻结{CARD_A}", conversation_id=self.cid), self.request, True))
        SecureChatResponse.model_validate(result)
        self.assertEqual(result["conversation_id"], self.cid)
        self.assertEqual(result["pending_action"]["conversation_id"], self.cid)
        self.assertEqual(self.confirm(result)["status"], "completed")


class CardNamedTargetTests(CardFixture, unittest.TestCase):
    chat = CardAgentTests.chat
    request_text = "请帮我临时冻结模拟生活卡A，银行卡尾号0101。"

    def test_explicit_name_and_tail_are_parsed_without_inventing_id(self):
        for message in (self.request_text, "临时冻结模拟生活卡A,尾号0101", "把模拟生活卡A，银行卡尾号0101临时冻结"):
            with self.subTest(message=message):
                result = resolve_card_request(message, "1001")
                self.assertEqual(result.tool, "temporarily_freeze_card")
                self.assertEqual(result.arguments, {"display_name": "模拟生活卡A", "last_four": "0101"})

    def test_unique_name_and_tail_prepare_yellow_with_server_snapshot(self):
        before = self.rows("cards")
        with patch("card_management.transitions.apply_temporary_action") as write:
            pending = self.chat(self.request_text)
            self.assertEqual(pending["status"], "pending")
            self.assertEqual((pending["risk_level"], pending["permission_action"]), ("yellow", "pending_confirmation"))
            self.assertEqual(pending["pending_action"]["params"], {"card_id": CARD_A, "expected_version": 0})
            self.assertEqual(write.call_count, 0)
        self.assertEqual(before, self.rows("cards"))
        self.assertEqual(self.rows("card_operations"), [])

    def test_model_uses_only_server_resolved_card_id(self):
        requests = []
        pending = self.chat(self.request_text, tool="temporarily_freeze_card", args={"card_id": CARD_A}, model_requests=requests)
        self.assertEqual(pending["status"], "pending")
        self.assertTrue(any(m["role"] == "system" and "服务端唯一匹配" in m["content"] and CARD_A in m["content"] for m in requests[0]["messages"]))
        self.assertFalse(self.frozen())

    def test_cancel_named_task_never_changes_state(self):
        before = self.rows("cards")
        pending = self.chat(self.request_text)
        self.assertEqual(pending["status"], "pending")
        self.assertEqual(self.confirm(pending, confirmed=False)["status"], "cancelled")
        self.assertEqual(before, self.rows("cards"))
        self.assertEqual(self.rows("card_operations"), [])

    def test_confirm_named_task_changes_only_selected_card_with_real_receipt(self):
        names = ("accounts", "bills", "spending_records", "analysis_datasets")
        original = {name: self.rows(name) for name in names}
        others = [c for c in self.service.list_cards()["cards"] if c["card_id"] != CARD_A]
        with patch("tools.transfer._write_transfer") as funds_write:
            pending = self.chat(self.request_text)
            self.assertEqual(pending["status"], "pending")
            result = self.confirm(pending)
            self.assertEqual(result["status"], "completed")
            self.assertEqual(funds_write.call_count, 0)
        receipt = result["result"]["card_operation"]
        self.assertEqual(receipt["card_id"], CARD_A)
        self.assertTrue(self.frozen())
        self.assertEqual(receipt["action_id"], pending["action_id"])
        self.assertNotIn("transfer_id", json.dumps(result))
        self.assertEqual(others, [c for c in self.service.list_cards()["cards"] if c["card_id"] != CARD_A])
        self.assertEqual(original, {name: self.rows(name) for name in names})
        self.assertTrue(any(e["event_type"] == "card_operation_committed" for e in self.logs(pending["action_id"])))
        self.assertEqual(assess_operation("1001", "transfer_money", {"amount": "500"})["risk_level"], "yellow")
        self.assertEqual(assess_operation("1001", "transfer_money", {"amount": "5000"})["risk_level"], "red")

    def test_conflicting_partial_or_similar_names_do_not_match(self):
        for name, tail in (("模拟生活卡A", "0202"), ("模拟备用卡", "0101"), ("生活卡A", "0101"), ("模拟生活卡a", "0101"), ("模拟生活卡Ａ", "0101")):
            with self.subTest(name=name, tail=tail):
                result = self.chat(f"临时冻结{name}，银行卡尾号{tail}。", tool="temporarily_freeze_card", args={"card_id": CARD_A})
                self.assertEqual(result["status"], "needs_input")
                self.assertIn("CARD_TARGET_NOT_FOUND", result["diagnostic_codes"])
                self.assertEqual(self.manager._pending, {})
        self.assertEqual(self.rows("card_operations"), [])

    def test_duplicate_exact_matches_never_select_first(self):
        duplicate = replace(card_samples()[0][0], card_id="card-duplicate")
        with closing(sqlite3.connect(self.path)) as conn:
            import_card_samples(conn, [duplicate], [])
        result = self.chat(self.request_text, tool="temporarily_freeze_card", args={"card_id": CARD_A})
        self.assertEqual(result["status"], "needs_input")
        self.assertIn("CARD_TARGET_AMBIGUOUS", result["diagnostic_codes"])
        self.assertEqual(self.manager._pending, {})
        self.assertEqual(self.rows("card_operations"), [])

    def test_foreign_card_name_and_tail_cannot_resolve(self):
        result = self.chat("临时冻结模拟生活卡B，银行卡尾号0404。", tool="temporarily_freeze_card", args={"card_id": CARD_B})
        self.assertEqual(result["status"], "needs_input")
        self.assertIn("CARD_TARGET_NOT_FOUND", result["diagnostic_codes"])
        self.assertEqual(self.manager._pending, {})
        self.assertEqual(self.rows("card_operations"), [])

    def test_identical_identity_in_another_account_does_not_change_match(self):
        foreign = replace(card_samples()[0][0], card_id="card-foreign-copy", account_id="1002")
        with closing(sqlite3.connect(self.path)) as conn:
            import_card_samples(conn, [foreign], [])
        pending = self.chat(self.request_text)
        self.assertEqual(pending["pending_action"]["params"]["card_id"], CARD_A)
        foreign_result = CardService(self.path, account_id="1002").resolve_card_target("模拟生活卡A", "0101")
        self.assertEqual(foreign_result["card"]["card_id"], foreign.card_id)
        self.assertEqual(self.rows("card_operations"), [])

    def test_identity_lookup_requires_both_strict_display_fields(self):
        for name, tail in ((None, "0101"), ("", "0101"), ("模拟生活卡A", 101), ("模拟生活卡A", "101"),
                           ("模拟生活卡A", "０００１"), ("模拟生活卡A", "0101 OR 1=1")):
            with self.subTest(name=name, tail=tail), self.assertRaises(CardError) as caught:
                self.service.resolve_card_target(name, tail)
            self.assertEqual(caught.exception.code, "INVALID_ARGUMENTS")

    def test_missing_target_negation_hypotheses_and_multiple_commands_stay_blocked(self):
        messages = ("临时冻结我的银行卡", "临时冻结这张卡", "临时冻结模拟生活卡A", "临时冻结银行卡尾号0101",
                    "不要临时冻结模拟生活卡A，银行卡尾号0101。", "如果临时冻结模拟生活卡A，银行卡尾号0101会怎样？",
                    "昨天说过临时冻结模拟生活卡A，银行卡尾号0101。",
                    "临时冻结模拟生活卡A，银行卡尾号0101，然后解除临时冻结模拟备用卡，尾号0202。",
                    f"临时冻结{CARD_SPARE} 模拟生活卡A，银行卡尾号0101。")
        with patch("card_management.transitions.apply_temporary_action") as write:
            for message in messages:
                with self.subTest(message=message):
                    result = self.chat(message, tool="temporarily_freeze_card", args={"card_id": CARD_A})
                    self.assertEqual(result["status"], "needs_input")
                    self.assertEqual(self.manager._pending, {})
            self.assertEqual(write.call_count, 0)

    def test_lost_card_cannot_be_unfrozen_by_name(self):
        result = self.chat("解除临时冻结模拟已挂失卡，银行卡尾号0303。")
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["tool_results"][0]["result"]["code"], "CARD_REPORTED_LOST")
        self.assertEqual(self.service.get_card(CARD_LOST)["card"]["loss_status"], "reported")
        self.assertEqual(self.manager._pending, {})

    def test_formal_loss_and_release_by_name_remain_red(self):
        for verb, action in (("正式挂失", "report_card_loss"), ("解挂", "cancel_card_loss_report")):
            with self.subTest(verb=verb):
                result = self.chat(f"{verb}模拟生活卡A，银行卡尾号0101。", tool=action, args={"card_id": CARD_A})
                self.assertEqual((result["status"], result["risk_level"], result["permission_action"]), ("require_verification", "red", "require_verification"))
                self.assertNotIn("action_id", result)
                events = [e for e in self.logger.query("1001", 500) if e["operation"] == action]
                self.assertTrue(any(e["event_type"] == "permission_decided" and e["confirmation_status"] == "verification_required" and e["execution_result"] == "not_executed" for e in events))
        self.assertEqual(self.rows("card_operations"), [])
        self.assertFalse(self.frozen())

    def test_model_forgery_and_legacy_execution_cannot_bypass_checks(self):
        cases = (("temporarily_freeze_card", {"card_id": CARD_SPARE}),
                 ("temporarily_freeze_card", {"card_id": "card-invented"}),
                 ("temporarily_freeze_card", {"card_id": CARD_A, "risk_level": "green"}),
                 ("temporarily_freeze_card", {"card_id": CARD_A, "expected_version": 99}),
                 ("report_card_loss", {"card_id": CARD_A}), ("unknown", {}))
        for tool, args in cases:
            with self.subTest(tool=tool, args=args):
                self.assertIn(self.chat(self.request_text, tool=tool, args=args)["status"], {"needs_input", "failed"})
                self.assertEqual(self.manager._pending, {})
        self.assertEqual(self.chat(self.request_text, legacy=True)["status"], "failed")
        self.assertFalse(self.frozen())
        self.assertEqual(self.rows("card_operations"), [])

    def test_resolution_database_failure_does_not_guess_or_create_task(self):
        with patch.object(CardService, "resolve_card_target", side_effect=CardError("CARD_DATABASE_UNAVAILABLE", "卡片数据无法可靠读取，未生成结果。")):
            result = self.chat(self.request_text, tool="temporarily_freeze_card", args={"card_id": CARD_A})
        self.assertEqual(result["status"], "failed")
        self.assertIn("CARD_DATABASE_UNAVAILABLE", result["diagnostic_codes"])
        self.assertEqual(self.manager._pending, {})
        self.assertEqual(self.rows("card_operations"), [])

    def test_existing_pending_is_preserved_and_chat_cannot_confirm_named_task(self):
        pending = self.chat(self.request_text)
        self.assertEqual(pending["status"], "pending")
        for message in ("好的", "确认一下", "就这样吧", "临时冻结模拟备用卡，银行卡尾号0202。"):
            self.chat(message, allow=False)
            self.assertIsNotNone(self.manager.get_pending_action("1001", pending["action_id"]))
            self.assertEqual(len(self.manager._pending), 1)
            self.assertFalse(self.frozen())
        self.assertEqual(self.rows("card_operations"), [])

    def test_named_unfreeze_uses_current_server_version(self):
        self.assertEqual(self.confirm(self.prepare())["status"], "completed")
        pending = self.chat("请帮我解除临时冻结模拟生活卡A，银行卡尾号0101。")
        self.assertEqual(pending["status"], "pending")
        self.assertEqual(pending["pending_action"]["params"], {"card_id": CARD_A, "expected_version": 1})
        self.assertTrue(self.frozen())
        self.assertEqual(self.confirm(pending)["status"], "completed")
        self.assertFalse(self.frozen())


class CardHTTPTests(CardFixture, unittest.TestCase):
    """需要真实asyncio/TestClient，在本机完整回归中执行。"""
    def setUp(self):
        super().setUp()
        env = patch.dict(os.environ, {"DEEPSEEK_API_KEY": ""})
        env.start(); self.addCleanup(env.stop)
        self.client = TestClient(app)
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)
        app.state.pending_actions = self.manager
        app.state.sessions = self.sessions
        app.state.audit_logger = self.logger

    def test_http_read_endpoints_and_ownership(self):
        self.assertEqual(self.client.get("/accounts/1001/cards").status_code, 200)
        self.assertEqual(self.client.get(f"/accounts/1002/cards/{CARD_A}").status_code, 404)
        self.assertEqual(self.client.get("/accounts/1001/analysis-records/oct20-a/card").json()["card"]["card_id"], CARD_A)

    def test_http_confirmation_requires_session_and_rejects_extra_fields(self):
        pending = self.prepare()
        body = {"account_id": "1001", "action_id": pending["action_id"]}
        self.assertEqual(self.client.post("/confirm", json=body).json()["error_code"], "SESSION_MISMATCH")
        self.assertEqual(self.client.post("/confirm", json={**body, "authorized": True}).status_code, 422)
        self.assertEqual(self.client.post("/confirm", json={**body, "conversation_id": self.cid}).json()["status"], "completed")
        self.assertEqual(self.client.get(f"/accounts/1001/card-operations/{pending['action_id']}").status_code, 200)

    def test_http_secure_chat_to_card_confirmation(self):
        async def create(**kwargs):
            return ChatCompletion.model_validate(completion("temporarily_freeze_card", {"card_id": CARD_A}))
        client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
        app.state.secure_agent = BankingAgent(client, "mock", partial(secure_execute_tool, manager=self.manager, audit_logger=self.logger))
        pending = self.client.post("/chat/secure", json={"account_id": "1001", "message": f"临时冻结{CARD_A}"}).json()
        self.assertEqual(pending["status"], "pending")
        self.assertFalse(self.frozen())
        result = self.client.post("/confirm", json={"account_id": "1001", "action_id": pending["action_id"], "conversation_id": pending["conversation_id"]})
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json()["status"], "completed")
        self.assertTrue(self.frozen())
