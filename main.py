"""FastAPI 入口：加载配置、初始化数据库、提供自然语言入口。"""

import json
import os
from contextlib import asynccontextmanager
from functools import partial
from typing import Annotated, Literal

from dotenv import load_dotenv
from fastapi import FastAPI, Query, Request
from openai import AsyncOpenAI
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from api.responses import (
    APIError, ActionResponse, ErrorResponse, SecurityDecision, action_response_fields,
    decision_scope, install_error_handlers, record_decision, secure_response,
)
from agent.llm_agent import ARGUMENT_MODELS, AgentError, AgentPause, BankingAgent, execute_tool, render_result
from agent.sessions import SessionError, SessionStore
from database.db import PROJECT_DIR, init_db
from deepseek_client import DeepSeekHTTPClient
from security import AuditLogger, PendingActionManager, PermissionController, RiskEngine
from security.audit import AUDIT_WARNING, AuditError
from security.daily_limit import assess_operation
from bill_analysis.tools import ANALYSIS_MODELS, READ_ONLY_TOOLS

load_dotenv(PROJECT_DIR / ".env")


def execute_saved_action(account_id: str, action: str, params: dict) -> dict:
    name = "transfer_money" if action == "transfer" else action
    return execute_tool(name, json.dumps(params, ensure_ascii=False), account_id)


def audited_execute_tool(name: str, raw_arguments: str, account_id: str, audit_logger: AuditLogger) -> dict:
    """只读兼容入口：风险与权限照常记录，任何写操作都不执行。"""
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
    risk = assess_operation(account_id, name, args) if valid else RiskEngine().evaluate(name, args.get("amount"))
    context["risk_level"] = risk["risk_level"]
    audit_logger.log_event("risk_evaluated", context, details={"reason": risk["reason"]})
    permission = PermissionController().decide(name, risk["risk_level"])
    record_decision({
        "operation": name, "risk_level": risk["risk_level"], "risk_reason": risk["reason"],
        "permission_action": permission["action"],
        "require_confirmation": permission["require_confirmation"],
        "require_verification": permission["require_verification"],
    })
    context["permission_action"] = permission["action"]
    context["confirmation_status"] = "not_required" if permission["action"] == "allow" else "blocked"
    audit_logger.log_event("permission_decided", context, details={**permission, "mode": "legacy"})
    if not valid:
        result = execute_tool(name, raw_arguments, account_id)  # 仅返回白名单/参数错误，不会执行工具。
        audit_logger.log_event("tool_validation_failed", context, execution_result="not_executed", details={"code": result["code"]})
        return result
    if valid and (name not in READ_ONLY_TOOLS or permission["action"] != "allow"):
        audit_logger.log_event("legacy_write_blocked", context, execution_result="not_executed")
        return {"ok": False, "code": "LEGACY_WRITE_DISABLED", "error": "旧 /chat 仅兼容只读查询。转账请使用 /chat/secure，并遵循确认或强验证策略。"}
    audit_logger.log_event("tool_execution_started", context, execution_result="started")
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
    record_decision(response)
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
    app.state.pending_actions = PendingActionManager(execute_saved_action, audit_logger=app.state.audit_logger,
                                                   risk_evaluator=assess_operation)
    app.state.sessions = SessionStore()
    app.state.secure_agent = BankingAgent(
        client, model, tool_executor=partial(secure_execute_tool, manager=app.state.pending_actions,
                                             audit_logger=app.state.audit_logger),
    ) if client else None
    try:
        yield
    finally:
        if client:
            await client.close()


app = FastAPI(
    title="Banking Agent AI", version="0.3.0", lifespan=lifespan,
    responses={code: {"model": ErrorResponse} for code in (403, 404, 409, 422, 429, 500, 502, 503, 504)},
)
install_error_handlers(app)


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    message: str = Field(min_length=1, max_length=2000)
    account_id: str = Field(default="1001", min_length=1, max_length=64)
    conversation_id: str | None = Field(default=None, min_length=32, max_length=32, pattern=r"^[0-9a-f]{32}$")

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
    status: Literal["completed", "pending", "require_verification", "failed", "needs_input", "partial", "execution_unknown"]
    request_id: str
    message: str
    response_kind: Literal["conversation", "capabilities", "clarification", "unsupported", "business_result"]
    conversation_id: str
    diagnostic_codes: list[str] = Field(default_factory=list)
    action_id: str | None = None
    risk_level: Literal["green", "yellow", "red"] | None = None
    operation: str | None = None
    risk_reason: str | None = None
    permission_action: Literal["allow", "pending_confirmation", "require_verification"] | None = None
    require_confirmation: bool | None = None
    require_verification: bool | None = None
    decisions: list[SecurityDecision] = Field(default_factory=list)
    audit_id: str
    audit_warning: str | None = None
    error_code: str | None = None
    error: ErrorResponse | None = None
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
        details={"status": response.get("status"), "diagnostic_codes": response.get("diagnostic_codes", [])},
    )
    tool_warning = any(item["result"].get("audit_warning") for item in response.get("tool_results", []))
    if not logged or tool_warning or response.get("audit_warning"):
        response["audit_warning"] = AUDIT_WARNING
        field = "reply" if "reply" in response else "message"
        response[field] = response.get(field, "") + "\n" + AUDIT_WARNING


async def run_audited_chat(body: ChatRequest, request: Request, secure: bool):
    logger = request.app.state.audit_logger
    try:
        with decision_scope() as decisions, logger.request_context(body.account_id, body.message) as context:
            logger.log_event("request_received", context, details={"entrypoint": "/chat/secure" if secure else "/chat"})
            agent = request.app.state.secure_agent if secure else request.app.state.agent
            if agent is None:
                logger.log_after_execution("request_failed", context, details={"code": "LLM_NOT_CONFIGURED"})
                raise APIError(503, "LLM_NOT_CONFIGURED", "请在 .env 中配置 DEEPSEEK_API_KEY，然后重启服务。")
            try:
                if secure:
                    with request.app.state.sessions.checkout(body.account_id, body.conversation_id) as (session, history):
                        pending = request.app.state.pending_actions.get_pending_action(body.account_id, session.pending_action_id) if session.pending_action_id else None
                        if pending is None:
                            session.pending_action_id = None
                        response = await agent.run(body.message, body.account_id, history=history, allow_transfer=pending is None)
                        response["conversation_id"] = session.conversation_id
                        if response.get("pending_action"):
                            session.pending_action_id = response["pending_action"]["action_id"]
                        request.app.state.sessions.remember(session, body.message, response)
                else:
                    response = await agent.run(body.message, body.account_id)
            except SessionError as exc:
                logger.log_after_execution("request_failed", context, details={"code": exc.code})
                raise APIError(exc.status_code, exc.code, str(exc)) from None
            except AgentError as exc:
                logger.log_after_execution("request_failed", context, details={"http_status": exc.status_code, "diagnostic_code": exc.diagnostic_code})
                code = "LLM_TIMEOUT" if exc.status_code == 504 else "LLM_SERVICE_ERROR"
                raise APIError(exc.status_code, code, str(exc)) from None
            except Exception:
                logger.log_after_execution("request_failed", context, execution_result="unknown")
                raise
            finish_audit(logger, context, response)
            return secure_response(response, decisions) if secure else response
    except AuditError as exc:
        raise APIError(503, "AUDIT_UNAVAILABLE", str(exc)) from None


def action_response(response: dict) -> dict:
    if response["status"] == "not_found":
        raise APIError(404, "ACTION_NOT_FOUND", response["message"], detail=response)
    if "result" in response:
        name = "transfer_money" if response["action"] == "transfer" else response["action"]
        response["reply"] = render_result(name, response["result"])
    return action_response_fields(response)


@app.post("/confirm", response_model=ActionResponse, response_model_exclude_none=True)
def confirm_action(body: ConfirmRequest, request: Request):
    return run_audited_confirmation(body, request, confirmed=body.confirmed)


@app.post("/cancel", response_model=ActionResponse, response_model_exclude_none=True)
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
        raise APIError(503, "AUDIT_UNAVAILABLE", str(exc)) from None


def checked_result(result: dict) -> dict:
    if result["ok"]:
        return result
    status = (404 if result["code"] in {"ACCOUNT_NOT_FOUND", "DATASET_NOT_FOUND", "RECORD_NOT_FOUND"} else
              422 if result["code"] == "INVALID_ARGUMENTS" else
              409 if result["code"] == "DATASET_AMBIGUOUS" else 503)
    raise APIError(status, result["code"], result["error"], detail=result)


@app.get("/accounts/{account_id}/balance")
def balance(account_id: str, request: Request):
    return audited_query("get_balance", account_id, {}, request)


@app.get("/accounts/{account_id}/bills")
def bills(account_id: str, request: Request, limit: int = Query(default=10, ge=1, le=100)):
    return audited_query("get_bills", account_id, {"limit": limit}, request)


def audited_query(name: str, account_id: str, params: dict, request: Request):
    logger = request.app.state.audit_logger
    try:
        with decision_scope() as decisions, logger.request_context(account_id, str(request.url.path), name) as context:
            logger.log_event("request_received", context)
            result = audited_execute_tool(name, json.dumps(params), account_id, logger)
            if name in ANALYSIS_MODELS:
                result.update(request_id=context["request_id"], audit_id=context["request_id"], tool=name, decisions=list(decisions))
                report = result.get("analysis")
                comparison = result.get("comparison")
                result["data_status"] = ("failed" if not result["ok"] else "incomplete_coverage" if (report and not report["coverage"]["complete"]) or (comparison and not comparison["comparable"]) else
                                         "no_records" if report and report["summary"]["count"] == 0 else "available")
            if not logger.log_after_execution("request_returned", context, execution_result="not_applicable"):
                result["audit_warning"] = AUDIT_WARNING
            return checked_result(result)
    except AuditError as exc:
        raise APIError(503, "AUDIT_UNAVAILABLE", str(exc)) from None


class BillAnalysisQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source: Literal["synthetic_demo"] = "synthetic_demo"
    period: str = Field(default="this_month", min_length=1, max_length=32)
    mode: Literal["summary", "compare"] = "summary"
    view: Literal["summary", "merchants", "anomalies"] = "summary"
    through_day: int | None = Field(default=None, ge=1, le=31)


class AnalysisRecordsQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source: Literal["synthetic_demo"] = "synthetic_demo"
    period: str = Field(default="this_month", min_length=1, max_length=32)
    limit: int = Field(default=20, ge=1, le=100)
    offset: int = Field(default=0, ge=0, le=100000)
    category: Literal["dining", "transport", "shopping", "housing", "other", "uncategorized"] | None = None
    merchant_id: str | None = Field(default=None, min_length=1, max_length=64)
    record_id: str | None = Field(default=None, min_length=1, max_length=64)


@app.get("/accounts/{account_id}/bill-analysis")
def bill_analysis(account_id: str, request: Request, query: Annotated[BillAnalysisQuery, Query()]):
    if query.mode == "compare":
        return audited_query("compare_spending", account_id, {"month": query.period, "through_day": query.through_day}, request)
    if query.through_day is not None:
        raise APIError(422, "INVALID_ARGUMENTS", "through_day仅用于相邻月份比较。")
    return audited_query("analyze_spending", account_id, {"period": query.period, "view": query.view}, request)


@app.get("/accounts/{account_id}/analysis-records")
def analysis_records(account_id: str, request: Request, query: Annotated[AnalysisRecordsQuery, Query()]):
    if query.record_id is not None:
        return audited_query("get_spending_record", account_id, {"record_id": query.record_id}, request)
    return audited_query("list_spending_records", account_id, query.model_dump(exclude={"source", "record_id"}), request)


@app.get("/audit/{account_id}")
def audit_history(account_id: str, request: Request, limit: int = Query(default=100, ge=1, le=500),
                  offset: int = Query(default=0, ge=0), request_id: str | None = Query(default=None, min_length=1, max_length=64)):
    try:
        logs = request.app.state.audit_logger.query(account_id, limit, offset, request_id)
        return {"account_id": account_id, "logs": logs, "limit": limit, "offset": offset}
    except AuditError as exc:
        raise APIError(503, "AUDIT_UNAVAILABLE", str(exc)) from None
    except ValueError:
        raise APIError(422, "INVALID_ARGUMENTS", "审计查询参数无效。") from None
