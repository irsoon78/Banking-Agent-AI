"""卡片工具契约；外部只读执行，写入只允许已领取确认任务的专用执行器。"""

from pydantic import BaseModel, ConfigDict, Field

from database.db import database_path


class EmptyArgs(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class CardArgs(EmptyArgs):
    card_id: str = Field(min_length=6, max_length=64, pattern=r"^card-[a-zA-Z0-9_-]+$")


class RecordArgs(EmptyArgs):
    record_id: str = Field(min_length=1, max_length=64)


class OperationArgs(EmptyArgs):
    action_id: str = Field(min_length=1, max_length=64)


CARD_READ_MODELS = {"list_cards": EmptyArgs, "get_card": CardArgs,
                    "get_card_for_spending_record": RecordArgs, "get_card_operation": OperationArgs}
CARD_TEMP_OPERATIONS = {"temporarily_freeze_card": "temporary_freeze", "remove_temporary_freeze": "temporary_unfreeze"}
CARD_RED_OPERATIONS = {"report_card_loss": "report_loss", "cancel_card_loss_report": "cancel_loss_report"}
CARD_WRITE_MODELS = {name: CardArgs for name in (*CARD_TEMP_OPERATIONS, *CARD_RED_OPERATIONS)}
CARD_MODELS = {**CARD_READ_MODELS, **CARD_WRITE_MODELS}


def card_error(exc):
    result = {"ok": False, "code": exc.code, "error": str(exc), "execution_unknown": exc.code == "EXECUTION_UNKNOWN"}
    if getattr(exc, "decision", None):
        result["security_decision"] = exc.decision
    return result


def prepare_card_params(account_id, action, params):
    """只有服务端读取的状态/版本可进入确认任务，拒绝客户端自报版本或授权。"""
    from card_management.service import CardService
    from card_management.storage import CardCommand, plan_transition
    args = CARD_WRITE_MODELS[action].model_validate(params).model_dump()
    service = CardService(database_path(), account_id=account_id)
    public = service.get_card(args["card_id"])["card"]
    if action in CARD_TEMP_OPERATIONS:
        from card_management.storage import Card
        card = Card(**{name: public[name] for name in Card.__dataclass_fields__})
        plan_transition(card, CardCommand(account_id, card.card_id, CARD_TEMP_OPERATIONS[action], card.version, "prepare-only"))
    return {"card_id": public["card_id"], "expected_version": public["version"]}


def execute_card_read(name, account_id, args):
    from card_management.service import CardService
    from card_management.storage import CardError
    try:
        service = CardService(database_path(), account_id=account_id)
        if name == "list_cards":
            return service.list_cards()
        if name == "get_card":
            return service.get_card(args["card_id"])
        if name == "get_card_for_spending_record":
            return service.card_for_consumption(args["record_id"])
        if name == "get_card_operation":
            return {"ok": True, "card_operation": service.get_operation(args["action_id"])}
        raise CardError("UNKNOWN_TOOL", "不支持的卡片查询。")
    except CardError as exc:
        return card_error(exc)


def execute_confirmed_card(account_id, action, params):
    from card_management.storage import CardCommand, CardError
    from card_management.authorization import _CardPermit, _active
    from card_management.transitions import apply_temporary_action
    permit = _active.get()
    if action not in CARD_TEMP_OPERATIONS or not isinstance(permit, _CardPermit):
        return card_error(CardError("AUTHORIZATION_REQUIRED", "缺少已确认的卡片专用许可，尚未执行。"))
    command = CardCommand(account_id, params.get("card_id"), CARD_TEMP_OPERATIONS[action], params.get("expected_version"), permit.command.action_id)
    try:
        result = apply_temporary_action(database_path(), command)
        return {"ok": True, "card_operation": result, "security_decision": result.get("security_decision")}
    except CardError as exc:
        return card_error(exc)


def render_card(result):
    if "card_operation" in result:
        r = result["card_operation"]
        label = "临时冻结" if r["operation"] == "temporary_freeze" else "解除临时冻结"
        return f"模拟卡片 {r['card_id']} 已完成{label}；状态版本 {r['after']['version']}。操作凭据：{r['operation_id']}。正式挂失状态未改变。"
    if result.get("linked") is False:
        return "该原始消费记录尚未关联模拟银行卡，不会猜测卡片。"
    cards = result.get("cards", [result["card"]] if "card" in result else [])
    labels = {"active": "正常", "temporarily_frozen": "临时冻结", "reported_lost": "正式挂失"}
    return "模拟银行卡（不代表真实银行卡状态）：\n" + ("\n".join(f"{c['card_id']} {c['display_name']} 尾号{c['last_four']}：{labels[c['effective_status']]}，版本{c['version']}。" for c in cards) or "当前账户没有模拟银行卡。")
