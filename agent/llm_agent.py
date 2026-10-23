"""LLM 选择工具，Python 执行工具；最终金融回复由工具结果确定。"""

import asyncio
import json
from typing import Callable

from openai import APIConnectionError, APIStatusError, APITimeoutError, AsyncOpenAI
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from agent.prompt import SYSTEM_PROMPT
from agent.dialogue import DialogueContext, social_or_help, text_response, unsupported_request, confirmation_message, clarification_response, business_request
from agent.intents import resolve_request, transfer_request_issue
from tools.account import get_balance
from tools.bill import get_bills
from tools.transfer import transfer_money
from agent.analysis_intents import resolve_analysis_request
from bill_analysis.tools import ANALYSIS_MODELS, execute_analysis_tool, render_analysis
from card_management.tools import CARD_MODELS, CARD_READ_MODELS, CARD_WRITE_MODELS, execute_card_read, render_card
from agent.card_intents import CardResolution, resolve_card_request


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
    function_schema("transfer_money", "提交一笔明确转账请求供服务端安全评估，不代表直接执行。收款账户及金额齐全时，无论金额大小均应提交；风险、待确认或强验证阻断全部由服务端决定。", TransferArgs),
    *[function_schema(name, description, ANALYSIS_MODELS[name]) for name, description in (
        ("analyze_spending", "只读分析当前账户独立模拟消费样本：分类总额、商户排行或规则异常提示。日期由服务端解释，数据不影响余额。"),
        ("compare_spending", "只读比较相邻月份消费样本；month为较晚月份，through_day指定两月相同前N天，整月用null。覆盖不足不能给出增幅。"),
        ("list_spending_records", "只读查看消费样本原始记录，分页不影响统计；未过滤分类和商户时传null。"),
        ("get_spending_record", "按用户提供的记录ID只读追溯当前账户样本原始记录。"),
    )],
    *[function_schema(name, "模拟银行卡只读查询。" if name in CARD_READ_MODELS else "提交明确卡片请求供服务端评估；临时操作须按钮确认，挂失/解挂强验证阻断，绝不直接执行。", model) for name, model in CARD_MODELS.items()],
]
ARGUMENT_MODELS = {"get_balance": EmptyArgs, "get_bills": BillArgs, "transfer_money": TransferArgs, **ANALYSIS_MODELS, **CARD_MODELS}
NO_TOOL_REPLY = "请明确说明要查询余额、查询最近几条账单，或模拟转账到哪个账户及金额。每条请求只支持一笔转账；请提供完整信息。"


class AgentError(Exception):
    def __init__(self, message: str, status_code: int = 502, diagnostic_code: str = "MODEL_ERROR"):
        super().__init__(message)
        self.status_code = status_code
        self.diagnostic_code = diagnostic_code


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
    if name in ANALYSIS_MODELS:
        return execute_analysis_tool(name, account_id, args)
    if name in CARD_READ_MODELS:
        return execute_card_read(name, account_id, args)
    if name in CARD_WRITE_MODELS:
        return {"ok": False, "code": "SECURE_ENTRYPOINT_REQUIRED", "error": "卡片写操作必须经过安全入口、权限及专用确认许可。"}
    return transfer_money(account_id, **args)


def execute_read_only_tool(name: str, raw_arguments: str, account_id: str) -> dict:
    if name == "transfer_money" or name in CARD_WRITE_MODELS:
        return {"ok": False, "code": "SECURE_ENTRYPOINT_REQUIRED", "error": "转账必须通过安全入口及用户确认，不能直接执行。"}
    return execute_tool(name, raw_arguments, account_id)


def render_result(name: str, result: dict) -> str:
    """所有金额、账户和交易结果都来自工具返回值，不采用模型自由文本。"""
    if not result["ok"]:
        return result["error"]
    if name == "get_balance":
        return f"账户 {result['account_id']}（{result['owner']}）余额：{result['balance']} 元。"
    if name in ANALYSIS_MODELS:
        return render_analysis(result)
    if name in CARD_MODELS:
        return render_card(result)
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
    return {"status": status, "reply": reply or NO_TOOL_REPLY, "tool_results": records, "response_kind": "business_result"}


def reply_from_evidence(message: str, model_text: str | None, records: list[dict], *, context: DialogueContext) -> dict:
    """只接收执行器的本轮记录，不接收模型声称的凭据或历史账本。

    有实际工具记录时只渲染结构化结果；无记录时仅能返回无业务凭据的对话。
    context 只帮助解释当前限制，不能把模型文本升级成账户事实或授权。
    """
    if records:
        status = "completed" if all(item["result"]["ok"] for item in records) else "failed"
        return make_response(records, status)
    return text_response(message, model_text, context=context)


class BankingAgent:
    def __init__(self, client: AsyncOpenAI, model: str, tool_executor: Callable = execute_read_only_tool):
        self.client = client
        self.model = model
        self.tool_executor = tool_executor

    async def run(self, message: str, account_id: str, *, history: list[dict] | None = None, allow_transfer: bool = True) -> dict:
        history = history or []
        diagnostics = []
        # allow_transfer 来自服务端对待确认任务的检查，不采用助手历史或客户端状态。
        dialogue_context = DialogueContext(transfer_pending=not allow_transfer)
        # 独立调查模式不扩展普通Agent工具白名单，且优先于单项分析/卡片路由。
        from agent.investigation_intents import resolve_investigation_request, investigation_clarification
        investigation = resolve_investigation_request(message, account_id)
        if investigation is not None:
            if investigation.options is None:
                return investigation_clarification(investigation.code)
            from card_management.authorization import current_conversation
            conversation_id = current_conversation()
            if conversation_id is None:
                return investigation_clarification("INVESTIGATION_SECURE_ENTRY_REQUIRED")
            from investigation.planner import run_planned_investigation, render_investigation
            from api.responses import record_decision
            report = await run_planned_investigation(client=self.client, model=self.model,
                account_id=account_id, options=investigation.options, conversation_id=conversation_id)
            for step in report["steps"]:
                for decision in step["decisions"]:
                    record_decision(decision)
            return {"status": report["status"], "reply": render_investigation(report),
                "tool_results": [], "response_kind": "business_result", "diagnostic_codes": ["INVESTIGATION_REPORT"],
                "investigation_id": report["investigation_id"], "investigation": report,
                "execution_status": report["execution_status"], "coverage_status": report["coverage_status"],
                "planner_mode": report["planner_mode"]}
        analysis_request = resolve_analysis_request(message, account_id)
        card_request = resolve_card_request(message, account_id)

        def finish(response: dict) -> dict:
            # 只有代码生成的枚举原因，不携带模型原文、参数或新的用户敏感数据。
            return {**response, "diagnostic_codes": list(dict.fromkeys([*diagnostics, *response.get("diagnostic_codes", [])]))}

        target_context = ""
        if card_request and card_request.tool in CARD_WRITE_MODELS and card_request.code == "CARD_NAMED_TARGET":
            # 不扩展写工具权限/参数：只读定位后仍用原 card_id 契约，模型须与此结果一致。
            from card_management.service import CardService
            from card_management.storage import CardError
            from database.db import database_path
            try:
                target = await asyncio.to_thread(CardService(database_path(), account_id=account_id).resolve_card_target, **card_request.arguments)
            except CardError as exc:
                diagnostics.append(exc.code)
                if exc.code in {"CARD_TARGET_NOT_FOUND", "CARD_TARGET_AMBIGUOUS"}:
                    return finish(clarification_response(exc.code))
                return finish(make_response([], "failed", str(exc)))
            card_request = CardResolution(card_request.tool, {"card_id": target["card"]["card_id"]}, "CARD_TARGET_RESOLVED")
            diagnostics.append(card_request.code)
            # 仅注入已校验的内部ID与枚举工具名，不把卡名等数据提升为系统指令。
            target_context = (
                f"\n服务端唯一匹配了本轮用户明确提供的完整卡名和尾号：card_id={card_request.arguments['card_id']}。"
                f"对应请求工具为{card_request.tool}；使用此ID提交安全评估。匹配不代表认证、授权或执行成功，不得填写版本或风险等级。")

        messages = [
            {"role": "system", "content": SYSTEM_PROMPT + target_context},
            *history,
            {"role": "user", "content": message},
        ]
        conversation_only = (not business_request(message, history) or social_or_help(message) or confirmation_message(message) or unsupported_request(message)) and card_request is None
        records = []
        for _ in range(6):
            try:
                tool_options = {} if conversation_only else {"tools": TOOLS, "tool_choice": "auto", "parallel_tool_calls": False}
                completion = await self.client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                    **tool_options,
                )
            except APITimeoutError:
                if records:
                    diagnostics.append("MODEL_TIMEOUT")
                    return finish(make_response(records, "partial", "后续模型调用超时；以上为已完成的工具结果。"))
                raise AgentError("模型调用超时，请稍后重试。", 504, "MODEL_TIMEOUT") from None
            except (APIConnectionError, APIStatusError):
                if records:
                    diagnostics.append("MODEL_ERROR")
                    return finish(make_response(records, "partial", "后续模型调用失败；以上为已完成的工具结果。"))
                raise AgentError("模型服务调用失败，请检查 API Key、模型名称和网络。") from None
            if not completion.choices:
                if records:
                    diagnostics.append("MODEL_EMPTY_RESPONSE")
                    return finish(make_response(records, "partial", "模型返回空响应；以上为已完成的工具结果。"))
                raise AgentError("模型返回空响应。", diagnostic_code="MODEL_EMPTY_RESPONSE")
            choice = completion.choices[0]
            assistant = choice.message
            calls = assistant.tool_calls or []
            fallback = False
            if getattr(assistant, "refusal", None) or getattr(assistant, "function_call", None) or choice.finish_reason not in {"stop", "tool_calls"}:
                diagnostics.append("MODEL_CALL_ERROR")
                return finish(make_response(records, "partial" if records else "failed", "模型未正常完成工具选择，本轮未启动新的操作。"))
            if not calls:
                if records:
                    return finish(reply_from_evidence(message, assistant.content, records, context=dialogue_context))
                diagnostics.append("MODEL_NO_TOOL_CALL")
                if choice.finish_reason != "stop":
                    diagnostics.append("MODEL_CALL_ERROR")
                    return finish(make_response([], "failed", "模型工具调用不完整，尚未执行操作。"))
                if card_request and (not isinstance(assistant.content, str) or not assistant.content.strip()):
                    diagnostics.append("MODEL_EMPTY_TEXT")
                    return finish(make_response([], "failed", "模型未返回可用内容，尚未启动卡片操作。"))
                if conversation_only:
                    return finish(reply_from_evidence(message, assistant.content, records, context=dialogue_context))
                resolution = card_request or analysis_request or resolve_request(message, account_id)
                if resolution is None:
                    return finish(reply_from_evidence(message, assistant.content, records, context=dialogue_context))
                diagnostics.append(resolution.code)
                if resolution.tool is None:
                    return finish(clarification_response(resolution.code))
                name, raw_arguments = resolution.tool, json.dumps(resolution.arguments, ensure_ascii=False)
                fallback = True
            else:
                # 模型明确返回错误调用时不能进入无工具兜底，也不能再次调用模型重试。
                if len(calls) != 1 or calls[0].type != "function":
                    diagnostics.append("INVALID_TOOL_CALL")
                    return finish(make_response(records, "partial" if records else "failed", "模型返回不支持的工具调用组合，本轮工具未执行。"))
                call = calls[0]
                name, raw_arguments = call.function.name, call.function.arguments
                if name not in ARGUMENT_MODELS:
                    diagnostics.append("UNKNOWN_TOOL")
                    return finish(make_response([*records, {"tool": name, "result": {"ok": False, "code": "UNKNOWN_TOOL", "error": "模型请求了不支持的工具。"}}], "failed"))
                if conversation_only:
                    diagnostics.append("INTENT_REJECTED")
                    return finish(reply_from_evidence(message, None, records, context=dialogue_context))
            try:
                args = ARGUMENT_MODELS[name].model_validate(json.loads(raw_arguments)).model_dump()
            except (ValueError, TypeError, ValidationError):
                # 原有执行器负责返回参数错误和审计；不会生成替代参数、触发兜底。
                args = None
                diagnostics.append("TOOL_ARGUMENTS_INVALID")
            if card_request:
                if card_request.tool is None:
                    diagnostics.append(card_request.code)
                    return finish(clarification_response(card_request.code))
                if name != card_request.tool or (args is not None and args != card_request.arguments):
                    diagnostics.append("CARD_REQUEST_MISMATCH")
                    return finish(clarification_response("CARD_REQUEST_MISMATCH"))
            elif name in CARD_MODELS and args is not None:
                diagnostics.append("CARD_INTENT_REQUIRED")
                return finish(clarification_response("CARD_INTENT_REQUIRED"))
            if name in CARD_WRITE_MODELS and not allow_transfer:
                diagnostics.append("PENDING_EXISTS")
                return finish(clarification_response("PENDING_EXISTS"))
            if analysis_request and not card_request:
                if analysis_request.tool is None:
                    return finish(clarification_response(analysis_request.code))
                if name != analysis_request.tool:
                    return finish(clarification_response("ANALYSIS_REQUEST_MISMATCH"))
                if args is not None:
                    # 有效模型调用也不能猜日期；原文或服务端时钟决定最终查询范围。
                    args = analysis_request.arguments
                    raw_arguments = json.dumps(args, ensure_ascii=False)
            elif name in ANALYSIS_MODELS and args is not None:
                return finish(clarification_response("ANALYSIS_DATE_REQUIRED"))
            if name == "transfer_money" and args is not None:
                if not allow_transfer:
                    diagnostics.append("PENDING_EXISTS")
                    return finish({**clarification_response("PENDING_EXISTS"), "tool_results": records})
                issue = transfer_request_issue(message, account_id, args, history)
                if issue:
                    diagnostics.append(issue)
                    return finish({**clarification_response(issue), "tool_results": records})
            if name in {"get_balance", "get_bills"} and args is not None:
                requested = resolve_request(message, account_id)
                if requested and requested.code == "ACCOUNT_MISMATCH":
                    diagnostics.append("ACCOUNT_MISMATCH")
                    return finish({**clarification_response("ACCOUNT_MISMATCH"), "tool_results": records})
            if not fallback:
                messages.append(assistant.model_dump(exclude_none=True))
            try:
                result = await asyncio.to_thread(self.tool_executor, name, raw_arguments, account_id)
            except AgentPause as pause:
                if pause.response["status"] == "execution_unknown":
                    diagnostics.append("EXECUTION_UNKNOWN")
                response = make_response(records, pause.response["status"], pause.response["message"])
                return finish({**response, **pause.response})
            records.append({"tool": name, "result": result})
            # 兜底直接用真实工具结果返回，不把一次操作变成额外模型轮次。
            if fallback:
                return finish(make_response(records, "completed" if result["ok"] else "failed"))
            messages.append({
                "role": "tool",
                "tool_call_id": call.id,
                "content": json.dumps(result, ensure_ascii=False),
            })
            if not result["ok"]:
                return finish(make_response(records, "failed"))
            # 转账提交后立即返回交易凭据，不依赖下一次 LLM 调用，也不会重复扣款。
            if name == "transfer_money" or name in ANALYSIS_MODELS or name in CARD_MODELS:
                return finish(make_response(records, "completed"))
        return finish(make_response(records, "partial", "已达到工具调用上限；以上为已完成的工具结果。"))
