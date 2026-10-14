"""待确认操作及黄色确认流程；不依赖 LLM、HTTP 或数据库。"""

from copy import deepcopy
from datetime import datetime, timezone
from threading import Lock
from typing import Callable
from uuid import uuid4

from security.permission import PermissionController
from security.risk_engine import RiskEngine

Executor = Callable[[str, str, dict], dict]


class PendingActionManager:
    """executor(account_id, action, params) 由服务端注册，不能由模型指定。

    待确认任务只存在于本进程内存。领取任务时原子移除，确保一个任务
    最多调用一次 executor；执行结果未知时也不自动重试。
    """

    def __init__(self, executor: Executor):
        self._executor = executor
        self._pending: dict[str, dict] = {}
        self._lock = Lock()
        self._risk_engine = RiskEngine()
        self._permissions = PermissionController()

    @staticmethod
    def _identifier(value: str, name: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{name} 不能为空")
        return value.strip()

    def create_pending_action(self, account_id: str, action: str, params: dict, risk_level: str) -> dict:
        account_id = self._identifier(account_id, "account_id")
        action = self._identifier(action, "action").lower()
        if not isinstance(params, dict):
            raise ValueError("params 必须为字典")
        params = deepcopy(params)
        # 风险由代码重新计算，不能通过传入 yellow 将红色操作降级。
        risk = self._risk_engine.evaluate(action, params.get("amount"))
        if risk_level != "yellow" or risk["risk_level"] != "yellow":
            raise ValueError("只有经过风险判断的 yellow 操作可以进入确认队列")
        record = {
            "action_id": uuid4().hex,
            "account_id": account_id,
            "action": action,
            "params": deepcopy(params),
            "risk_level": "yellow",
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        with self._lock:
            self._pending[record["action_id"]] = record
        return deepcopy(record)

    def get_pending_action(self, account_id: str, action_id: str) -> dict | None:
        account_id = self._identifier(account_id, "account_id")
        action_id = self._identifier(action_id, "action_id")
        with self._lock:
            record = self._pending.get(action_id)
            return deepcopy(record) if record and record["account_id"] == account_id else None

    def prepare_action(self, account_id: str, action: str, params: dict) -> dict:
        """已识别的操作 → 风险 → 权限；黄色只保存，绝不提前执行。"""
        account_id = self._identifier(account_id, "account_id")
        action = self._identifier(action, "action").lower()
        if not isinstance(params, dict):
            raise ValueError("params 必须为字典")
        params = deepcopy(params)
        risk = self._risk_engine.evaluate(action, params.get("amount"))
        permission = self._permissions.decide(action, risk["risk_level"])
        if permission["action"] == "allow":
            return self._execute(account_id, action, deepcopy(params))
        if permission["action"] == "require_verification":
            return {
                "status": "require_verification", "risk_level": "red",
                "message": "该操作需要强验证，尚未执行。",
            }
        pending = self.create_pending_action(account_id, action, params, risk["risk_level"])
        return {
            "status": "pending", "risk_level": "yellow",
            "message": f"转账 {pending['params']['amount']} 元，需要用户确认，尚未执行。",
            "action_id": pending["action_id"], "pending_action": pending,
        }

    def confirm_action(self, account_id: str, action_id: str, confirmed: bool = True) -> dict:
        account_id = self._identifier(account_id, "account_id")
        action_id = self._identifier(action_id, "action_id")
        if type(confirmed) is not bool:
            raise ValueError("confirmed 必须为布尔值")
        with self._lock:
            record = self._pending.get(action_id)
            if record is None or record["account_id"] != account_id:
                return {"status": "not_found", "message": "待确认操作不存在或不属于当前账户。"}
            # 执行前原子领取，防止双击、并发确认或取消触发重复资金操作。
            record = self._pending.pop(action_id)
        if not confirmed:
            return {"status": "cancelled", "message": "操作已取消，未执行。", "action_id": action_id}
        response = self._execute(record["account_id"], record["action"], record["params"])
        return {**response, "action_id": action_id}

    def cancel_action(self, account_id: str, action_id: str) -> dict:
        return self.confirm_action(account_id, action_id, confirmed=False)

    def _execute(self, account_id: str, action: str, params: dict) -> dict:
        try:
            result = self._executor(account_id, action, params)
            if not isinstance(result, dict) or type(result.get("ok")) is not bool:
                raise ValueError("执行器返回值不符合工具协议")
        except Exception:
            # 不透露异常细节，也不声称已回滚；执行器可能已产生副作用。
            return {
                "status": "execution_unknown", "action": action,
                "message": "操作执行结果未知，请先查询账单，不要重复确认或重新转账。",
            }
        return {
            "status": "completed" if result["ok"] else "failed", "action": action,
            "message": "操作已完成。" if result["ok"] else "操作执行失败，请查看工具结果。",
            "result": result,
        }
