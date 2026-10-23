"""独立调查模式：模型只选择本次就绪命令，业务参数始终由核心派生。"""

import asyncio
import json

from investigation.contracts import InvestigationError, PlannerSelection, StepCommand
from investigation.service import InvestigationService
from investigation.storage import InvestigationStore
from database.db import database_path

PLANNER_PROMPT = """你是只读消费调查步骤选择器，不是业务执行器。
只使用select_investigation_step从ready_steps中原样选择一个命令。
focus和日期是服务端固定的用户范围，不可扩大、更改。不得填写账户、SQL、record_id、card_id、金额或风险。
可优先核对某个异常分支，或先查询已核验关联卡片。只能改变独立就绪步骤的顺序，不能跳过依赖。
返回一个函数调用，不输出消费报告、金融事实或操作授权。"""


def _selection(completion, view):
    if len(completion.choices) != 1:
        raise InvestigationError("PLAN_INVALID_RESPONSE", "规划响应无效。")
    choice = completion.choices[0]
    message = choice.message
    calls = message.tool_calls or []
    if (choice.finish_reason != "tool_calls" or getattr(message, "refusal", None)
            or getattr(message, "function_call", None) or len(calls) != 1
            or calls[0].type != "function" or calls[0].function.name != "select_investigation_step"):
        raise InvestigationError("PLAN_INVALID_CALL", "规划调用无效。")
    arguments = calls[0].function.arguments
    if not isinstance(arguments, str) or len(arguments.encode("utf-8")) > 4096:
        raise InvestigationError("PLAN_INVALID_ARGUMENTS", "规划参数无效。")
    try:
        plan = PlannerSelection.model_validate_json(arguments)
    except (ValueError, TypeError):
        raise InvestigationError("PLAN_INVALID_ARGUMENTS", "规划参数无效。") from None
    if plan.focus != view["focus"]:
        raise InvestigationError("PLAN_SCOPE_CHANGED", "模型不得改变用户调查重点。")
    command = StepCommand.model_validate(plan.model_dump(exclude={"focus", "reason_code"})).model_dump()
    if command not in view["ready_steps"]:
        raise InvestigationError("PLAN_STEP_NOT_READY", "步骤或证据不属于当前就绪集合。")
    return command, plan.reason_code


async def run_planned_investigation(*, client, model, account_id, options, conversation_id=None,
                                    audit_logger=None, store=None):
    """每步与每次规划后保存。失败后仅对尚未执行的只读步骤确定性降级。"""
    from agent.llm_agent import function_schema
    selection_tool = function_schema("select_investigation_step", "选择一个服务端就绪调查步骤。", PlannerSelection)
    selection_tool["function"]["parameters"]["required"] = list(selection_tool["function"]["parameters"]["properties"])
    store = store or InvestigationStore()
    run = await asyncio.to_thread(InvestigationService(account_id=account_id, audit_logger=audit_logger).start, options)
    planning = {"model_calls": 0, "events": [], "fallback_reason": None, "status": "not_requested"}
    mode = "deterministic_no_choice"
    persisted = True

    def snapshot():
        result = run.snapshot()
        result.update(orchestration="restricted_step_planning", planner_mode=mode,
                      planning=json.loads(json.dumps(planning)), persistence_status="saved" if persisted else "failed")
        return result

    async def save():
        if store.path != database_path().resolve():
            raise InvestigationError("DATABASE_CONTEXT_CHANGED", "调查存储上下文变化。")
        return await asyncio.to_thread(store.save, snapshot(), conversation_id=conversation_id)

    async def event(status, code, command=None, reason=None):
        entry = {"index": len(planning["events"]) + 1, "status": status, "code": code}
        if command:
            entry["step_id"] = command["step_id"]
        if reason:
            entry["reason_code"] = reason
        event_id = await asyncio.to_thread(run.record_planner_event, entry)
        planning["events"].append({**entry, "audit_event_id": event_id})

    try:
        await save()  # 模型等待前已有可恢复的running快照。
        while ready := run.ready_steps():
            command = ready[0]
            if len(ready) > 1 and planning["fallback_reason"] is None:
                if planning["model_calls"] >= 20:
                    mode = "deterministic_fallback"
                    planning["fallback_reason"] = "PLAN_BUDGET_EXCEEDED"
                else:
                    planning["model_calls"] += 1
                    planning["status"] = "awaiting_model"
                    await save()
                    try:
                        view = run.planning_view()
                        completion = await client.chat.completions.create(
                            model=model, timeout=15, messages=[{"role": "system", "content": PLANNER_PROMPT},
                                {"role": "user", "content": json.dumps(view, ensure_ascii=False)}],
                            tools=[selection_tool],
                            tool_choice={"type": "function", "function": {"name": "select_investigation_step"}},
                            parallel_tool_calls=False,
                        )
                        # await期间状态可能变化：必须重新获取合法集合，再由execute_step二次复核。
                        command, reason = _selection(completion, run.planning_view())
                    except asyncio.CancelledError:
                        raise
                    except Exception as exc:
                        code = exc.code if isinstance(exc, InvestigationError) else "PLAN_MODEL_UNAVAILABLE"
                        planning.update(fallback_reason=code, status="fallback")
                        mode = "deterministic_fallback"
                        await event("rejected", code)
                        command = ready[0]
                    else:
                        mode = "model_planned"
                        planning["status"] = "accepted"
                        await event("accepted", "PLAN_ACCEPTED", command, reason)
                    await save()
            # 审计失败使ready_steps为空；不在失败后继续运行原候选命令。
            if command not in run.ready_steps():
                break
            await asyncio.to_thread(run.execute_step, command)
            await save()
        await asyncio.to_thread(run.finish)
        planning["status"] = "finished"
        await save()
    except asyncio.CancelledError:
        await asyncio.to_thread(run.interrupt)
        planning["status"] = "interrupted"
        try:
            await save()
        except Exception:
            pass
        raise
    except Exception:
        # 不自动重试任何写入；元数据失败也不能宣称可恢复、完整成功。
        persisted = False
        await asyncio.to_thread(run.interrupt)
        planning["status"] = "storage_failed"
    result = snapshot()
    if not persisted:
        result["status"] = "partial" if result.get("dataset") else "failed"
        result["errors"].append({"step_id": None, "code": "PERSISTENCE_UNAVAILABLE", "message": "调查快照未能可靠保存；未继续或自动重试。"})
    return result


def render_investigation(report):
    labels = {"completed": "执行完成", "partial": "部分执行", "failed": "执行失败", "interrupted": "已中断", "running": "执行中"}
    period = report["period"]
    lines = [f"只读消费调查 {report['investigation_id']}：{labels[report['execution_status']]}。",
             f"北京时间范围：{period['start_date']} 至 {period['end_date_exclusive']}（结束日不含）。",
             f"规划模式：{report['planner_mode']}。"]
    if report["coverage_status"] != "complete":
        lines.append("数据覆盖不足或无法确定，未覆盖日期不能视为没有异常；请查看报告coverage中的实际覆盖区间。")
    lines.append(f"命中{len(report['findings'])}组规则提示，核验{report['budget']['verified_records']}条原始记录。规则命中不代表已确认盗刷或重复扣款。")
    for item in report["findings"]:
        finding = item["finding"]
        lines.append(f"规则{finding['rule_id']}，涉及记录：{', '.join(finding['record_ids'])}。")
        if "observed_interval_minutes" in item:
            lines.append(f"已核验的两笔消费时间差：{item['observed_interval_minutes']}分钟。")
    for evidence in report["evidence"]["records"]:
        if evidence["status"] == "verified":
            record = evidence["record"]
            lines.append(f"记录{record['record_id']}：{record['amount']}元；发生于{record['occurred_at']}。")
    for evidence in report["evidence"]["cards"]:
        if evidence["status"] == "verified":
            card = evidence["card"]
            lines.append(f"关联模拟卡{card['card_id']}：临时冻结={card['temporary_frozen']}，挂失状态={card['loss_status']}，版本={card['version']}；读取于{evidence['read_at']}。")
    if report["planning"]["fallback_reason"]:
        lines.append("模型规划未能完成，剩余只读步骤采用确定性降级；原因：" + report["planning"]["fallback_reason"] + "。")
    if report["persistence_status"] != "saved":
        lines.append("快照保存失败，不能保证后续恢复查询。")
    lines.extend([report["disclaimer"], report["safety"]["note"]])
    return "\n".join(lines)
