"""有限意图解析与真实安全入口回归；临时数据库、MockTransport，禁止资金工具执行。

IntentParserTests 可在同步环境执行；RoutingAPITests 和 StandaloneRoutingTests
需要本机可用的 asyncio。不要为适配沙箱改写事件循环或业务实现。
"""

from contextlib import contextmanager
import json
import os
import unittest
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient
from openai import AsyncOpenAI

from agent.dialogue import CAPABILITIES, capability_question, text_response
from agent.intents import resolve_request, transfer_request_issue
from agent.llm_agent import BankingAgent
from agent.llm_agent import AgentPause, execute_read_only_tool
from api.responses import decision_scope, secure_response
from deepseek_client import DeepSeekHTTPClient
from main import app, audited_execute_tool, execute_saved_action, finish_audit, secure_execute_tool
from security import AuditLogger, PendingActionManager
from tests.test_banking import DatabaseFixture, completion
from tools.account import get_balance
from tools.bill import get_bills


EXPRESSIONS = (
    "给账户1002转{amount}元",
    "请从账户1001向账户1002转账{amount}元",
)
REJECTED = (
    "不要给账户1002转5000元", "如果给账户1002转5000元会怎样",
    "昨天我说过给账户1002转5000元", "“给账户1002转5000元”", "`给账户1002转500元`",
    "余额足够的话给账户1002转5000元", "给账户1002转500元再给账户1003转500元",
    "给账户1002和1003转500元", "给账户1002转500或700元",
    "给1002/1003转500元", "给1002及1003转500元", "给1002转500/700元",
    "明天给账户1002转500元", "给账户1002转500元?",
)


class IntentParserTests(unittest.TestCase):
    def test_both_transfer_forms_preserve_recipient_and_decimal_amount(self):
        for template in EXPRESSIONS:
            for amount in ("500", "5000", "500.00", "0.01", "1000.01"):
                with self.subTest(template=template, amount=amount):
                    result = resolve_request(template.format(amount=amount), "1001")
                    self.assertEqual(result.tool, "transfer_money")
                    self.assertEqual(result.arguments, {"to_account_id": "1002", "amount": amount})
                    self.assertIsNone(transfer_request_issue(template.format(amount=amount), "1001", result.arguments, []))

    def test_denied_expressions_never_propose_a_tool(self):
        for message in REJECTED:
            with self.subTest(message=message):
                result = resolve_request(message, "1001")
                self.assertTrue(result is None or result.tool is None)
                self.assertEqual(transfer_request_issue(message, "1001", {"to_account_id": "1002", "amount": "500"}, []), "INTENT_REJECTED")

    def test_invalid_or_ambiguous_amount_never_becomes_a_different_amount(self):
        for amount in ("50 00", "5000.001", "5,000", "5e3", "-500", "0", "500美元", "五千"):
            with self.subTest(amount=amount):
                result = resolve_request(f"给账户1002转{amount}元", "1001")
                self.assertTrue(result is None or result.tool is None)

    def test_incomplete_parameters_are_distinguished_from_unresolved_phrasing(self):
        for message in ("我想转账", "给账户1002转账", "转账500元", "给李四转500元"):
            with self.subTest(message=message):
                self.assertEqual(resolve_request(message, "1001").code, "MISSING_PARAMETERS")
        self.assertIsNone(resolve_request("模拟转账500元给1002", "1001"))
        self.assertNotIn("收款账户编号和转账金额", text_response("模拟转账500元给1002", None)["reply"])

    def test_sender_mismatch_is_rejected(self):
        message = "请从账户1003向账户1002转账500元"
        self.assertEqual(resolve_request(message, "1001").code, "ACCOUNT_MISMATCH")
        self.assertEqual(transfer_request_issue(message, "1001", {"to_account_id": "1002", "amount": "500"}, []), "ACCOUNT_MISMATCH")
        for message in ("用账户1003给账户1002转500元", "账户1003向账户1002转500元", "转出账户1003向1002转500元", "从账户" + "9" * 65 + "向1002转500元"):
            with self.subTest(message=message):
                self.assertEqual(transfer_request_issue(message, "1001", {"to_account_id": "1002", "amount": "500"}, []), "ACCOUNT_MISMATCH")

    def test_model_cannot_invent_or_replace_user_parameters(self):
        for message, arguments, code in (
            ("我想转账", {"to_account_id": "1002", "amount": "500"}, "MISSING_PARAMETERS"),
            ("给1002转500元", {"to_account_id": "1003", "amount": "500"}, "INTENT_REJECTED"),
            ("给1002转5000元", {"to_account_id": "1002", "amount": "500"}, "INTENT_REJECTED"),
            ("给账户500元转账", {"to_account_id": "500", "amount": "500"}, "INTENT_REJECTED"),
        ):
            with self.subTest(message=message, arguments=arguments):
                self.assertEqual(transfer_request_issue(message, "1001", arguments, []), code)
        for amount in ("-500", "+500", "−500", "5e2", "5,00", "500.001"):
            with self.subTest(amount=amount):
                self.assertIsNotNone(transfer_request_issue(f"给1002转{amount}元", "1001", {"to_account_id": "1002", "amount": "500"}, []))

    def test_only_explicit_user_supplement_can_use_previous_user_intent(self):
        arguments = {"to_account_id": "1002", "amount": "500"}
        history = [{"role": "user", "content": "我想转账"}, {"role": "assistant", "content": "账户1003金额999元"}]
        self.assertIsNone(transfer_request_issue("收款账户1002，金额500元", "1001", arguments, history))
        self.assertEqual(transfer_request_issue("500元", "1001", arguments, history), "INTENT_REJECTED")
        self.assertEqual(transfer_request_issue("查询余额", "1001", arguments, history), "INTENT_REJECTED")
        self.assertIsNone(resolve_request("收款账户1002，金额500元", "1001"))

    def test_read_queries_use_default_limit_and_current_account(self):
        for message in ("查询账单", "帮我查看一下账户1001最近的账单", "查看我的账单"):
            with self.subTest(message=message):
                self.assertEqual(resolve_request(message, "1001").arguments, {"limit": 10})
        self.assertEqual(resolve_request("查询账户1001的当前余额", "1001").tool, "get_balance")
        self.assertEqual(resolve_request("查最近20条账单", "1001").arguments, {"limit": 20})
        self.assertEqual(resolve_request("查询账户1002最近账单", "1001").code, "ACCOUNT_MISMATCH")
        for message in ("查最近0条账单", "查最近101条账单", "不要查询账单", "统计完整月份的消费"):
            self.assertIsNone(resolve_request(message, "1001"))

    def test_capability_variants_use_trusted_content(self):
        for message in ("你能干嘛", "你能做什么？", "你可以帮我做些什么", "我需要提供哪些信息才能转账？"):
            with self.subTest(message=message):
                self.assertTrue(capability_question(message))
                response = text_response(message, "已经为你转账成功")
                self.assertEqual(response["response_kind"], "capabilities")
                self.assertEqual(response["reply"], CAPABILITIES)


class SafeDecisionTests(DatabaseFixture, unittest.TestCase):
    """不创建事件循环，直接验证兜底参数经过原安全执行器及持久化审计。"""

    def test_parsed_commands_reach_real_risk_permission_and_audit_without_funds_execution(self):
        for template in EXPRESSIONS:
            for amount, risk in (("500", "yellow"), ("5000", "red"), ("1000", "yellow"), ("1000.01", "red")):
                message = template.format(amount=amount)
                proposal = resolve_request(message, "1001")
                logger = AuditLogger()
                manager = PendingActionManager(execute_saved_action, audit_logger=logger)
                with self.subTest(message=message), decision_scope() as decisions, logger.request_context("1001", message) as context:
                    with patch("agent.llm_agent.transfer_money", side_effect=AssertionError("No funds execution allowed")) as funds:
                        with self.assertRaises(AgentPause) as paused:
                            secure_execute_tool(proposal.tool, json.dumps(proposal.arguments), "1001", manager, logger)
                        self.assertEqual(funds.call_count, 0)
                    result = paused.exception.response
                    self.assertEqual(result["risk_level"], risk)
                    self.assertEqual(result["permission_action"], "pending_confirmation" if risk == "yellow" else "require_verification")
                    self.assertEqual(len(decisions), 1)
                    self.assertEqual(len(manager._pending), 1 if risk == "yellow" else 0)
                    response = {**result, "reply": result["message"], "tool_results": [], "diagnostic_codes": ["MODEL_NO_TOOL_CALL", proposal.code]}
                    finish_audit(logger, context, response)
                    adapted = secure_response(response, decisions)
                    self.assertEqual(adapted["risk_level"], risk)
                    rows = logger.query("1001", request_id=adapted["audit_id"])
                    events = [row["event_type"] for row in rows]
                    self.assertIn("risk_evaluated", events)
                    self.assertIn("permission_decided", events)
                    self.assertEqual(events.count("pending_created"), 1 if risk == "yellow" else 0)
                    self.assertNotIn("tool_execution_started", events)
                    self.assertNotIn("tool_executed", events)
                    self.assertEqual(rows[0]["details"]["diagnostic_codes"], response["diagnostic_codes"])
                    self.assertEqual(get_balance("1001")["balance"], "10000.00")
                    self.assertEqual(get_balance("1002")["balance"], "5000.00")

    def test_legacy_and_default_executors_remain_read_only_for_fallback_parameters(self):
        for amount in ("500", "5000"):
            proposal = resolve_request(f"给账户1002转{amount}元", "1001")
            raw = json.dumps(proposal.arguments)
            logger = AuditLogger()
            with logger.request_context("1001", "test") as context, patch("agent.llm_agent.transfer_money") as funds:
                self.assertEqual(audited_execute_tool(proposal.tool, raw, "1001", logger)["code"], "LEGACY_WRITE_DISABLED")
                self.assertEqual(execute_read_only_tool(proposal.tool, raw, "1001")["code"], "SECURE_ENTRYPOINT_REQUIRED")
                self.assertEqual(funds.call_count, 0)
                events = [row["event_type"] for row in logger.query("1001", request_id=context["request_id"])]
                self.assertIn("legacy_write_blocked", events)
                self.assertNotIn("tool_executed", events)

    def test_read_fallback_uses_database_results_and_preserves_account_scope(self):
        manager = PendingActionManager(execute_saved_action)
        with patch("agent.llm_agent.transfer_money") as funds:
            for message, tool in (("查询余额", "get_balance"), ("帮我查看一下账户1001最近的账单", "get_bills")):
                proposal = resolve_request(message, "1001")
                result = secure_execute_tool(proposal.tool, json.dumps(proposal.arguments), "1001", manager)
                self.assertTrue(result["ok"])
                self.assertEqual(result["account_id"], "1001")
                if tool == "get_balance":
                    self.assertEqual(result["balance"], "10000.00")
                else:
                    self.assertEqual(result["bills"][0]["kind"], "opening")
            self.assertEqual(funds.call_count, 0)

    def test_invalid_arguments_cannot_prepare_an_action(self):
        logger = AuditLogger()
        manager = PendingActionManager(execute_saved_action, audit_logger=logger)
        for raw in ("{invalid", '{"amount":"500"}', '{"to_account_id":"1002","amount":500}'):
            with patch("agent.llm_agent.transfer_money") as funds:
                result = secure_execute_tool("transfer_money", raw, "1001", manager, logger)
                self.assertEqual(result["code"], "INVALID_ARGUMENTS")
                self.assertEqual(manager._pending, {})
                self.assertEqual(funds.call_count, 0)


class RoutingAPITests(DatabaseFixture, unittest.TestCase):
    @contextmanager
    def sdk_client(self, responses):
        requests = []

        def handler(request):
            requests.append(json.loads(request.content))
            response = responses.pop(0)
            if isinstance(response, Exception):
                raise response
            return httpx.Response(200, json=response)

        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "offline-test-key"}), patch(
            "main.DeepSeekHTTPClient", side_effect=lambda: DeepSeekHTTPClient(transport=httpx.MockTransport(handler)),
        ), patch("agent.llm_agent.transfer_money", side_effect=AssertionError("No funds execution allowed")) as funds, TestClient(app) as client:
            try:
                yield client, requests, funds
            finally:
                self.assertEqual(funds.call_count, 0)

    def assert_unchanged_ledger(self):
        self.assertEqual(get_balance("1001")["balance"], "10000.00")
        self.assertEqual(get_balance("1002")["balance"], "5000.00")
        self.assertEqual(len(get_bills("1001")["bills"]), 1)
        self.assertEqual(len(get_bills("1002")["bills"]), 1)

    def test_model_and_fallback_both_use_real_yellow_and_red_decisions(self):
        for use_tool in (False, True):
            for template in EXPRESSIONS:
                for amount, risk, status in (("500", "yellow", "pending"), ("5000", "red", "require_verification")):
                    with self.subTest(use_tool=use_tool, template=template, amount=amount):
                        model = completion("transfer_money", {"to_account_id": "1002", "amount": amount}) if use_tool else completion(content="请提供完整信息")
                        with self.sdk_client([model]) as (client, requests, funds):
                            response = client.post("/chat/secure", json={"message": template.format(amount=amount)}).json()
                            self.assertEqual(response["status"], status)
                            self.assertEqual(response["risk_level"], risk)
                            self.assertEqual(response["permission_action"], "pending_confirmation" if risk == "yellow" else "require_verification")
                            self.assertEqual(response["require_confirmation"], risk == "yellow")
                            self.assertEqual(response["require_verification"], risk == "red")
                            self.assertEqual(requests[0]["tool_choice"], "auto")
                            self.assertEqual(len(requests), 1)
                            if risk == "yellow":
                                record = app.state.pending_actions.get_pending_action("1001", response["action_id"])
                                self.assertEqual(record["params"], {"to_account_id": "1002", "amount": amount})
                            else:
                                self.assertNotIn("action_id", response)
                                self.assertEqual(app.state.pending_actions._pending, {})
                            rows = AuditLogger().query("1001", request_id=response["audit_id"])
                            events = [row["event_type"] for row in rows]
                            self.assertIn("risk_evaluated", events)
                            self.assertIn("permission_decided", events)
                            self.assertNotIn("tool_execution_started", events)
                            self.assertNotIn("tool_executed", events)
                            decision = next(row for row in rows if row["event_type"] == "permission_decided")
                            self.assertEqual(decision["risk_level"], risk)
                            returned = next(row for row in rows if row["event_type"] == "request_returned")
                            self.assertEqual(returned["details"]["diagnostic_codes"], response["diagnostic_codes"])
                            if not use_tool:
                                self.assertEqual(response["diagnostic_codes"], ["MODEL_NO_TOOL_CALL", "TRANSFER_FALLBACK"])
                            self.assert_unchanged_ledger()

    def test_rejected_and_incomplete_commands_never_reach_funds_or_pending(self):
        for use_tool in (False, True):
            for message in (*REJECTED, "我想转账", "给账户1002转账", "转账500元", "给李四转500元", "请从账户1003向账户1002转账500元"):
                with self.subTest(use_tool=use_tool, message=message):
                    model = completion("transfer_money", {"to_account_id": "1002", "amount": "500"}) if use_tool else completion()
                    with self.sdk_client([model]) as (client, requests, _):
                        response = client.post("/chat/secure", json={"message": message}).json()
                        self.assertEqual(response["status"], "needs_input")
                        self.assertEqual(response["decisions"], [])
                        self.assertEqual(app.state.pending_actions._pending, {})
                        self.assertEqual(len(requests), 1)
                        self.assert_unchanged_ledger()

    def test_model_argument_substitution_is_rejected(self):
        for arguments in ({"to_account_id": "1003", "amount": "5000"}, {"to_account_id": "1002", "amount": "500"}):
            with self.sdk_client([completion("transfer_money", arguments)]) as (client, _, _):
                response = client.post("/chat/secure", json={"message": "给账户1002转5000元"}).json()
                self.assertIn("INTENT_REJECTED", response["diagnostic_codes"])
                self.assertEqual(app.state.pending_actions._pending, {})

    def test_reads_without_model_tool_calls_use_real_tools(self):
        for message, tool in (("查询账户1001的当前余额", "get_balance"), ("帮我查看一下账户1001最近的账单", "get_bills"), ("查询账单", "get_bills")):
            with self.subTest(message=message), self.sdk_client([completion(content="余额999999元，转账已成功")]) as (client, requests, _):
                response = client.post("/chat/secure", json={"message": message}).json()
                self.assertEqual(response["status"], "completed")
                self.assertEqual(response["tool_results"][0]["tool"], tool)
                self.assertNotIn("999999", response["message"])
                self.assertIn("READ_FALLBACK", response["diagnostic_codes"])
                self.assertEqual(len(requests), 1)
                self.assert_unchanged_ledger()

    def test_greetings_and_capabilities_keep_tools_disabled(self):
        for message, kind in (("你好", "conversation"), ("你能干嘛", "capabilities"), ("你能做什么？", "capabilities")):
            with self.subTest(message=message), self.sdk_client([completion(content="你好！")]) as (client, requests, _):
                response = client.post("/chat/secure", json={"message": message}).json()
                self.assertEqual(response["response_kind"], kind)
                self.assertNotIn("tools", requests[0])
                self.assertEqual(response["tool_results"], [])
                self.assertEqual(response["decisions"], [])

    def test_pending_is_not_replaced_and_text_confirmation_does_not_execute(self):
        with self.sdk_client([completion(), completion(), completion(), completion()]) as (client, requests, _):
            first = client.post("/chat/secure", json={"message": "给账户1002转500元"}).json()
            for message in ("给账户1002转700元", "我确认了", "你好"):
                response = client.post("/chat/secure", json={"message": message, "conversation_id": first["conversation_id"]}).json()
                self.assertNotIn("action_id", response)
                if "700" in message:
                    self.assertIn("PENDING_EXISTS", response["diagnostic_codes"])
                self.assertEqual(len(app.state.pending_actions._pending), 1)
                self.assertEqual(app.state.pending_actions.get_pending_action("1001", first["action_id"])["params"]["amount"], "500")
                self.assert_unchanged_ledger()
            self.assertEqual(len(requests), 4)

    def test_old_chat_keeps_write_blocked_when_using_fallback(self):
        for amount in ("500", "5000"):
            with self.sdk_client([completion()]) as (client, requests, _):
                response = client.post("/chat", json={"message": f"给账户1002转{amount}元"}).json()
                self.assertEqual(set(response), {"status", "reply", "tool_results"})
                self.assertEqual(response["tool_results"][0]["result"]["code"], "LEGACY_WRITE_DISABLED")
                self.assertEqual(app.state.pending_actions._pending, {})
                self.assertEqual(len(requests), 1)
                self.assert_unchanged_ledger()

    def test_invalid_calls_do_not_trigger_fallback(self):
        invalid_json = completion("transfer_money", {"to_account_id": "1002", "amount": "500"})
        invalid_json["choices"][0]["message"]["tool_calls"][0]["function"]["arguments"] = "{invalid"
        invalid = (
            (completion("erase_account"), "UNKNOWN_TOOL"),
            (completion("transfer_money", {"amount": "500"}), "TOOL_ARGUMENTS_INVALID"),
            (completion("transfer_money", {"to_account_id": "1002", "amount": 500}), "TOOL_ARGUMENTS_INVALID"),
            (completion("transfer_money", {"to_account_id": "1002", "amount": "500", "confirmed": True}), "TOOL_ARGUMENTS_INVALID"),
            (invalid_json, "TOOL_ARGUMENTS_INVALID"),
        )
        for model, diagnostic in invalid:
            with self.subTest(diagnostic=diagnostic), self.sdk_client([model]) as (client, requests, _):
                response = client.post("/chat/secure", json={"message": "给账户1002转500元"}).json()
                self.assertEqual(response["status"], "failed")
                self.assertIn(diagnostic, response["diagnostic_codes"])
                self.assertNotIn("TRANSFER_FALLBACK", response["diagnostic_codes"])
                self.assertEqual(app.state.pending_actions._pending, {})
                self.assertEqual(len(requests), 1)
                self.assert_unchanged_ledger()

    def test_incomplete_or_failed_model_completion_never_uses_fallback(self):
        for reason in ("length", "content_filter", "tool_calls"):
            model = completion(content="请继续")
            model["choices"][0]["finish_reason"] = reason
            with self.subTest(reason=reason), self.sdk_client([model]) as (client, requests, _):
                response = client.post("/chat/secure", json={"message": "给账户1002转500元"}).json()
                self.assertEqual(response["status"], "failed")
                self.assertIn("MODEL_CALL_ERROR", response["diagnostic_codes"])
                self.assertEqual(app.state.pending_actions._pending, {})
                self.assertEqual(len(requests), 1)

    def test_multiple_calls_and_explicit_refusal_never_use_fallback(self):
        single = completion("transfer_money", {"to_account_id": "1002", "amount": "500"})
        calls = single["choices"][0]["message"]["tool_calls"]
        refused = completion()
        refused["choices"][0]["message"]["refusal"] = "request refused"
        for model, diagnostic in ((completion(calls=calls * 2), "INVALID_TOOL_CALL"), (refused, "MODEL_CALL_ERROR")):
            with self.subTest(diagnostic=diagnostic), self.sdk_client([model]) as (client, requests, _):
                response = client.post("/chat/secure", json={"message": "给账户1002转500元"}).json()
                self.assertEqual(response["status"], "failed")
                self.assertIn(diagnostic, response["diagnostic_codes"])
                self.assertNotIn("TRANSFER_FALLBACK", response["diagnostic_codes"])
                self.assertEqual(app.state.pending_actions._pending, {})
                self.assertEqual(len(requests), 1)

    def test_empty_model_choices_do_not_trigger_fallback(self):
        model = completion()
        model["choices"] = []
        with self.sdk_client([model]) as (client, requests, _):
            response = client.post("/chat/secure", json={"message": "给账户1002转500元"})
            self.assertEqual(response.status_code, 502)
            self.assertEqual(app.state.pending_actions._pending, {})
            failed = next(row for row in AuditLogger().query("1001") if row["event_type"] == "request_failed")
            self.assertEqual(failed["details"]["diagnostic_code"], "MODEL_EMPTY_RESPONSE")
            self.assertEqual(len(requests), 1)

    def test_explicit_other_account_read_is_rejected_with_or_without_model_tool(self):
        for model in (completion(), completion("get_balance")):
            with self.sdk_client([model]) as (client, requests, _):
                response = client.post("/chat/secure", json={"message": "查询账户1002余额"}).json()
                self.assertEqual(response["diagnostic_codes"][-1], "ACCOUNT_MISMATCH")
                self.assertEqual(response["tool_results"], [])
                self.assertEqual(response["decisions"], [])
                self.assertEqual(len(requests), 1)

    def test_network_failures_are_not_retried_and_have_audit_diagnostics(self):
        for error, status, diagnostic in ((httpx.ConnectError("offline"), 502, "MODEL_ERROR"), (httpx.ReadTimeout("offline"), 504, "MODEL_TIMEOUT")):
            with self.subTest(status=status), self.sdk_client([error]) as (client, requests, _):
                response = client.post("/chat/secure", json={"message": "给账户1002转500元"})
                self.assertEqual(response.status_code, status)
                self.assertEqual(len(requests), 1)
                self.assertEqual(app.state.pending_actions._pending, {})
                failed = next(row for row in AuditLogger().query("1001") if row["event_type"] == "request_failed")
                self.assertEqual(failed["details"]["diagnostic_code"], diagnostic)
                self.assert_unchanged_ledger()

    def test_completed_read_then_no_tool_call_never_starts_transfer_fallback(self):
        with self.sdk_client([completion("get_balance"), completion()]) as (client, requests, _):
            response = client.post("/chat/secure", json={"message": "给账户1002转500元"}).json()
            self.assertEqual(response["tool_results"][0]["tool"], "get_balance")
            self.assertNotIn("TRANSFER_FALLBACK", response["diagnostic_codes"])
            self.assertEqual(app.state.pending_actions._pending, {})
            self.assertEqual(len(requests), 2)

    def test_unknown_execution_result_is_not_retried(self):
        with self.sdk_client([completion()]) as (client, requests, _):
            with patch.object(app.state.pending_actions, "_executor", side_effect=RuntimeError("private diagnostic")) as executor:
                response = client.post("/chat/secure", json={"message": "查询余额"}).json()
            self.assertEqual(response["status"], "execution_unknown")
            self.assertIn("EXECUTION_UNKNOWN", response["diagnostic_codes"])
            self.assertEqual(executor.call_count, 1)
            self.assertEqual(len(requests), 1)
            self.assert_unchanged_ledger()


class StandaloneRoutingTests(DatabaseFixture, unittest.IsolatedAsyncioTestCase):
    async def test_default_executor_stays_read_only_for_transfer_fallback(self):
        calls = []

        def handler(request):
            calls.append(request)
            return httpx.Response(200, json=completion())

        async with AsyncOpenAI(api_key="offline-test-key", max_retries=0, http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler))) as client:
            with patch("agent.llm_agent.transfer_money", side_effect=AssertionError("No funds execution allowed")) as funds:
                response = await BankingAgent(client, "test-model").run("给账户1002转500元", "1001")
                self.assertEqual(response["tool_results"][0]["result"]["code"], "SECURE_ENTRYPOINT_REQUIRED")
                self.assertEqual(funds.call_count, 0)
        self.assertEqual(len(calls), 1)
