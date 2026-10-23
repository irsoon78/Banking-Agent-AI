"""真实事件循环/HTTP集成：在本机完整回归中执行，不用于沙箱同步子集。"""

from contextlib import asynccontextmanager
import unittest

from fastapi import FastAPI
from fastapi.testclient import TestClient

import main
from agent.llm_agent import BankingAgent
from api.responses import install_error_handlers
from tests.test_card_integration import CardFixture
from tests.test_investigation_planning import Model


class InvestigationHTTPTests(CardFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        @asynccontextmanager
        async def lifespan(app):
            app.state.audit_logger = self.logger
            app.state.sessions = self.sessions
            app.state.pending_actions = self.manager
            app.state.secure_agent = BankingAgent(Model(), "mock")
            yield
        # 复用真实路由和响应模型，使用隔离lifespan，不读取Key或初始化演示库。
        self.app = FastAPI(lifespan=lifespan)
        install_error_handlers(self.app)
        # 复用路由对象而非include_router，避免合并main的真实启动lifespan。
        self.app.router.routes.extend(main.app.router.routes)

    def test_chat_response_model_and_refresh_recovery(self):
        with TestClient(self.app) as client:
            response = client.post("/chat/secure", json={"message": "帮我调查2026年10月异常消费", "account_id": "1001"})
            self.assertEqual(response.status_code, 200, response.text)
            body = response.json()
            self.assertEqual(body["execution_status"], "completed")
            self.assertEqual(body["coverage_status"], "incomplete")
            detail = client.get("/accounts/1001/investigations/" + body["investigation_id"])
            self.assertEqual(detail.status_code, 200, detail.text)
            self.assertEqual(detail.json()["investigation"]["steps"], body["investigation"]["steps"])
            self.assertEqual(client.get("/accounts/1001/investigations").json()["total"], 1)

    def test_foreign_account_and_invalid_pagination_rejected(self):
        with TestClient(self.app) as client:
            body = client.post("/chat/secure", json={"message": "调查2026年10月相似消费"}).json()
            self.assertEqual(client.get("/accounts/1002/investigations/" + body["investigation_id"]).status_code, 404)
            self.assertEqual(client.get("/accounts/1001/investigations?limit=21").status_code, 422)

    def test_client_recreation_can_read_completed_report_without_model_calls(self):
        with TestClient(self.app) as client:
            body = client.post("/chat/secure", json={"message": "调查2026年10月相似消费"}).json()
        with TestClient(self.app) as client:
            self.app.state.secure_agent = None
            response = client.get("/accounts/1001/investigations/" + body["investigation_id"])
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()["investigation"]["execution_status"], "completed")
