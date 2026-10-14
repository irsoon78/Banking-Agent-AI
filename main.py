"""FastAPI 入口：加载配置、初始化数据库、提供自然语言入口。"""

import os
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query, Request
from openai import AsyncOpenAI
from pydantic import BaseModel, ConfigDict, Field, field_validator

from agent.llm_agent import AgentError, BankingAgent
from database.db import PROJECT_DIR, init_db
from deepseek_client import DeepSeekHTTPClient
from tools.account import get_balance
from tools.bill import get_bills

load_dotenv(PROJECT_DIR / ".env")


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
