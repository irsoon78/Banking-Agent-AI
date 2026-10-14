"""无需 Key 和网络；使用临时 SQLite 与 SDK 的 MockTransport。"""

import json
import os
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient
from openai import AsyncOpenAI

from agent.llm_agent import AgentError, BankingAgent, NO_TOOL_REPLY, TOOLS, execute_tool
from database.db import connection, init_db
from deepseek_client import DeepSeekHTTPClient
from main import app
from tools.account import get_balance
from tools.bill import get_bills
from tools.transfer import transfer_money


class DatabaseFixture:
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        env = patch.dict(os.environ, {
            "BANK_DB_PATH": str(Path(self.temp.name) / "test.db"),
            "DEEPSEEK_API_KEY": "",
            "DEEPSEEK_BASE_URL": "https://api.deepseek.com/beta",
            "DEEPSEEK_MODEL": "deepseek-flash",
        })
        env.start()
        self.addCleanup(env.stop)
        init_db()


class BankingTests(DatabaseFixture, unittest.TestCase):
    def test_initial_data_and_restart(self):
        self.assertEqual(get_balance("1001")["balance"], "10000.00")
        transfer_money("1001", "1002", "0.01")
        init_db()
        self.assertEqual(get_balance("1001")["balance"], "9999.99")
        self.assertEqual(len(get_bills("1001")["bills"]), 2)

    def test_transfer_updates_both_balances_and_linked_bills(self):
        result = transfer_money("1001", "1002", "100.50")
        self.assertTrue(result["ok"])
        self.assertEqual(get_balance("1001")["balance"], "9899.50")
        self.assertEqual(get_balance("1002")["balance"], "5100.50")
        outgoing = get_bills("1001")["bills"][0]
        incoming = get_bills("1002")["bills"][0]
        self.assertEqual(outgoing["direction"], "out")
        self.assertEqual(incoming["direction"], "in")
        self.assertEqual(outgoing["transfer_id"], result["transfer_id"])
        self.assertEqual(incoming["transfer_id"], result["transfer_id"])
        self.assertEqual(outgoing["amount_cents"], incoming["amount_cents"])

    def test_invalid_transfers_leave_data_unchanged(self):
        for amount in ("0", "-1", "1.001", "NaN", "1e2", "Infinity", "", "99999999999", 1.2, True):
            with self.subTest(amount=amount):
                self.assertFalse(transfer_money("1001", "1002", amount)["ok"])
        for sender, recipient, amount, code in (
            ("1001", "1002", "10000.01", "INSUFFICIENT_FUNDS"),
            ("1001", "1001", "1", "SAME_ACCOUNT"),
            ("1001", "missing", "1", "ACCOUNT_NOT_FOUND"),
            ("missing", "1002", "1", "ACCOUNT_NOT_FOUND"),
        ):
            self.assertEqual(transfer_money(sender, recipient, amount)["code"], code)
        self.assertEqual(get_balance("1001")["balance"], "10000.00")
        self.assertEqual(get_balance("1002")["balance"], "5000.00")
        self.assertEqual(len(get_bills("1001")["bills"]), 1)

    def test_sql_failure_rolls_back_all_writes(self):
        with connection() as conn:
            conn.execute("""
                CREATE TRIGGER fail_incoming BEFORE INSERT ON bills
                WHEN NEW.kind = 'transfer_in'
                BEGIN SELECT RAISE(ABORT, 'forced failure'); END
            """)
        self.assertEqual(transfer_money("1001", "1002", "100")["code"], "DATABASE_ERROR")
        self.assertEqual(get_balance("1001")["balance"], "10000.00")
        self.assertEqual(get_balance("1002")["balance"], "5000.00")
        self.assertEqual(len(get_bills("1001")["bills"]), 1)

    def test_concurrent_transfers_cannot_overdraw(self):
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: transfer_money("1001", "1002", "6000"), range(2)))
        self.assertEqual(sum(result["ok"] for result in results), 1)
        self.assertEqual(get_balance("1001")["balance"], "4000.00")
        self.assertEqual(get_balance("1002")["balance"], "11000.00")

    def test_queries_and_argument_validation(self):
        for tool in TOOLS:
            schema = tool["function"]["parameters"]
            self.assertFalse(schema["additionalProperties"])
            self.assertEqual(set(schema["required"]), set(schema["properties"]))
        self.assertEqual(get_balance("unknown")["code"], "ACCOUNT_NOT_FOUND")
        self.assertEqual(get_bills("unknown")["code"], "ACCOUNT_NOT_FOUND")
        self.assertEqual(get_bills("1001", True)["code"], "INVALID_LIMIT")
        self.assertEqual(get_bills("1001", 101)["code"], "INVALID_LIMIT")
        self.assertEqual(execute_tool("unknown", "{}", "1001")["code"], "UNKNOWN_TOOL")
        self.assertEqual(execute_tool("get_balance", "{", "1001")["code"], "INVALID_ARGUMENTS")
        self.assertEqual(execute_tool("get_balance", '{"account_id":"1002"}', "1001")["code"], "INVALID_ARGUMENTS")
        self.assertEqual(execute_tool("get_bills", '{"limit":true}', "1001")["code"], "INVALID_ARGUMENTS")

    def test_api_startup_validation_and_missing_key(self):
        with TestClient(app) as client:
            self.assertEqual(client.get("/health").json(), {"status": "ok", "llm_configured": False})
            self.assertEqual(client.get("/accounts/1001/balance").json()["balance"], "10000.00")
            self.assertEqual(client.get("/accounts/1001/bills?limit=1").status_code, 200)
            self.assertEqual(client.get("/accounts/missing/balance").status_code, 404)
            self.assertEqual(client.get("/accounts/1001/bills?limit=101").status_code, 422)
            self.assertEqual(client.post("/chat", json={"message": " "}).status_code, 422)
            missing_key = client.post("/chat", json={"message": "查询余额"})
            self.assertEqual(missing_key.status_code, 503)
            self.assertIn("DEEPSEEK_API_KEY", missing_key.json()["detail"])
            self.assertEqual(client.get("/openapi.json").status_code, 200)

    def test_chat_http_to_sdk_to_transfer(self):
        def handler(request):
            body = json.loads(request.content)
            self.assertIn("模拟转账", body["messages"][-1]["content"])
            self.assertEqual(str(request.url), "https://api.deepseek.com/beta/chat/completions")
            self.assertEqual(request.headers["Authorization"], "Bearer offline-test-key")
            self.assertEqual(body["model"], "deepseek-flash")
            self.assertEqual(body["thinking"], {"type": "disabled"})
            self.assertNotIn("parallel_tool_calls", body)
            for tool in body["tools"]:
                self.assertTrue(tool["function"]["strict"])
                self.assertNotIn("minLength", json.dumps(tool))
                self.assertNotIn("maxLength", json.dumps(tool))
            return httpx.Response(200, json=completion(
                "transfer_money", {"to_account_id": "1002", "amount": "1.25"},
            ))

        transport = httpx.MockTransport(handler)
        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "offline-test-key"}), patch(
            "main.DeepSeekHTTPClient", side_effect=lambda: DeepSeekHTTPClient(transport=transport),
        ):
            with TestClient(app) as client:
                response = client.post("/chat", json={"message": "模拟转账给1002，金额1.25元"})
                self.assertEqual(response.status_code, 200)
                data = response.json()
                self.assertEqual(data["status"], "completed")
                self.assertEqual(data["tool_results"][0]["result"]["amount"], "1.25")
                self.assertEqual(get_balance("1001")["balance"], "9998.75")
        # 兼容层没有修改 Agent 原有 schema 或校验规则。
        original = TOOLS[2]["function"]["parameters"]["properties"]["amount"]
        self.assertEqual(original["maxLength"], 13)

    def test_deepseek_default_and_custom_environment(self):
        for base_url, model, expected_url, expected_model in (
            ("", "", "https://api.deepseek.com/beta/", "deepseek-flash"),
            ("https://api.deepseek.com/beta", "deepseek-v4-pro",
             "https://api.deepseek.com/beta/", "deepseek-v4-pro"),
        ):
            with self.subTest(model=model), patch.dict(os.environ, {
                "DEEPSEEK_API_KEY": "offline-test-key",
                "DEEPSEEK_BASE_URL": base_url,
                "DEEPSEEK_MODEL": model,
            }), TestClient(app) as client:
                self.assertTrue(client.get("/health").json()["llm_configured"])
                self.assertEqual(str(app.state.agent.client.base_url), expected_url)
                self.assertEqual(app.state.agent.model, expected_model)

    def test_deepseek_query_roundtrip(self):
        responses = [completion("get_balance"), completion(content="余额999999元")]
        requests = []

        def handler(request):
            requests.append(json.loads(request.content))
            return httpx.Response(200, json=responses.pop(0))

        transport = httpx.MockTransport(handler)
        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "offline-test-key"}), patch(
            "main.DeepSeekHTTPClient", side_effect=lambda: DeepSeekHTTPClient(transport=transport),
        ), TestClient(app) as client:
            response = client.post("/chat", json={"message": "查询余额"})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["status"], "completed")
            self.assertIn("10000.00", response.json()["reply"])
            self.assertNotIn("999999", response.json()["reply"])
        self.assertEqual(len(requests), 2)
        tool_message = requests[1]["messages"][-1]
        self.assertIsNone(requests[1]["messages"][-2]["content"])
        self.assertEqual(tool_message["role"], "tool")
        self.assertEqual(json.loads(tool_message["content"])["balance"], "10000.00")


def completion(name=None, args=None, content=None, calls=None):
    message = {"role": "assistant", "content": content}
    if name:
        calls = [{"id": "call_test", "type": "function", "function": {
            "name": name, "arguments": json.dumps(args or {}),
        }}]
    if calls:
        message["tool_calls"] = calls
    return {
        "id": "chatcmpl-test", "object": "chat.completion", "created": 0, "model": "test-model",
        "choices": [{"index": 0, "message": message, "finish_reason": "tool_calls" if calls else "stop"}],
    }


class AgentTests(DatabaseFixture, unittest.IsolatedAsyncioTestCase):
    async def run_agent(self, responses, message="查询余额"):
        requests = []

        def handler(request):
            requests.append(json.loads(request.content))
            response = responses.pop(0)
            if isinstance(response, Exception):
                raise response
            return httpx.Response(200, json=response)

        async with AsyncOpenAI(
            api_key="offline-test-key", max_retries=0,
            http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        ) as client:
            result = await BankingAgent(client, "test-model").run(message, "1001")
        return result, requests

    async def test_tool_result_is_sent_back_and_model_financial_text_is_discarded(self):
        result, requests = await self.run_agent([
            completion("get_balance"), completion(content="你的余额是 999999 元。"),
        ])
        self.assertEqual(result["status"], "completed")
        self.assertIn("10000.00", result["reply"])
        self.assertNotIn("999999", result["reply"])
        tool_message = requests[1]["messages"][-1]
        self.assertEqual(tool_message["role"], "tool")
        self.assertEqual(tool_message["tool_call_id"], "call_test")
        self.assertEqual(json.loads(tool_message["content"])["balance"], "10000.00")
        self.assertFalse(requests[0]["parallel_tool_calls"])

    async def test_bill_tool(self):
        result, _ = await self.run_agent([completion("get_bills", {"limit": 3}), completion()])
        self.assertEqual(result["tool_results"][0]["result"]["bills"][0]["amount"], "10000.00")

    async def test_transfer_returns_after_one_call(self):
        result, requests = await self.run_agent([
            completion("transfer_money", {"to_account_id": "1002", "amount": "100.50"}),
        ], "给账户1002模拟转账100.50元")
        self.assertEqual(len(requests), 1)
        self.assertEqual(result["status"], "completed")
        self.assertIn("9899.50", result["reply"])
        self.assertEqual(len(get_bills("1001")["bills"]), 2)

    async def test_no_tool_never_returns_fabricated_data(self):
        result, _ = await self.run_agent([completion(content="转账成功，余额999999元。")])
        self.assertEqual(result["status"], "needs_input")
        self.assertEqual(result["reply"], NO_TOOL_REPLY)
        self.assertEqual(get_balance("1001")["balance"], "10000.00")

    async def test_business_error_stops_agent(self):
        result, requests = await self.run_agent([
            completion("transfer_money", {"to_account_id": "1002", "amount": "20000"}),
        ])
        self.assertEqual(result["status"], "failed")
        self.assertEqual(len(requests), 1)
        self.assertIn("余额不足", result["reply"])

    async def test_multiple_calls_do_not_execute_transfer(self):
        calls = completion("transfer_money", {"to_account_id": "1002", "amount": "1"})["choices"][0]["message"]["tool_calls"]
        result, _ = await self.run_agent([completion(calls=calls * 2)])
        self.assertEqual(result["status"], "failed")
        self.assertEqual(get_balance("1001")["balance"], "10000.00")

    async def test_network_failure_and_timeout(self):
        for error, status in (
            (httpx.ConnectError("offline"), 502), (httpx.ReadTimeout("offline"), 504),
        ):
            with self.subTest(status=status):
                with self.assertRaises(AgentError) as caught:
                    await self.run_agent([error])
                self.assertEqual(caught.exception.status_code, status)

    async def test_partial_query_survives_followup_failure(self):
        result, _ = await self.run_agent([completion("get_balance"), httpx.ConnectError("offline")])
        self.assertEqual(result["status"], "partial")
        self.assertIn("10000.00", result["reply"])

    async def test_call_limit(self):
        result, requests = await self.run_agent([completion("get_balance") for _ in range(6)])
        self.assertEqual(result["status"], "partial")
        self.assertEqual(len(requests), 6)


if __name__ == "__main__":
    unittest.main()
