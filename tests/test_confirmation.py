"""确认流程单元及 HTTP/SDK/SQLite 集成测试，全部使用临时数据。"""

import json
import os
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from threading import Event
from unittest.mock import Mock, patch

import httpx
from fastapi.testclient import TestClient

from deepseek_client import DeepSeekHTTPClient
from main import app
from security import PendingActionManager
from tests.test_banking import DatabaseFixture, completion
from tools.account import get_balance
from tools.bill import get_bills


class PendingActionTests(unittest.TestCase):
    def setUp(self):
        self.executor = Mock(return_value={"ok": True, "transfer_id": "offline-id"})
        self.manager = PendingActionManager(self.executor)
        self.params = {"to_account_id": "1002", "amount": "500"}

    def create_pending(self):
        return self.manager.prepare_action("1001", "transfer_money", self.params)

    def test_create_yellow_transfer_saves_pending_without_execution(self):
        response = self.create_pending()
        self.assertEqual(response["status"], "pending")
        record = self.manager.get_pending_action("1001", response["action_id"])
        self.assertEqual(record["account_id"], "1001")
        self.assertEqual(record["action"], "transfer_money")
        self.assertEqual(record["params"], self.params)
        self.assertEqual(record["risk_level"], "yellow")
        self.assertIsNotNone(datetime.fromisoformat(record["created_at"]).tzinfo)
        self.executor.assert_not_called()

    def test_confirm_executes_and_clears_pending(self):
        action_id = self.create_pending()["action_id"]
        result = self.manager.confirm_action("1001", action_id)
        self.assertEqual(result["status"], "completed")
        self.executor.assert_called_once_with("1001", "transfer_money", self.params)
        self.assertIsNone(self.manager.get_pending_action("1001", action_id))

    def test_cancel_deletes_without_execution(self):
        action_id = self.create_pending()["action_id"]
        self.assertEqual(self.manager.cancel_action("1001", action_id)["status"], "cancelled")
        self.assertIsNone(self.manager.get_pending_action("1001", action_id))
        self.assertEqual(self.manager.confirm_action("1001", action_id)["status"], "not_found")
        self.executor.assert_not_called()

    def test_explicit_false_cancels(self):
        action_id = self.create_pending()["action_id"]
        self.assertEqual(self.manager.confirm_action("1001", action_id, False)["status"], "cancelled")
        self.executor.assert_not_called()

    def test_repeated_confirmation_does_not_execute_twice(self):
        action_id = self.create_pending()["action_id"]
        self.manager.confirm_action("1001", action_id)
        self.assertEqual(self.manager.confirm_action("1001", action_id)["status"], "not_found")
        self.executor.assert_called_once()

    def test_wrong_account_cannot_read_confirm_or_cancel(self):
        action_id = self.create_pending()["action_id"]
        self.assertIsNone(self.manager.get_pending_action("1002", action_id))
        self.assertEqual(self.manager.confirm_action("1002", action_id)["status"], "not_found")
        self.assertEqual(self.manager.cancel_action("1002", action_id)["status"], "not_found")
        self.assertIsNotNone(self.manager.get_pending_action("1001", action_id))
        self.executor.assert_not_called()

    def test_copies_cannot_change_saved_operation(self):
        response = self.create_pending()
        action_id = response["action_id"]
        self.params["amount"] = "9000"
        response["pending_action"]["params"]["amount"] = "8000"
        fetched = self.manager.get_pending_action("1001", action_id)
        fetched["params"]["to_account_id"] = "wrong"
        self.manager.confirm_action("1001", action_id)
        self.executor.assert_called_once_with("1001", "transfer_money", {
            "to_account_id": "1002", "amount": "500",
        })

    def test_red_or_unknown_operations_never_enter_confirmation(self):
        for action, params in (("transfer", {"amount": "5000"}), ("unknown", {}), ("transfer", {})):
            with self.subTest(action=action, params=params):
                response = self.manager.prepare_action("1001", action, params)
                self.assertEqual(response["status"], "require_verification")
                self.assertNotIn("action_id", response)
        with self.assertRaises(ValueError):
            self.manager.create_pending_action("1001", "transfer", {"amount": "5000"}, "yellow")
        self.executor.assert_not_called()

    def test_green_query_executes_immediately(self):
        self.assertEqual(self.manager.prepare_action("1001", "get_balance", {})["status"], "completed")
        self.executor.assert_called_once_with("1001", "get_balance", {})

    def test_risk_is_bound_to_saved_parameter_snapshot(self):
        evaluate = self.manager._risk_engine.evaluate

        def change_original(action, amount):
            self.params["amount"] = "5000"
            return evaluate(action, amount)

        with patch.object(self.manager._risk_engine, "evaluate", side_effect=change_original):
            record = self.manager.create_pending_action("1001", "transfer_money", self.params, "yellow")
        self.assertEqual(record["params"]["amount"], "500")
        self.executor.assert_not_called()

    def test_invalid_create_inputs(self):
        for account_id, action, params, level in (
            ("", "transfer", self.params, "yellow"),
            ("1001", "", self.params, "yellow"),
            ("1001", "transfer", [], "yellow"),
            ("1001", "transfer", self.params, "red"),
        ):
            with self.subTest(account_id=account_id, action=action):
                with self.assertRaises(ValueError):
                    self.manager.create_pending_action(account_id, action, params, level)
        action_id = self.create_pending()["action_id"]
        with self.assertRaises(ValueError):
            self.manager.confirm_action("1001", action_id, "true")
        self.assertIsNotNone(self.manager.get_pending_action("1001", action_id))
        self.executor.assert_not_called()

    def test_tool_failure_consumes_action_without_retry(self):
        self.executor.return_value = {"ok": False, "code": "INSUFFICIENT_FUNDS"}
        action_id = self.create_pending()["action_id"]
        self.assertEqual(self.manager.confirm_action("1001", action_id)["status"], "failed")
        self.assertEqual(self.manager.confirm_action("1001", action_id)["status"], "not_found")
        self.executor.assert_called_once()

    def test_unexpected_executor_error_does_not_repeat_or_leak_details(self):
        self.executor.side_effect = RuntimeError("private diagnostic")
        action_id = self.create_pending()["action_id"]
        result = self.manager.confirm_action("1001", action_id)
        self.assertEqual(result["status"], "execution_unknown")
        self.assertNotIn("private diagnostic", json.dumps(result))
        self.assertEqual(self.manager.confirm_action("1001", action_id)["status"], "not_found")
        self.executor.assert_called_once()

    def test_concurrent_confirmation_only_executes_once(self):
        action_id = self.create_pending()["action_id"]
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: self.manager.confirm_action("1001", action_id), range(2)))
        self.assertEqual(sorted(result["status"] for result in results), ["completed", "not_found"])
        self.executor.assert_called_once()

    def test_cancel_after_execution_claim_cannot_execute_or_cancel_again(self):
        started, release = Event(), Event()

        def executor(*args):
            started.set()
            if not release.wait(5):
                raise RuntimeError("test timeout")
            return {"ok": True}

        self.executor.side_effect = executor
        action_id = self.create_pending()["action_id"]
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(self.manager.confirm_action, "1001", action_id)
            try:
                self.assertTrue(started.wait(5))
                self.assertEqual(self.manager.cancel_action("1001", action_id)["status"], "not_found")
            finally:
                release.set()
            self.assertEqual(future.result(timeout=5)["status"], "completed")
        self.executor.assert_called_once()

    def test_multiple_actions_are_independent(self):
        first = self.create_pending()["action_id"]
        second = self.create_pending()["action_id"]
        self.assertNotEqual(first, second)
        self.manager.cancel_action("1001", first)
        self.assertIsNotNone(self.manager.get_pending_action("1001", second))
        self.manager.confirm_action("1001", second)
        self.executor.assert_called_once()


class ConfirmationAPITests(DatabaseFixture, unittest.TestCase):
    def run_chat(self, amount, callback):
        calls = []

        def handler(request):
            body = json.loads(request.content)
            calls.append(body)
            self.assertNotIn("confirm_action", [tool["function"]["name"] for tool in body["tools"]])
            return httpx.Response(200, json=completion("transfer_money", {
                "to_account_id": "1002", "amount": amount,
            }))

        transport = httpx.MockTransport(handler)
        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "offline-test-key"}), patch(
            "main.DeepSeekHTTPClient", side_effect=lambda: DeepSeekHTTPClient(transport=transport),
        ), TestClient(app) as client:
            response = client.post("/chat/secure", json={"message": f"给账户1002转{amount}元"})
            self.assertEqual(response.status_code, 200)
            callback(client, response.json(), calls)

    def test_secure_yellow_then_confirm_runs_original_transfer_once(self):
        def check(client, response, calls):
            self.assertEqual(response["status"], "pending")
            self.assertEqual(get_balance("1001")["balance"], "10000.00")
            self.assertEqual(len(get_bills("1001")["bills"]), 1)
            body = {"account_id": "1001", "action_id": response["action_id"]}
            confirmed = client.post("/confirm", json=body)
            self.assertEqual(confirmed.status_code, 200)
            self.assertEqual(confirmed.json()["status"], "completed")
            self.assertEqual(confirmed.json()["result"]["amount"], "500.00")
            self.assertIn("9500.00", confirmed.json()["reply"])
            self.assertEqual(get_balance("1001")["balance"], "9500.00")
            self.assertEqual(get_balance("1002")["balance"], "5500.00")
            self.assertEqual(len(get_bills("1001")["bills"]), 2)
            self.assertEqual(client.post("/confirm", json=body).status_code, 404)
            self.assertEqual(len(calls), 1)  # 确认不调用 LLM。
        self.run_chat("500", check)

    def test_secure_yellow_cancel_does_not_transfer(self):
        def check(client, response, calls):
            body = {"action_id": response["action_id"]}
            self.assertEqual(client.post("/cancel", json=body).json()["status"], "cancelled")
            self.assertEqual(client.post("/confirm", json=body).status_code, 404)
            self.assertEqual(get_balance("1001")["balance"], "10000.00")
            self.assertEqual(len(get_bills("1001")["bills"]), 1)
            self.assertEqual(len(calls), 1)
        self.run_chat("500", check)

    def test_secure_red_never_creates_pending_or_transfers(self):
        def check(client, response, calls):
            self.assertEqual(response["status"], "require_verification")
            self.assertNotIn("action_id", response)
            self.assertEqual(get_balance("1001")["balance"], "10000.00")
            self.assertEqual(client.post("/confirm", json={"action_id": "unknown"}).status_code, 404)
            self.assertEqual(len(calls), 1)
        self.run_chat("5000", check)

    def test_http_owner_and_parameter_validation(self):
        with TestClient(app) as client:
            pending = app.state.pending_actions.prepare_action("1001", "transfer", {
                "to_account_id": "1002", "amount": "500",
            })
            action_id = pending["action_id"]
            wrong = {"account_id": "1002", "action_id": action_id}
            self.assertEqual(client.post("/confirm", json=wrong).status_code, 404)
            self.assertEqual(client.post("/cancel", json=wrong).status_code, 404)
            self.assertEqual(client.post("/confirm", json={"action_id": action_id, "confirmed": "true"}).status_code, 422)
            self.assertEqual(client.post("/confirm", json={"action_id": " "}).status_code, 422)
            self.assertEqual(client.post("/confirm", json={"action_id": action_id, "confirmed": False}).json()["status"], "cancelled")
            self.assertEqual(get_balance("1001")["balance"], "10000.00")
            self.assertEqual(client.post("/chat/secure", json={"message": "查询余额"}).status_code, 503)

    def test_confirmation_returns_original_tool_business_error(self):
        with TestClient(app) as client:
            pending = app.state.pending_actions.prepare_action("1001", "transfer", {
                "to_account_id": "missing", "amount": "500",
            })
            response = client.post("/confirm", json={"action_id": pending["action_id"]})
            self.assertEqual(response.json()["status"], "failed")
            self.assertEqual(response.json()["result"]["code"], "ACCOUNT_NOT_FOUND")
            self.assertEqual(get_balance("1001")["balance"], "10000.00")
            self.assertEqual(client.post("/confirm", json={"action_id": pending["action_id"]}).status_code, 404)

    def test_secure_green_queries_continue_tool_calling_loop(self):
        responses = [completion("get_balance"), completion("get_bills", {"limit": 1}), completion()]
        requests = []

        def handler(request):
            requests.append(json.loads(request.content))
            return httpx.Response(200, json=responses.pop(0))

        transport = httpx.MockTransport(handler)
        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "offline-test-key"}), patch(
            "main.DeepSeekHTTPClient", side_effect=lambda: DeepSeekHTTPClient(transport=transport),
        ), TestClient(app) as client:
            result = client.post("/chat/secure", json={"message": "查余额和账单"}).json()
            self.assertEqual(result["status"], "completed")
            self.assertEqual([item["tool"] for item in result["tool_results"]], ["get_balance", "get_bills"])
            self.assertEqual(result["tool_results"][0]["result"]["balance"], "10000.00")
            self.assertEqual(len(requests), 3)

    def test_llm_cannot_call_confirmation_tool(self):
        action_id = None

        def handler(request):
            return httpx.Response(200, json=completion("confirm_action", {"action_id": action_id}))

        transport = httpx.MockTransport(handler)
        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "offline-test-key"}), patch(
            "main.DeepSeekHTTPClient", side_effect=lambda: DeepSeekHTTPClient(transport=transport),
        ), TestClient(app) as client:
            pending = app.state.pending_actions.prepare_action("1001", "transfer", {
                "to_account_id": "1002", "amount": "500",
            })
            action_id = pending["action_id"]
            result = client.post("/chat/secure", json={"message": "我确认"}).json()
            self.assertEqual(result["status"], "failed")
            self.assertEqual(result["tool_results"][0]["result"]["code"], "UNKNOWN_TOOL")
            self.assertIsNotNone(app.state.pending_actions.get_pending_action("1001", action_id))
            self.assertEqual(get_balance("1001")["balance"], "10000.00")

    def test_secure_invalid_tool_arguments_never_create_pending(self):
        def handler(request):
            return httpx.Response(200, json=completion("transfer_money", {
                "to_account_id": "1002", "amount": 500, "confirmed": True,
            }))

        transport = httpx.MockTransport(handler)
        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "offline-test-key"}), patch(
            "main.DeepSeekHTTPClient", side_effect=lambda: DeepSeekHTTPClient(transport=transport),
        ), TestClient(app) as client:
            result = client.post("/chat/secure", json={"message": "给1002转500元"}).json()
            self.assertEqual(result["status"], "failed")
            self.assertEqual(result["tool_results"][0]["result"]["code"], "INVALID_ARGUMENTS")
            self.assertNotIn("action_id", result)
            self.assertEqual(get_balance("1001")["balance"], "10000.00")


if __name__ == "__main__":
    unittest.main()
