"""Demo 响应适配；只接收服务端决策和工具结果，不解析 LLM 文本。"""

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Literal

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from starlette.exceptions import HTTPException as StarletteHTTPException


class ErrorResponse(BaseModel):
    status: Literal["error"] = "error"
    error_code: str
    message: str
    # 为旧客户端保留 detail；验证错误不回显输入值。
    detail: str | dict | list | None = None
    request_id: str | None = None
    audit_id: str | None = None


class SecurityDecision(BaseModel):
    operation: str
    risk_level: Literal["green", "yellow", "red"]
    risk_reason: str
    permission_action: Literal["allow", "pending_confirmation", "require_verification"]
    require_confirmation: bool
    require_verification: bool


class ActionResponse(BaseModel):
    status: Literal["completed", "cancelled", "failed", "execution_unknown", "require_verification"]
    message: str
    execution_result: dict = Field(default_factory=dict)
    audit_id: str
    request_id: str
    action_id: str | None = None
    action: str | None = None
    result: dict | None = None
    reply: str | None = None
    audit_warning: str | None = None
    error_code: str | None = None
    error: ErrorResponse | None = None
    decision: SecurityDecision | None = None


class APIError(HTTPException):
    def __init__(self, status_code: int, error_code: str, message: str, detail=None):
        super().__init__(status_code, detail=message if detail is None else detail)
        self.error_code = error_code
        self.message = message


_decisions: ContextVar[list[dict] | None] = ContextVar("demo_security_decisions", default=None)


@contextmanager
def decision_scope():
    # asyncio.to_thread 复制上下文，当前请求的列表随工具调用传递；不同请求互不共享。
    decisions: list[dict] = []
    token = _decisions.set(decisions)
    try:
        yield decisions
    finally:
        _decisions.reset(token)


def record_decision(decision: dict) -> None:
    target = _decisions.get()
    if target is not None:
        fields = {name: decision[name] for name in SecurityDecision.model_fields}
        target.append(SecurityDecision.model_validate(fields).model_dump())


def error_payload(error_code: str, message: str, **extra) -> dict:
    return {"status": "error", "error_code": error_code, "message": message, **extra}


def attach_business_error(response: dict, result: dict | None = None) -> None:
    """业务状态保留旧协议，error 字段使用与 HTTP 错误相同的核心格式。"""
    if response["status"] == "require_verification":
        code = "VERIFICATION_REQUIRED"
    elif response["status"] == "execution_unknown":
        code = "EXECUTION_UNKNOWN"
    elif response["status"] == "failed":
        code = (result or {}).get("code", "TOOL_EXECUTION_FAILED")
    else:
        return
    response["error_code"] = code
    response["error"] = error_payload(code, response["message"])


def secure_response(response: dict, decisions: list[dict]) -> dict:
    response = dict(response)
    response["message"] = response.get("reply") or response.get("message", "")
    response["audit_id"] = response["request_id"]
    response["decisions"] = list(decisions)
    # 多工具请求采用实际决策中最高风险的一项；完整列表保留每一步的策略。
    rank = {"green": 0, "yellow": 1, "red": 2}
    selected = max(decisions, key=lambda item: rank[item["risk_level"]]) if decisions else None
    for name in SecurityDecision.model_fields:
        response[name] = selected[name] if selected else None
    failed_result = next((item["result"] for item in reversed(response["tool_results"])
                          if not item["result"].get("ok")), None)
    attach_business_error(response, failed_result)
    return response


def action_response_fields(response: dict) -> dict:
    response = dict(response)
    response["execution_result"] = response.get("result", {})
    response["audit_id"] = response.get("request_id")
    response["message"] = response.get("reply") or response["message"]
    warning = response.get("audit_warning")
    if warning and warning not in response["message"]:
        response["message"] += "\n" + warning
    attach_business_error(response, response.get("result"))
    return response


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):
        detail = [{"loc": list(item["loc"]), "msg": item["msg"], "type": item["type"]}
                  for item in exc.errors()]
        return JSONResponse(status_code=422, content=error_payload(
            "INVALID_ARGUMENTS", "请求参数无效，请检查字段、类型和取值范围。", detail=detail,
        ))

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, exc: StarletteHTTPException):
        if isinstance(exc, APIError):
            code, message = exc.error_code, exc.message
        else:
            code = {403: "FORBIDDEN", 404: "NOT_FOUND", 405: "METHOD_NOT_ALLOWED",
                    422: "INVALID_ARGUMENTS", 503: "SERVICE_UNAVAILABLE"}.get(exc.status_code, "HTTP_ERROR")
            message = exc.detail if isinstance(exc.detail, str) else "请求未能完成。"
        payload = error_payload(code, message, detail=exc.detail)
        if isinstance(exc, APIError) and isinstance(exc.detail, dict):
            for key in ("request_id", "audit_id"):
                if isinstance(exc.detail.get(key), str):
                    payload[key] = exc.detail[key]
        return JSONResponse(status_code=exc.status_code, headers=exc.headers, content=payload)

    @app.exception_handler(Exception)
    async def unexpected_error(request: Request, exc: Exception):
        return JSONResponse(status_code=500, content=error_payload(
            "INTERNAL_ERROR", "服务处理异常；如涉及转账，请先核对账单，不要自动重试。",
        ))
