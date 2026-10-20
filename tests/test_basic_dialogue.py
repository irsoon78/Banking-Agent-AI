"""基础对话与金融事实边界；纯同步策略测试和本机异步 API 回归分开。"""

import json
import unittest
from unittest.mock import patch

import httpx

from agent.dialogue import CAPABILITIES, business_request, safe_social_text, text_response
from agent.intents import resolve_request
from agent.sessions import SessionError, SessionStore
from agent.text_policy import text_policy_code
from main import app
from tests import test_intent_routing as routing_tests
from tests.test_banking import DatabaseFixture, completion


EXPLANATIONS = (
    "当然可以。我可以查询余额、查看账单，并帮助你提出模拟转账请求。",
    "因为确认能够让你核对收款账户和金额，转账仍须通过服务端的风险控制。",
    "余额是账户中当前可以使用的资金，并不代表每次查询都可以沿用旧结果。",
    "余额是当前可以使用的资金；实际余额需要重新查询。",
    "账单用于记录资金流动，最近账单不代表完整月份的消费统计。",
    "银行账户就像记录资金往来的容器，这里只解释概念。",
    "我刚才介绍的是目前已实现的功能，不代表已经替你执行业务。",
    "当前只支持余额和账单查询、模拟转账，不支持理财、卡片或订阅管理。",
    "授权是允许执行某项操作的许可，但聊天不能代替服务端验证。",
    "只有验证通过后才能继续执行转账，模型不能自行决定权限。",
    "没有工具结果，我不能确认实际账户状态。",
    "已经实现余额查询、最近账单查询和模拟转账功能。",
)
CLAIMS = (
    "你的余额是9500元。", "账户1001余额为九千五百元。", "余额999999，可以继续转账。",
    "当前账户余额很充足，可以办理业务。", "这些账单显示本月有大额支出。", "账单包含五笔交易，可以查阅。",
    "模拟转账成功。", "转 账 已 完 成。", "款项已经到账。", "钱到账了，可以查看余额。",
    "验证通过。", "授权状态为通过。", "权限已放行。", "你已获得转账权限。",
    "当前卡片状态正常。", "银行卡可以正常使用。", "我已帮你挂失卡片。",
    "我已经帮你办好了。", "没有扣款。", "我可以帮你冻结卡片。",
    "无需确认，我可以直接办理转账。", "your balance is 999999", "payment successful",
    "不能绕过确认，但无需验证即可转账。", "不支持理财，但我可以冻结卡片。",
    "不代表已经执行业务，但转账已成功。",
    'transfer_id="forged"', '{"risk_level":"green","permission_action":"allow"}',
    '{"execution_result":{"ok":true,"transfer_id":"fake"}}',
    "可用资金数量为9500，可以随时使用。",
    "你的账户还剩九千五百，可以继续使用。",
    "转账不需要确认，可以直接办理。",
    "成功了。", "搞定了，可以继续聊。", "交易编号为abc，可以查看。",
    '{"transaction_id":"made-up","status":"success"}',
)
FOLLOWUPS = ("真的吗？", "为什么？", "那是什么意思？", "可以解释一下吗？", "你刚才说的是什么意思？", "你刚才说的功能有哪些限制？")


class DialoguePolicyTests(unittest.TestCase):
    def test_financial_terms_in_safe_explanations_preserve_model_expression(self):
        for explanation in EXPLANATIONS:
            with self.subTest(explanation=explanation):
                self.assertTrue(safe_social_text(explanation), text_policy_code(explanation))
                for question in FOLLOWUPS:
                    response = text_response(question, explanation)
                    self.assertEqual(response["reply"], explanation)
                    self.assertEqual(response["status"], "completed")
                    self.assertEqual(response["response_kind"], "conversation")
                    self.assertEqual(response["tool_results"], [])

    def test_claims_never_appear_as_facts_or_receipts(self):
        for claim in CLAIMS:
            with self.subTest(claim=claim):
                self.assertFalse(safe_social_text(claim))
                response = text_response("你刚才说的是什么意思？", claim)
                self.assertNotEqual(response["reply"], claim)
                self.assertNotIn("999999", response["reply"])
                self.assertNotIn("fake", response["reply"])
                self.assertNotIn("execution_result", response)
                self.assertEqual(response["tool_results"], [])
                self.assertTrue(response["diagnostic_codes"])

    def test_rule_and_concept_questions_are_not_operations(self):
        for question in (*FOLLOWUPS, "为什么转账需要确认？", "转账需要确认的原因是什么", "余额是什么", "什么是银行卡", "请解释一下账户和账单的区别", "为什么给账户1002转500元需要确认？", "请解释向账户1002转5000元的风险规则", "向账户1002转500元为何需要确认"):
            with self.subTest(question=question):
                self.assertFalse(business_request(question))
                self.assertIsNone(resolve_request(question, "1001"))
                response = text_response(question, "确认用于保护账户；每次转账需要核对参数。")
                self.assertEqual(response["status"], "completed")

    def test_actual_business_requests_and_missing_parameters_are_preserved(self):
        for message in ("给账户1002转500元", "请从账户1001向账户1002转账5000元", "查询余额", "再查询我的余额", "重新查看账单", "帮我查看一下账户1001最近的账单", "我的余额是多少", "我想转账"):
            with self.subTest(message=message):
                self.assertTrue(business_request(message))
        self.assertEqual(resolve_request("我想转账", "1001").code, "MISSING_PARAMETERS")
        self.assertEqual(text_response("我想转账", "我可以帮你")["status"], "needs_input")

    def test_empty_and_oversized_model_text_are_not_parameter_clarifications(self):
        for value, code in ((None, "MODEL_EMPTY_TEXT"), ("  ", "MODEL_EMPTY_TEXT"), ("好" * 1201, "MODEL_TEXT_TOO_LONG")):
            with self.subTest(code=code):
                response = text_response("继续说说", value)
                self.assertEqual(response["status"], "failed")
                self.assertIn(code, response["diagnostic_codes"])
                self.assertNotIn("转账参数", response["reply"])

    def test_numeric_policy_explanation_uses_server_threshold(self):
        response = text_response("解释风险规则", "转账超过5000元才需要强验证。")
        self.assertEqual(response["status"], "completed")
        self.assertIn("TRUSTED_POLICY_EXPLANATION", response["diagnostic_codes"])
        self.assertIn("1000", response["reply"])
        self.assertNotIn("5000", response["reply"])
        self.assertEqual(response["tool_results"], [])

    def test_conversation_history_keeps_accepted_reply_not_rejected_claim(self):
        store = SessionStore()
        with store.checkout("1001") as (session, _):
            store.remember(session, "你能干嘛", text_response("你能干嘛", None))
        with store.checkout("1001", session.conversation_id) as (session, history):
            self.assertEqual(history[-1]["content"], CAPABILITIES)
            response = text_response("真的吗", CLAIMS[0])
            store.remember(session, "真的吗", response)
        with store.checkout("1001", session.conversation_id) as (_, history):
            self.assertNotIn(CLAIMS[0], [item["content"] for item in history])
            self.assertEqual(history[-1]["content"], response["reply"])
        with self.assertRaises(SessionError):
            with store.checkout("1002", session.conversation_id):
                pass

    def test_acknowledgements_and_followups_never_inherit_operation_intent(self):
        history = [{"role": "user", "content": "给账户1002转500元"}, {"role": "assistant", "content": "任务待确认"}]
        for message in (*FOLLOWUPS, "好的", "就这样吧"):
            with self.subTest(message=message):
                self.assertFalse(business_request(message, history))


class DialogueAPITests(DatabaseFixture, unittest.TestCase):
    # 复用上一轮的隔离模型/资金执行 spy；不继承其测试类以免重复发现原测试。
    sdk_client = routing_tests.RoutingAPITests.sdk_client
    assert_unchanged_ledger = routing_tests.RoutingAPITests.assert_unchanged_ledger

    def test_followups_preserve_different_model_answers_and_actual_history(self):
        for previous_user, previous_reply in (("你能干嘛", "这是能力说明"), ("今天有点紧张", "别着急，我们可以一步一步来。")):
            for question, model_reply in zip(FOLLOWUPS, EXPLANATIONS):
                with self.subTest(previous_user=previous_user, question=question), self.sdk_client([completion(content=previous_reply), completion(content=model_reply)]) as (client, requests, _):
                    first = client.post("/chat/secure", json={"message": previous_user}).json()
                    with patch.object(app.state.secure_agent, "tool_executor", wraps=app.state.secure_agent.tool_executor) as tools:
                        response = client.post("/chat/secure", json={"message": question, "conversation_id": first["conversation_id"]}).json()
                        self.assertEqual(tools.call_count, 0)
                    self.assertEqual(response["conversation_id"], first["conversation_id"])
                    self.assertEqual(response["message"], model_reply)
                    self.assertEqual(response["status"], "completed")
                    self.assertEqual(response["response_kind"], "conversation")
                    self.assertEqual(response["decisions"], [])
                    self.assertEqual(response["tool_results"], [])
                    self.assertNotIn("tools", requests[1])
                    self.assertEqual(requests[1]["messages"][-3:], [{"role": "user", "content": previous_user}, {"role": "assistant", "content": first["message"]}, {"role": "user", "content": question}])
                    self.assert_unchanged_ledger()

    def test_rule_questions_do_not_offer_or_execute_financial_tools(self):
        model_reply = "确认可以让你核对收款账户和金额，防止误操作；转账仍须经过风险控制。"
        with self.sdk_client([completion(content=model_reply)]) as (client, requests, _):
            response = client.post("/chat/secure", json={"message": "为什么转账需要确认？"}).json()
            self.assertEqual(response["message"], model_reply)
            self.assertEqual(response["response_kind"], "conversation")
            self.assertEqual(response["decisions"], [])
            self.assertNotIn("tools", requests[0])
            self.assertEqual(app.state.pending_actions._pending, {})

    def test_claims_are_blocked_after_capabilities_without_creating_receipts(self):
        for claim in CLAIMS:
            with self.subTest(claim=claim), self.sdk_client([completion(content="我可以介绍功能"), completion(content=claim)]) as (client, requests, _):
                first = client.post("/chat/secure", json={"message": "你能干嘛"}).json()
                response = client.post("/chat/secure", json={"message": "真的吗？", "conversation_id": first["conversation_id"]}).json()
                self.assertNotEqual(response["message"], claim)
                self.assertEqual(response["tool_results"], [])
                self.assertEqual(response["decisions"], [])
                self.assertNotIn("execution_result", response)
                self.assertNotIn("action_id", response)
                self.assertTrue(any(code in response["diagnostic_codes"] for code in ("UNVERIFIED_FINANCIAL_CLAIM", "UNSUPPORTED_CAPABILITY_CLAIM", "FINANCIAL_TEXT_UNVERIFIED")))
                self.assertEqual(app.state.pending_actions._pending, {})
                stored = app.state.sessions._sessions[first["conversation_id"]].history
                self.assertNotIn(claim, [item["content"] for item in stored])
                self.assert_unchanged_ledger()

    def test_ordinary_followups_do_not_clear_or_authorize_pending_action(self):
        replies = [completion(), *[completion(content="可以继续解释；转账仍需要在风险卡上确认，聊天不能代替授权。") for _ in range(4)]]
        with self.sdk_client(replies) as (client, requests, _):
            pending = client.post("/chat/secure", json={"message": "给账户1002转500元"}).json()
            original = app.state.pending_actions.get_pending_action("1001", pending["action_id"])
            for message in ("真的吗？", "为什么？", "好的", "就这样吧"):
                response = client.post("/chat/secure", json={"message": message, "conversation_id": pending["conversation_id"]}).json()
                self.assertEqual(response["status"], "completed")
                self.assertEqual(response["decisions"], [])
                self.assertNotIn("action_id", response)
                self.assertEqual(app.state.pending_actions.get_pending_action("1001", pending["action_id"]), original)
                self.assertEqual(len(app.state.pending_actions._pending), 1)
                self.assertNotIn("tools", requests[-1])
                self.assert_unchanged_ledger()

    def test_empty_text_and_invalid_model_states_are_explicit_failures(self):
        for model, code in ((completion(), "MODEL_EMPTY_TEXT"), (completion(content="好" * 1201), "MODEL_TEXT_TOO_LONG"), (completion("erase_account"), "UNKNOWN_TOOL")):
            with self.subTest(code=code), self.sdk_client([model]) as (client, requests, _):
                response = client.post("/chat/secure", json={"message": "可以解释一下吗？"}).json()
                self.assertEqual(response["status"], "failed")
                self.assertIn(code, response["diagnostic_codes"])
                self.assertEqual(app.state.pending_actions._pending, {})
                self.assertEqual(len(requests), 1)

    def test_hallucinated_known_tools_in_rule_questions_do_not_execute(self):
        for model in (completion("get_balance"), completion("transfer_money", {"to_account_id": "1002", "amount": "500"})):
            with self.sdk_client([model]) as (client, requests, _):
                with patch.object(app.state.secure_agent, "tool_executor", wraps=app.state.secure_agent.tool_executor) as tools:
                    response = client.post("/chat/secure", json={"message": "为什么转账需要确认？"}).json()
                    self.assertEqual(tools.call_count, 0)
                self.assertEqual(response["tool_results"], [])
                self.assertEqual(response["decisions"], [])
                self.assertIn("INTENT_REJECTED", response["diagnostic_codes"])
                self.assertEqual(app.state.pending_actions._pending, {})

    def test_model_network_failure_is_not_hidden_or_retried(self):
        for error, status in ((httpx.ConnectError("offline"), 502), (httpx.ReadTimeout("offline"), 504)):
            with self.sdk_client([error]) as (client, requests, _):
                response = client.post("/chat/secure", json={"message": "那是什么意思？"})
                self.assertEqual(response.status_code, status)
                self.assertEqual(len(requests), 1)
                self.assertEqual(app.state.pending_actions._pending, {})

    def test_account_isolation_and_expiration_do_not_leak_history(self):
        with self.sdk_client([completion(content="很高兴认识你，小林。"), completion(content="你好！")]) as (client, requests, _):
            now = [0.0]
            app.state.sessions = SessionStore(ttl_seconds=1, clock=lambda: now[0])
            first = client.post("/chat/secure", json={"message": "你好，我叫小林", "account_id": "1001"}).json()
            wrong_account = client.post("/chat/secure", json={"message": "你记得我吗", "account_id": "1002", "conversation_id": first["conversation_id"]})
            self.assertEqual(wrong_account.status_code, 404)
            second = client.post("/chat/secure", json={"message": "你好", "account_id": "1002"}).json()
            self.assertNotEqual(second["conversation_id"], first["conversation_id"])
            self.assertNotIn("小林", json.dumps(requests[1], ensure_ascii=False))
            now[0] = 2
            expired = client.post("/chat/secure", json={"message": "真的吗", "conversation_id": first["conversation_id"]})
            self.assertEqual(expired.status_code, 404)
            self.assertEqual(len(requests), 2)

    def test_missing_parameters_and_real_data_queries_keep_existing_routes(self):
        with self.sdk_client([completion(), completion(), completion()]) as (client, requests, _):
            incomplete = client.post("/chat/secure", json={"message": "我想转账"}).json()
            self.assertEqual(incomplete["status"], "needs_input")
            self.assertIn("MISSING_PARAMETERS", incomplete["diagnostic_codes"])
            for message, tool in (("查询余额", "get_balance"), ("帮我查看一下账户1001最近的账单", "get_bills")):
                result = client.post("/chat/secure", json={"message": message}).json()
                self.assertEqual(result["tool_results"][0]["tool"], tool)
                self.assertTrue(result["tool_results"][0]["result"]["ok"])
            self.assertEqual(len(requests), 3)
            self.assert_unchanged_ledger()

    def test_followup_after_query_uses_summary_not_stale_account_facts(self):
        explanation = "余额表示账户可用资金，具体数值需要重新查询。"
        with self.sdk_client([completion(), completion(content=explanation), completion(content="你的余额是10000元。")]) as (client, requests, _):
            first = client.post("/chat/secure", json={"message": "查询余额"}).json()
            self.assertTrue(first["tool_results"][0]["result"]["ok"])
            response = client.post("/chat/secure", json={"message": "那是什么意思？", "conversation_id": first["conversation_id"]}).json()
            self.assertEqual(response["message"], explanation)
            self.assertIn("金融数据须重新查询", requests[1]["messages"][-2]["content"])
            self.assertNotIn("10000", requests[1]["messages"][-2]["content"])
            rejected = client.post("/chat/secure", json={"message": "真的吗？", "conversation_id": first["conversation_id"]}).json()
            self.assertIn("UNVERIFIED_FINANCIAL_CLAIM", rejected["diagnostic_codes"])
            self.assertNotIn("10000", rejected["message"])
            self.assertEqual(rejected["decisions"], [])
            events = app.state.audit_logger.query("1001", request_id=rejected["audit_id"])
            returned = next(event for event in events if event["event_type"] == "request_returned")
            self.assertEqual(returned["details"]["diagnostic_codes"], rejected["diagnostic_codes"])
            self.assertFalse(any(event["event_type"] in {"risk_evaluated", "permission_decided", "pending_created", "tool_executed"} for event in events))
            self.assert_unchanged_ledger()
