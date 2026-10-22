"""待确认操作及黄色确认流程；预评估可注入，真实资金仍由事务内安全执行器复核。"""

from copy import deepcopy
from datetime import datetime, timezone
from threading import Lock
from time import monotonic
from typing import Callable
from uuid import uuid4

from security.audit import AUDIT_WARNING, AuditLogger
from security.permission import PermissionController
from security.risk_engine import RiskEngine
from security.transfer_grant import confirmed_transfer_scope
from card_management.tools import CARD_TEMP_OPERATIONS, CARD_WRITE_MODELS, card_error, prepare_card_params
from pydantic import ValidationError

Executor = Callable[[str, str, dict], dict]


class PendingActionManager:
    """executor(account_id, action, params) 由服务端注册，不能由模型指定。

    待确认任务只存在于本进程内存。领取任务时原子移除，确保一个任务
    最多调用一次 executor；执行结果未知时也不自动重试。
    """

    def __init__(self, executor: Executor, audit_logger: AuditLogger | None = None, *, risk_evaluator=None, clock=monotonic, card_ttl=300):
        self._executor = executor
        self._pending: dict[str, dict] = {}
        self._lock = Lock()
        self._risk_engine = RiskEngine()
        self._permissions = PermissionController()
        self._audit_logger = audit_logger
        self._risk_evaluator = risk_evaluator
        self._clock, self._card_ttl = clock, card_ttl

    def _assess(self, account_id, action, params):
        if self._risk_evaluator:
            return self._risk_evaluator(account_id, action, params)
        # 不连接账本的纯策略管理器可用于模拟执行器；真实资金工具仍强制事务内复核。
        return self._risk_engine.evaluate(action, params.get("amount"))

    def _log(self, event_type: str, context: dict, **kwargs) -> None:
        if self._audit_logger:
            self._audit_logger.log_event(event_type, context, **kwargs)

    @staticmethod
    def _public_record(record: dict) -> dict:
        return deepcopy({key: value for key, value in record.items() if not key.startswith("_")})

    @staticmethod
    def _identifier(value: str, name: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{name} 不能为空")
        return value.strip()

    def create_pending_action(self, account_id: str, action: str, params: dict, risk_level: str,
                              audit_context: dict | None = None) -> dict:
        account_id = self._identifier(account_id, "account_id")
        action = self._identifier(action, "action").lower()
        if not isinstance(params, dict):
            raise ValueError("params 必须为字典")
        params = deepcopy(params)
        if action in CARD_WRITE_MODELS:
            raise ValueError("卡片任务必须通过归属与版本预评估创建")
        # 风险由代码重新计算，不能通过传入 yellow 将红色操作降级。
        risk = self._assess(account_id, action, params)
        if risk_level != "yellow" or risk["risk_level"] != "yellow":
            raise ValueError("只有经过风险判断的 yellow 操作可以进入确认队列")
        return self._save_pending(account_id, action, params, audit_context)

    def _save_pending(self, account_id, action, params, audit_context):
        from card_management.authorization import current_conversation
        from card_management.storage import CardError
        record = {
            "action_id": uuid4().hex,
            "account_id": account_id,
            "action": action,
            "params": deepcopy(params),
            "risk_level": "yellow",
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        conversation = current_conversation()
        if conversation:
            record["conversation_id"] = conversation
        if action in CARD_TEMP_OPERATIONS:
            record["_expires"] = self._clock() + self._card_ttl
        context = deepcopy(audit_context) if audit_context else (
            self._audit_logger.context(account_id, action) if self._audit_logger else {}
        )
        context.update(action_id=record["action_id"], risk_level="yellow",
                       permission_action="pending_confirmation", confirmation_status="pending")
        record["_audit_context"] = context
        with self._lock:
            if conversation and any(item.get("conversation_id") == conversation and item["account_id"] == account_id for item in self._pending.values()):
                raise CardError("PENDING_EXISTS", "本会话已有待确认任务，请先确认或取消，不会替换原任务。")
            self._log("pending_created", context, details={"params": params})
            self._pending[record["action_id"]] = record
        return self._public_record(record)

    def get_pending_action(self, account_id: str, action_id: str) -> dict | None:
        account_id = self._identifier(account_id, "account_id")
        action_id = self._identifier(action_id, "action_id")
        with self._lock:
            record = self._pending.get(action_id)
            return self._public_record(record) if record and record["account_id"] == account_id else None

    def prepare_action(self, account_id: str, action: str, params: dict) -> dict:
        """已识别的操作 → 风险 → 权限；黄色只保存，绝不提前执行。"""
        from card_management.authorization import current_conversation
        from card_management.storage import CardError
        account_id = self._identifier(account_id, "account_id")
        action = self._identifier(action, "action").lower()
        if not isinstance(params, dict):
            raise ValueError("params 必须为字典")
        params = deepcopy(params)
        if action in CARD_WRITE_MODELS:
            try:
                if action in CARD_TEMP_OPERATIONS and not current_conversation():
                    raise CardError("SESSION_REQUIRED", "卡片操作必须绑定有效服务端会话。")
                params = prepare_card_params(account_id, action, params)
            except (CardError, ValidationError) as exc:
                result = card_error(exc) if isinstance(exc, CardError) else {"ok": False, "code": "INVALID_ARGUMENTS", "error": "卡片参数无效。"}
                context = self._audit_logger.context(account_id, action) if self._audit_logger else {}
                self._log("tool_validation_failed", context, execution_result="not_executed", details={"code": result["code"]})
                return {"status": "failed", "action": action, "message": result["error"], "result": result}
        risk = self._assess(account_id, action, params)
        context = self._audit_logger.context(account_id, action) if self._audit_logger else {}
        context["risk_level"] = risk["risk_level"]
        self._log("risk_evaluated", context, details={"reason": risk["reason"], "stage": "prepare",
                                                    "daily_limit": risk.get("daily_limit"), "risk_error": risk.get("risk_error")})
        permission = self._permissions.decide(action, risk["risk_level"])
        # 把同一次确定性判断交给 API 展示，不由 API 或模型重新推断权限。
        decision = {
            "operation": action, "risk_level": risk["risk_level"], "risk_reason": risk["reason"],
            "permission_action": permission["action"],
            "require_confirmation": permission["require_confirmation"],
            "require_verification": permission["require_verification"],
        }
        context["permission_action"] = permission["action"]
        context["confirmation_status"] = "pending" if risk["risk_level"] == "yellow" else (
            "verification_required" if risk["risk_level"] == "red" else "not_required"
        )
        self._log("permission_decided", context, details=permission)
        if permission["action"] == "allow":
            return {**self._execute(account_id, action, deepcopy(params), context), **decision}
        if permission["action"] == "require_verification":
            return {
                **decision, "status": "require_verification",
                "message": "该操作需要强验证，尚未执行。",
            }
        try:
            pending = self._save_pending(account_id, action, params, context)
        except CardError as exc:
            return {"status": "failed", "action": action, "message": str(exc), "result": card_error(exc), **decision}
        message = (f"模拟卡片 {params['card_id']} 的{'临时冻结' if action == 'temporarily_freeze_card' else '解除临时冻结'}需要确认，尚未执行。"
                   if action in CARD_TEMP_OPERATIONS else f"转账 {pending['params']['amount']} 元，需要用户确认，尚未执行。")
        return {
            **decision, "status": "pending",
            "message": message,
            "action_id": pending["action_id"], "pending_action": pending,
        }

    def confirm_action(self, account_id: str, action_id: str, confirmed: bool = True, *, conversation_id=None) -> dict:
        account_id = self._identifier(account_id, "account_id")
        action_id = self._identifier(action_id, "action_id")
        if type(confirmed) is not bool:
            raise ValueError("confirmed 必须为布尔值")
        with self._lock:
            record = self._pending.get(action_id)
            if record is None or record["account_id"] != account_id:
                context = self._audit_logger.context(account_id, "confirm_action" if confirmed else "cancel_action") if self._audit_logger else {}
                self._log("confirmation_rejected", context, action_id=action_id,
                          confirmation_status="not_found")
                return {"status": "not_found", "message": "待确认操作不存在或不属于当前账户。"}
            context = {**record["_audit_context"], "confirmation_status": "confirmed" if confirmed else "cancelled"}
            if record["action"] in CARD_TEMP_OPERATIONS:
                if conversation_id is None or conversation_id != record.get("conversation_id"):
                    self._log("confirmation_rejected", context, confirmation_status="session_mismatch")
                    return {"status": "failed", "action_id": action_id, "message": "必须在发起卡片操作的原会话确认或取消。",
                            "result": {"ok": False, "code": "SESSION_MISMATCH", "error": "卡片任务会话不匹配。"}}
                if self._clock() >= record["_expires"]:
                    self._log("confirmation_rejected", context, confirmation_status="expired", execution_result="not_executed")
                    self._pending.pop(action_id)
                    return {"status": "failed", "action_id": action_id, "message": "卡片待确认任务已过期，请重新查询并发起。",
                            "result": {"ok": False, "code": "ACTION_EXPIRED", "error": "卡片待确认任务已过期。"}}
            self._log("confirmation_confirmed" if confirmed else "confirmation_cancelled", context)
            if confirmed:
                # 审计不可用时不领取任务、不执行，恢复后仍可确认。
                self._log("tool_execution_started", context, execution_result="started",
                          details={"stage": "confirmation_gate", "funds_committed": False})
            # 执行前原子领取，防止双击、并发确认或取消触发重复资金操作。
            record = self._pending.pop(action_id)
        if not confirmed:
            response = {"status": "cancelled", "message": "操作已取消，未执行。", "action_id": action_id}
            if self._audit_logger:
                response["request_id"] = context["request_id"]
            return response
        response = self._execute(record["account_id"], record["action"], record["params"], context, started=True)
        if self._audit_logger:
            response["request_id"] = context["request_id"]
        return {**response, "action_id": action_id}

    def cancel_action(self, account_id: str, action_id: str, *, conversation_id=None) -> dict:
        return self.confirm_action(account_id, action_id, confirmed=False, conversation_id=conversation_id)

    def _execute(self, account_id: str, action: str, params: dict, context: dict, started: bool = False) -> dict:
        from card_management.authorization import confirmed_card_scope
        from card_management.storage import CardError
        if not started:
            self._log("tool_execution_started", context, execution_result="started")
        try:
            if action in RiskEngine.TRANSFER_OPERATIONS and started:
                with confirmed_transfer_scope(account_id, params, self._audit_logger, context):
                    result = self._executor(account_id, action, params)
            elif action in CARD_TEMP_OPERATIONS and started:
                with confirmed_card_scope(account_id, action, params, self._audit_logger, context):
                    result = self._executor(account_id, action, params)
            else:
                result = self._executor(account_id, action, params)
            if not isinstance(result, dict) or type(result.get("ok")) is not bool:
                raise ValueError("执行器返回值不符合工具协议")
        except CardError as exc:
            response = {"status": "failed", "action": action, "message": str(exc), "result": card_error(exc)}
        except Exception:
            # 不透露异常细节，也不声称已回滚；执行器可能已产生副作用。
            response = {
                "status": "execution_unknown", "action": action,
                "message": "操作执行结果未知，请按账户及action_id查询卡片操作凭据，不要自动重试。" if action in CARD_TEMP_OPERATIONS else "操作执行结果未知，请先查询账单，不要重复确认或重新转账。",
            }
        else:
            status = "completed" if result["ok"] else (
                "execution_unknown" if result.get("execution_unknown") else
                "require_verification" if result.get("code") == "VERIFICATION_REQUIRED" else "failed"
            )
            response = {"status": status, "action": action,
                        "message": "操作已完成。" if result["ok"] else result.get("error", "操作执行失败，请查看工具结果。"),
                        "result": result}
            if result.get("security_decision"):
                response["decision"] = result["security_decision"]
                if action in CARD_TEMP_OPERATIONS:
                    context.update(risk_level=response["decision"]["risk_level"], permission_action=response["decision"]["permission_action"])
                    if response["decision"]["require_verification"]:
                        context["confirmation_status"] = "verification_required"
        outcome = {"completed": "success", "failed": "failed", "execution_unknown": "unknown", "require_verification": "not_executed"}[response["status"]]
        if self._audit_logger and not self._audit_logger.log_after_execution(
            "tool_executed", context, execution_result=outcome,
            details={"result": response.get("result"), "status": response["status"]},
        ):
            response["audit_warning"] = AUDIT_WARNING
        return response
