"""持久化审计和真实工具确认链；仅使用模拟 SDK 与临时 SQLite。"""

import json
import os
import subprocess
import sys
import unittest
from contextlib import contextmanager
from datetime import datetime
from unittest.mock import Mock, patch

import httpx
from fastapi.testclient import TestClient

from database.db import connection, init_db
from deepseek_client import DeepSeekHTTPClient
from main import app
from security import AuditLogger, PendingActionManager
from security.audit import AUDIT_WARNING, AuditError
from tests.test_banking import DatabaseFixture, completion, confirmed_test_transfer
from tools.account import get_balance

REQUIRED = {"timestamp", "account_id", "user_request", "operation", "risk_level",
            "permission_action", "confirmation_status", "execution_result"}


class AuditLoggerTests(DatabaseFixture, unittest.TestCase):
    def test_create_record_and_query_history(self):
        logger = AuditLogger()
        with logger.request_context("1001", "查询余额", "get_balance") as context:
            event_id = logger.log_event("tool_executed", context, risk_level="green", permission_action="allow",
                                        execution_result="success", details={"balance": "10000.00"})
        records = logger.query("1001")
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["id"], event_id)
        self.assertTrue(REQUIRED <= records[0].keys())
        self.assertEqual(records[0]["user_request"], "查询余额")
        self.assertEqual(records[0]["execution_result"], "success")
        self.assertEqual(records[0]["details"]["balance"], "10000.00")
        self.assertIsNotNone(datetime.fromisoformat(records[0]["timestamp"].replace("Z", "+00:00")).tzinfo)

    def test_log_can_generate_request_id_without_http_context(self):
        logger = AuditLogger()
        logger.log_event("request_received", {"account_id": "1001", "user_request": "查询余额"})
        record = logger.query("1001")[0]
        self.assertEqual(len(record["request_id"]), 32)
        self.assertTrue(REQUIRED <= record.keys())

    def test_queries_are_account_scoped_and_parameterized(self):
        logger = AuditLogger()
        for account in ("1001", "1002"):
            logger.log_event("request_received", logger.context(account))
        self.assertEqual([row["account_id"] for row in logger.query("1001")], ["1001"])
        self.assertEqual(logger.query("1001' OR 1=1 --"), [])

    def test_pagination_and_request_filter(self):
        logger = AuditLogger()
        with logger.request_context("1001", "查询") as first:
            for _ in range(3):
                logger.log_event("request_received", first)
        with logger.request_context("1001", "再次查询") as second:
            logger.log_event("request_received", second)
        filtered = logger.query("1001", request_id=first["request_id"])
        self.assertEqual(len(filtered), 3)
        self.assertEqual(logger.query("1001", limit=1, offset=1)[0]["id"], filtered[0]["id"])

    def test_logs_survive_new_process_and_database_reinitialization(self):
        logger = AuditLogger()
        logger.log_event("request_received", logger.context("1001"))
        init_db()
        self.assertEqual(len(AuditLogger().query("1001")), 1)
        result = subprocess.run(
            [sys.executable, "-c", "from security import AuditLogger; print(len(AuditLogger().query('1001')))"],
            capture_output=True, text=True, check=True, timeout=15,
        )
        self.assertEqual(result.stdout.strip(), "1")
        self.assertEqual(get_balance("1001")["balance"], "10000.00")

    def test_existing_accounts_and_bills_are_preserved(self):
        confirmed_test_transfer("1001", "1002", "0.01")
        with connection() as conn:
            conn.execute("DROP TABLE audit_logs")
        init_db()
        self.assertEqual(get_balance("1001")["balance"], "9999.99")
        with connection() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM bills WHERE account_id='1001'").fetchone()[0], 2)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM audit_logs").fetchone()[0], 0)

    def test_invalid_query_parameters(self):
        logger = AuditLogger()
        for limit, offset in ((0, 0), (501, 0), (True, 0), (10, -1), (10, True)):
            with self.subTest(limit=limit, offset=offset):
                with self.assertRaises(ValueError):
                    logger.query("1001", limit, offset)

    def test_optional_audit_does_not_require_database_for_plain_manager(self):
        executor = Mock(return_value={"ok": True})
        manager = PendingActionManager(executor)
        pending = manager.prepare_action("1001", "transfer", {"amount": "500"})
        self.assertEqual(manager.confirm_action("1001", pending["action_id"])["status"], "completed")
        self.assertEqual(AuditLogger().query("1001"), [])


class AuditAPITests(DatabaseFixture, unittest.TestCase):
    @contextmanager
    def sdk_client(self, responses):
        requests = []

        def handler(request):
            requests.append(json.loads(request.content))
            response = responses.pop(0)
            if isinstance(response, Exception):
                raise response
            return httpx.Response(200, json=response)

        transport = httpx.MockTransport(handler)
        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "offline-test-key"}), patch(
            "main.DeepSeekHTTPClient", side_effect=lambda: DeepSeekHTTPClient(transport=transport),
        ), TestClient(app) as client:
            yield client, requests

    def transfer_response(self, amount="500", recipient="1002"):
        return completion("transfer_money", {"to_account_id": recipient, "amount": amount})

    def test_confirmation_produces_full_persistent_decision_chain(self):
        message = "给账户1002模拟转账500元"
        with self.sdk_client([self.transfer_response()]) as (client, requests):
            pending = client.post("/chat/secure", json={"message": message}).json()
            self.assertEqual(pending["status"], "pending")
            self.assertNotIn("_audit_context", pending["pending_action"])
            result = client.post("/confirm", json={"action_id": pending["action_id"]}).json()
            self.assertEqual(result["status"], "completed")
            self.assertEqual(result["request_id"], pending["request_id"])
            rows = client.get(f"/audit/1001?request_id={pending['request_id']}").json()["logs"]
            events = [row["event_type"] for row in reversed(rows)]
            for event in ("request_received", "risk_evaluated", "permission_decided", "pending_created",
                          "confirmation_confirmed", "tool_execution_started", "tool_executed"):
                self.assertIn(event, events)
            self.assertLess(events.index("risk_evaluated"), events.index("permission_decided"))
            self.assertLess(events.index("confirmation_confirmed"), events.index("tool_execution_started"))
            executed = next(row for row in rows if row["event_type"] == "tool_executed")
            self.assertEqual(executed["user_request"], message)
            self.assertEqual(executed["operation"], "transfer_money")
            self.assertEqual(executed["risk_level"], "yellow")
            self.assertEqual(executed["permission_action"], "pending_confirmation")
            self.assertEqual(executed["confirmation_status"], "confirmed")
            self.assertEqual(executed["execution_result"], "success")
            self.assertEqual(executed["action_id"], pending["action_id"])
            self.assertEqual(executed["details"]["result"]["transfer_id"], result["result"]["transfer_id"])
            self.assertTrue(all(REQUIRED <= row.keys() for row in rows))
            self.assertEqual(len(requests), 1)

    def test_cancel_is_logged_without_tool_execution(self):
        with self.sdk_client([self.transfer_response()]) as (client, _):
            pending = client.post("/chat/secure", json={"message": "给1002转500元"}).json()
            result = client.post("/cancel", json={"action_id": pending["action_id"]}).json()
            self.assertEqual(result["status"], "cancelled")
            rows = client.get(f"/audit/1001?request_id={pending['request_id']}").json()["logs"]
            cancelled = next(row for row in rows if row["event_type"] == "confirmation_cancelled")
            self.assertEqual(cancelled["confirmation_status"], "cancelled")
            self.assertEqual(cancelled["execution_result"], "not_executed")
            self.assertNotIn("tool_executed", [row["event_type"] for row in rows])
            self.assertEqual(get_balance("1001")["balance"], "10000.00")

    def test_failed_tool_result_is_logged(self):
        with self.sdk_client([self.transfer_response(recipient="missing")]) as (client, _):
            pending = client.post("/chat/secure", json={"message": "给账户missing转500元"}).json()
            result = client.post("/confirm", json={"action_id": pending["action_id"]}).json()
            self.assertEqual(result["status"], "failed")
            row = next(item for item in AuditLogger().query("1001") if item["event_type"] == "tool_executed")
            self.assertEqual(row["execution_result"], "failed")
            self.assertEqual(row["details"]["result"]["code"], "ACCOUNT_NOT_FOUND")

    def test_red_risk_and_verification_policy_are_logged_without_execution(self):
        with self.sdk_client([self.transfer_response("5000")]) as (client, _):
            response = client.post("/chat/secure", json={"message": "给1002转5000元"}).json()
            self.assertEqual(response["status"], "require_verification")
            rows = AuditLogger().query("1001", request_id=response["request_id"])
            decision = next(row for row in rows if row["event_type"] == "permission_decided")
            self.assertEqual(decision["risk_level"], "red")
            self.assertEqual(decision["permission_action"], "require_verification")
            self.assertEqual(decision["confirmation_status"], "verification_required")
            self.assertNotIn("tool_executed", [row["event_type"] for row in rows])

    def test_legacy_write_is_logged_as_blocked_without_execution(self):
        with self.sdk_client([self.transfer_response("5000")]) as (client, _):
            response = client.post("/chat", json={"message": "给1002转5000元"}).json()
            self.assertEqual(response["status"], "failed")
            rows = AuditLogger().query("1001")
            row = next(item for item in rows if item["event_type"] == "legacy_write_blocked")
            self.assertEqual(row["risk_level"], "red")
            self.assertEqual(row["permission_action"], "require_verification")
            self.assertEqual(row["confirmation_status"], "blocked")
            self.assertEqual(row["execution_result"], "not_executed")
            self.assertNotIn("tool_executed", [item["event_type"] for item in rows])
            self.assertEqual(get_balance("1001")["balance"], "10000.00")

    def test_green_queries_and_audit_query_validation(self):
        with TestClient(app) as client:
            self.assertEqual(client.get("/accounts/1001/balance").status_code, 200)
            self.assertEqual(client.get("/accounts/1001/bills").status_code, 200)
            rows = client.get("/audit/1001").json()["logs"]
            executed = [row for row in rows if row["event_type"] == "tool_executed"]
            self.assertEqual(len(executed), 2)
            self.assertTrue(all(row["risk_level"] == "green" and row["permission_action"] == "allow" for row in executed))
            self.assertEqual(client.get("/audit/1002").json()["logs"], [])
            self.assertEqual(client.get("/audit/1001?limit=0").status_code, 422)
            self.assertEqual(client.get("/audit/1001?offset=-1").status_code, 422)
            self.assertEqual(client.get("/audit/1001?request_id=").status_code, 422)

    def test_service_restart_preserves_logs_but_not_memory_pending_actions(self):
        with self.sdk_client([self.transfer_response()]) as (client, _):
            pending = client.post("/chat/secure", json={"message": "给1002转500元"}).json()
            before = client.get("/audit/1001").json()["logs"]
        with TestClient(app) as restarted:
            after = restarted.get("/audit/1001").json()["logs"]
            self.assertEqual(after, before)
            self.assertEqual(restarted.post("/confirm", json={"action_id": pending["action_id"]}).status_code, 404)

    def test_llm_failure_still_records_the_initial_request(self):
        with self.sdk_client([httpx.ConnectError("offline")]) as (client, _):
            self.assertEqual(client.post("/chat/secure", json={"message": "查余额"}).status_code, 502)
            events = [row["event_type"] for row in AuditLogger().query("1001")]
            self.assertIn("request_received", events)
            self.assertIn("request_failed", events)

    def test_missing_key_is_logged_without_any_llm_call(self):
        with TestClient(app) as client:
            self.assertEqual(client.post("/chat", json={"message": "查余额"}).status_code, 503)
            events = [row["event_type"] for row in AuditLogger().query("1001")]
            self.assertEqual(events, ["request_failed", "request_received"])

    def test_pre_execution_audit_failure_preserves_pending_and_does_not_transfer(self):
        with TestClient(app) as client:
            pending = app.state.pending_actions.prepare_action("1001", "transfer", {"to_account_id": "1002", "amount": "500"})
            original = app.state.audit_logger.log_event

            def fail_started(event_type, *args, **kwargs):
                if event_type == "tool_execution_started":
                    raise AuditError("审计数据库暂时不可用。")
                return original(event_type, *args, **kwargs)

            with patch.object(app.state.audit_logger, "log_event", side_effect=fail_started):
                response = client.post("/confirm", json={"action_id": pending["action_id"]})
            self.assertEqual(response.status_code, 503)
            self.assertEqual(get_balance("1001")["balance"], "10000.00")
            self.assertIsNotNone(app.state.pending_actions.get_pending_action("1001", pending["action_id"]))

    def test_post_execution_audit_failure_does_not_report_transfer_failure_or_repeat(self):
        with TestClient(app) as client:
            pending = app.state.pending_actions.prepare_action("1001", "transfer", {"to_account_id": "1002", "amount": "500"})
            with connection() as conn:
                conn.execute("""CREATE TRIGGER fail_audit AFTER INSERT ON audit_logs
                    WHEN NEW.event_type = 'tool_executed'
                    BEGIN SELECT RAISE(ABORT, 'test failure'); END""")
            with self.assertLogs("security.audit", level="ERROR"):
                response = client.post("/confirm", json={"action_id": pending["action_id"]}).json()
            self.assertEqual(response["status"], "completed")
            self.assertEqual(response["audit_warning"], AUDIT_WARNING)
            self.assertEqual(get_balance("1001")["balance"], "9500.00")
            self.assertEqual(client.post("/confirm", json={"action_id": pending["action_id"]}).status_code, 404)


if __name__ == "__main__":
    unittest.main()
