"""LLM 选择工具，Python 执行工具；最终金融回复由工具结果确定。"""

import asyncio
import json
from typing import Callable

from openai import APIConnectionError, APIStatusError, APITimeoutError, AsyncOpenAI
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from agent.prompt import SYSTEM_PROMPT
from tools.account import get_balance
from tools.bill import get_bills
from tools.transfer import transfer_money


class EmptyArgs(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class BillArgs(EmptyArgs):
    limit: int = Field(ge=1, le=100)


class TransferArgs(EmptyArgs):
    to_account_id: str = Field(min_length=1, max_length=64)
    amount: str = Field(min_length=1, max_length=13)


def function_schema(name: str, description: str, arguments: type[BaseModel]) -> dict:
    parameters = arguments.model_json_schema()
    # Pydantic 的无参数模型省略 required；strict schema 明确提供空数组。
    parameters.setdefault("required", [])
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": parameters,
            "strict": True,
        },
    }


TOOLS = [
    function_schema("get_balance", "查询当前账户的真实演示余额。", EmptyArgs),
    function_schema("get_bills", "查询当前账户最近的账单，默认查询 10 条。", BillArgs),
    function_schema("transfer_money", "按用户明确指定的收款账户编号和人民币金额模拟转账。", TransferArgs),
]
ARGUMENT_MODELS = {"get_balance": EmptyArgs, "get_bills": BillArgs, "transfer_money": TransferArgs}
NO_TOOL_REPLY = "请明确说明要查询余额、查询最近几条账单，或模拟转账到哪个账户及金额。每条请求只支持一笔转账；请提供完整信息。"


class AgentError(Exception):
    def __init__(self, message: str, status_code: int = 502):
        super().__init__(message)
        self.status_code = status_code


class AgentPause(Exception):
    """由服务端执行策略暂停操作，模型不能生成或批准此异常。"""

    def __init__(self, response: dict):
        super().__init__(response["message"])
        self.response = response


def execute_tool(name: str, raw_arguments: str, account_id: str) -> dict:
    """只调度允许的函数，校验参数；当前账户由 API 注入。"""
    argument_model = ARGUMENT_MODELS.get(name)
    if argument_model is None:
        return {"ok": False, "code": "UNKNOWN_TOOL", "error": "模型请求了不支持的工具。"}
    try:
        payload = json.loads(raw_arguments)
        args = argument_model.model_validate(payload).model_dump()
    except (ValueError, TypeError, ValidationError):
        return {"ok": False, "code": "INVALID_ARGUMENTS", "error": "工具参数无效，请明确提供所需信息。"}
    if name == "get_balance":
        return get_balance(account_id)
    if name == "get_bills":
        return get_bills(account_id, **args)
    return transfer_money(account_id, **args)


def render_result(name: str, result: dict) -> str:
    """所有金额、账户和交易结果都来自工具返回值，不采用模型自由文本。"""
    if not result["ok"]:
        return result["error"]
    if name == "get_balance":
        return f"账户 {result['account_id']}（{result['owner']}）余额：{result['balance']} 元。"
    if name == "transfer_money":
        return (
            f"模拟转账成功：从 {result['from_account_id']} 向 {result['to_account_id']} "
            f"转入 {result['amount']} 元。转出账户余额：{result['balance']} 元。"
            f"交易编号：{result['transfer_id']}。"
        )
    if not result["bills"]:
        return f"账户 {result['account_id']} 暂无账单。"
    labels = {"opening": "演示初始入账", "transfer_in": "转入", "transfer_out": "转出"}
    lines = [f"账户 {result['account_id']} 最近 {len(result['bills'])} 条账单："]
    for item in result["bills"]:
        counterparty = f"，对方账户 {item['counterparty_id']}" if item["counterparty_id"] else ""
        lines.append(f"{item['created_at']} | {labels[item['kind']]} {item['amount']} 元{counterparty}")
    return "\n".join(lines)


def make_response(records: list[dict], status: str, notice: str = "") -> dict:
    reply = "\n".join(render_result(item["tool"], item["result"]) for item in records)
    if notice:
        reply = f"{reply}\n{notice}".strip()
    return {"status": status, "reply": reply or NO_TOOL_REPLY, "tool_results": records}


class BankingAgent:
    def __init__(self, client: AsyncOpenAI, model: str, tool_executor: Callable = execute_tool):
        self.client = client
        self.model = model
        self.tool_executor = tool_executor

    async def run(self, message: str, account_id: str) -> dict:
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": message},
        ]
        records = []
        for _ in range(6):
            try:
                completion = await self.client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                    tools=TOOLS,
                    tool_choice="auto",
                    parallel_tool_calls=False,
                )
            except APITimeoutError:
                if records:
                    return make_response(records, "partial", "后续模型调用超时；以上为已完成的工具结果。")
                raise AgentError("模型调用超时，请稍后重试。", 504) from None
            except (APIConnectionError, APIStatusError):
                if records:
                    return make_response(records, "partial", "后续模型调用失败；以上为已完成的工具结果。")
                raise AgentError("模型服务调用失败，请检查 API Key、模型名称和网络。") from None
            if not completion.choices:
                if records:
                    return make_response(records, "partial", "模型返回空响应；以上为已完成的工具结果。")
                raise AgentError("模型返回空响应。")
            assistant = completion.choices[0].message
            calls = assistant.tool_calls or []
            if not calls:
                return make_response(records, "completed" if records else "needs_input")
            # 虽然请求禁用并行调用，仍校验响应，避免一次执行多笔转账。
            if len(calls) != 1 or calls[0].type != "function":
                return make_response(records, "partial" if records else "failed", "模型返回不支持的工具调用组合，本轮工具未执行。")
            call = calls[0]
            messages.append(assistant.model_dump(exclude_none=True))
            try:
                result = await asyncio.to_thread(self.tool_executor, call.function.name, call.function.arguments, account_id)
            except AgentPause as pause:
                response = make_response(records, pause.response["status"], pause.response["message"])
                return {**response, **pause.response}
            records.append({"tool": call.function.name, "result": result})
            messages.append({
                "role": "tool",
                "tool_call_id": call.id,
                "content": json.dumps(result, ensure_ascii=False),
            })
            if not result["ok"]:
                return make_response(records, "failed")
            # 转账提交后立即返回交易凭据，不依赖下一次 LLM 调用，也不会重复扣款。
            if call.function.name == "transfer_money":
                return make_response(records, "completed")
        return make_response(records, "partial", "已达到工具调用上限；以上为已完成的工具结果。")
