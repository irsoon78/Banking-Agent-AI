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
from security import AuditLogger, PendingActionManager, PermissionController, RiskEngine
from security.audit import AUDIT_WARNING, AuditError

load_dotenv(PROJECT_DIR / ".env")


def execute_saved_action(account_id: str, action: str, params: dict) -> dict:
    name = "transfer_money" if action == "transfer" else action
    return execute_tool(name, json.dumps(params, ensure_ascii=False), account_id)


def audited_execute_tool(name: str, raw_arguments: str, account_id: str, audit_logger: AuditLogger) -> dict:
    """兼容入口保留直接执行行为，同时明确记录未应用确认的事实。"""
    model = ARGUMENT_MODELS.get(name)
    args = {}
    valid = False
    if model:
        try:
            args = model.model_validate(json.loads(raw_arguments)).model_dump()
            valid = True
        except (ValueError, TypeError, ValidationError):
            pass
    context = audit_logger.context(account_id, name)
    risk = RiskEngine().evaluate(name, args.get("amount"))
    context["risk_level"] = risk["risk_level"]
    audit_logger.log_event("risk_evaluated", context, details={"reason": risk["reason"]})
    permission = PermissionController().decide(name, risk["risk_level"])
    context["permission_action"] = permission["action"]
    context["confirmation_status"] = "legacy_bypass" if valid and permission["action"] != "allow" else "not_required"
    audit_logger.log_event("permission_decided", context, details={**permission, "mode": "legacy"})
    audit_logger.log_event("tool_execution_started" if valid else "tool_validation_failed", context,
                           execution_result="started" if valid else "not_executed")
    try:
        result = execute_tool(name, raw_arguments, account_id)
    except Exception:
        audit_logger.log_after_execution("tool_executed", context, execution_result="unknown")
        raise
    if not audit_logger.log_after_execution(
        "tool_executed", context, execution_result="success" if result["ok"] else "failed", details={"result": result},
    ):
        result["audit_warning"] = AUDIT_WARNING
    return result


def secure_execute_tool(name: str, raw_arguments: str, account_id: str, manager: PendingActionManager,
                        audit_logger: AuditLogger | None = None) -> dict:
    model = ARGUMENT_MODELS.get(name)
    if model is None:
        return audited_execute_tool(name, raw_arguments, account_id, audit_logger) if audit_logger else execute_tool(name, raw_arguments, account_id)
    try:
        args = model.model_validate(json.loads(raw_arguments)).model_dump()
    except (ValueError, TypeError, ValidationError):
        return audited_execute_tool(name, raw_arguments, account_id, audit_logger) if audit_logger else execute_tool(name, raw_arguments, account_id)
    response = manager.prepare_action(account_id, name, args)
    if response["status"] in {"pending", "require_verification", "execution_unknown"}:
        raise AgentPause(response)
    result = response["result"]
    if "audit_warning" in response:
        result["audit_warning"] = response["audit_warning"]
    return result


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
    app.state.audit_logger = AuditLogger()
    app.state.agent = BankingAgent(
        client, model, tool_executor=partial(audited_execute_tool, audit_logger=app.state.audit_logger),
    ) if client else None
    app.state.pending_actions = PendingActionManager(execute_saved_action, audit_logger=app.state.audit_logger)
    app.state.secure_agent = BankingAgent(
        client, model, tool_executor=partial(secure_execute_tool, manager=app.state.pending_actions,
                                             audit_logger=app.state.audit_logger),
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
    request_id: str | None = None
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
    return await run_audited_chat(body, request, secure=False)


@app.post("/chat/secure", response_model=SecureChatResponse, response_model_exclude_none=True)
async def secure_chat(body: ChatRequest, request: Request):
    return await run_audited_chat(body, request, secure=True)


def finish_audit(logger: AuditLogger, context: dict, response: dict) -> None:
    response.setdefault("request_id", context["request_id"])
    logged = logger.log_after_execution(
        "request_returned", context, execution_result="not_applicable",
        details={"status": response.get("status")},
    )
    tool_warning = any(item["result"].get("audit_warning") for item in response.get("tool_results", []))
    if not logged or tool_warning or response.get("audit_warning"):
        response["audit_warning"] = AUDIT_WARNING
        field = "reply" if "reply" in response else "message"
        response[field] = response.get(field, "") + "\n" + AUDIT_WARNING


async def run_audited_chat(body: ChatRequest, request: Request, secure: bool):
    logger = request.app.state.audit_logger
    try:
        with logger.request_context(body.account_id, body.message) as context:
            logger.log_event("request_received", context, details={"entrypoint": "/chat/secure" if secure else "/chat"})
            agent = request.app.state.secure_agent if secure else request.app.state.agent
            if agent is None:
                logger.log_after_execution("request_failed", context, details={"code": "LLM_NOT_CONFIGURED"})
                raise HTTPException(503, "请在 .env 中配置 DEEPSEEK_API_KEY，然后重启服务。")
            try:
                response = await agent.run(body.message, body.account_id)
            except AgentError as exc:
                logger.log_after_execution("request_failed", context, details={"http_status": exc.status_code})
                raise HTTPException(exc.status_code, str(exc)) from None
            except Exception:
                logger.log_after_execution("request_failed", context, execution_result="unknown")
                raise
            finish_audit(logger, context, response)
            return response
    except AuditError as exc:
        raise HTTPException(503, str(exc)) from None


def action_response(response: dict) -> dict:
    if response["status"] == "not_found":
        raise HTTPException(404, detail=response)
    if "result" in response:
        name = "transfer_money" if response["action"] == "transfer" else response["action"]
        response["reply"] = render_result(name, response["result"])
    return response


@app.post("/confirm")
def confirm_action(body: ConfirmRequest, request: Request):
    return run_audited_confirmation(body, request, confirmed=body.confirmed)


@app.post("/cancel")
def cancel_action(body: ActionRequest, request: Request):
    return run_audited_confirmation(body, request, confirmed=False)


def run_audited_confirmation(body: ActionRequest, request: Request, confirmed: bool):
    logger = request.app.state.audit_logger
    try:
        with logger.request_context(body.account_id, body.model_dump_json(), "confirm_action" if confirmed else "cancel_action") as context:
            logger.log_event("request_received", context, action_id=body.action_id)
            response = request.app.state.pending_actions.confirm_action(body.account_id, body.action_id, confirmed)
            finish_audit(logger, context, response)
            return action_response(response)
    except AuditError as exc:
        raise HTTPException(503, str(exc)) from None


def checked_result(result: dict) -> dict:
    if result["ok"]:
        return result
    status = 404 if result["code"] == "ACCOUNT_NOT_FOUND" else 503
    raise HTTPException(status, detail=result)


@app.get("/accounts/{account_id}/balance")
def balance(account_id: str, request: Request):
    return audited_query("get_balance", account_id, {}, request)


@app.get("/accounts/{account_id}/bills")
def bills(account_id: str, request: Request, limit: int = Query(default=10, ge=1, le=100)):
    return audited_query("get_bills", account_id, {"limit": limit}, request)


def audited_query(name: str, account_id: str, params: dict, request: Request):
    logger = request.app.state.audit_logger
    try:
        with logger.request_context(account_id, str(request.url.path), name) as context:
            logger.log_event("request_received", context)
            result = audited_execute_tool(name, json.dumps(params), account_id, logger)
            if not logger.log_after_execution("request_returned", context, execution_result="not_applicable"):
                result["audit_warning"] = AUDIT_WARNING
            return checked_result(result)
    except AuditError as exc:
        raise HTTPException(503, str(exc)) from None


@app.get("/audit/{account_id}")
def audit_history(account_id: str, request: Request, limit: int = Query(default=100, ge=1, le=500),
                  offset: int = Query(default=0, ge=0), request_id: str | None = Query(default=None, min_length=1, max_length=64)):
    try:
        logs = request.app.state.audit_logger.query(account_id, limit, offset, request_id)
        return {"account_id": account_id, "logs": logs, "limit": limit, "offset": offset}
    except AuditError as exc:
        raise HTTPException(503, str(exc)) from None
    except ValueError:
        raise HTTPException(422, "审计查询参数无效。") from None
