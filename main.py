"""FastAPI 入口：加载配置、初始化数据库、提供自然语言入口。"""

import json
import os
from contextlib import asynccontextmanager
from functools import partial

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query, Request
from openai import AsyncOpenAI
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from agent.llm_agent import ARGUMENT_MODELS, AgentError, AgentPause, BankingAgent, execute_tool, render_result
from database.db import PROJECT_DIR, init_db
from deepseek_client import DeepSeekHTTPClient
from security import PendingActionManager
from tools.account import get_balance
from tools.bill import get_bills

load_dotenv(PROJECT_DIR / ".env")


def execute_saved_action(account_id: str, action: str, params: dict) -> dict:
    name = "transfer_money" if action == "transfer" else action
    return execute_tool(name, json.dumps(params, ensure_ascii=False), account_id)


def secure_execute_tool(name: str, raw_arguments: str, account_id: str, manager: PendingActionManager) -> dict:
    model = ARGUMENT_MODELS.get(name)
    if model is None:
        return execute_tool(name, raw_arguments, account_id)
    try:
        args = model.model_validate(json.loads(raw_arguments)).model_dump()
    except (ValueError, TypeError, ValidationError):
        return execute_tool(name, raw_arguments, account_id)
    response = manager.prepare_action(account_id, name, args)
    if response["status"] in {"pending", "require_verification", "execution_unknown"}:
        raise AgentPause(response)
    return response["result"]


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    api_key = os.getenv("DEEPSEEK_API_KEY", "").strip()
    base_url = os.getenv("DEEPSEEK_BASE_URL", "").strip() or "https://api.deepseek.com/beta"
    model = os.getenv("DEEPSEEK_MODEL", "").strip() or "deepseek-flash"
    client = AsyncOpenAI(
        api_key=api_key,
        base_url=base_url,
        http_client=DeepSeekHTTPClient(),
        timeout=30.0,
        max_retries=0,
    ) if api_key else None
    app.state.agent = BankingAgent(client, model) if client else None
    app.state.pending_actions = PendingActionManager(execute_saved_action)
    app.state.secure_agent = BankingAgent(
        client, model, tool_executor=partial(secure_execute_tool, manager=app.state.pending_actions),
    ) if client else None
    try:
        yield
    finally:
        if client:
            await client.close()


app = FastAPI(title="Banking Agent AI", version="1.0.0", lifespan=lifespan)


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    message: str = Field(min_length=1, max_length=2000)
    account_id: str = Field(default="1001", min_length=1, max_length=64)

    @field_validator("message", "account_id")
    @classmethod
    def reject_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("内容不能为空")
        return value


class ChatResponse(BaseModel):
    status: str
    reply: str
    tool_results: list[dict]


class SecureChatResponse(ChatResponse):
    message: str | None = None
    action_id: str | None = None
    risk_level: str | None = None
    pending_action: dict | None = None


class ActionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    account_id: str = Field(default="1001", min_length=1, max_length=64)
    action_id: str = Field(min_length=1, max_length=64)

    @field_validator("account_id", "action_id")
    @classmethod
    def reject_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("内容不能为空")
        return value.strip()


class ConfirmRequest(ActionRequest):
    confirmed: bool = True


@app.get("/health")
def health(request: Request):
    return {"status": "ok", "llm_configured": request.app.state.agent is not None}


@app.post("/chat", response_model=ChatResponse)
async def chat(body: ChatRequest, request: Request):
    agent = request.app.state.agent
    if agent is None:
        raise HTTPException(503, "请在 .env 中配置 DEEPSEEK_API_KEY，然后重启服务。")
    try:
        return await agent.run(body.message, body.account_id)
    except AgentError as exc:
        raise HTTPException(exc.status_code, str(exc)) from None


@app.post("/chat/secure", response_model=SecureChatResponse, response_model_exclude_none=True)
async def secure_chat(body: ChatRequest, request: Request):
    agent = request.app.state.secure_agent
    if agent is None:
        raise HTTPException(503, "请在 .env 中配置 DEEPSEEK_API_KEY，然后重启服务。")
    try:
        return await agent.run(body.message, body.account_id)
    except AgentError as exc:
        raise HTTPException(exc.status_code, str(exc)) from None


def action_response(response: dict) -> dict:
    if response["status"] == "not_found":
        raise HTTPException(404, detail=response)
    if "result" in response:
        name = "transfer_money" if response["action"] == "transfer" else response["action"]
        response["reply"] = render_result(name, response["result"])
    return response


@app.post("/confirm")
def confirm_action(body: ConfirmRequest, request: Request):
    return action_response(request.app.state.pending_actions.confirm_action(
        body.account_id, body.action_id, body.confirmed,
    ))


@app.post("/cancel")
def cancel_action(body: ActionRequest, request: Request):
    return action_response(request.app.state.pending_actions.cancel_action(body.account_id, body.action_id))


def checked_result(result: dict) -> dict:
    if result["ok"]:
        return result
    status = 404 if result["code"] == "ACCOUNT_NOT_FOUND" else 503
    raise HTTPException(status, detail=result)


@app.get("/accounts/{account_id}/balance")
def balance(account_id: str):
    return checked_result(get_balance(account_id))


@app.get("/accounts/{account_id}/bills")
def bills(account_id: str, limit: int = Query(default=10, ge=1, le=100)):
    return checked_result(get_bills(account_id, limit))
