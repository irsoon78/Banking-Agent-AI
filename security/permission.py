"""独立权限策略：消费可信风险等级，不执行工具或实现真实验证。"""

from typing import Literal, TypedDict


class PermissionDecision(TypedDict):
    action: Literal["allow", "pending_confirmation", "require_verification"]
    require_confirmation: bool
    require_verification: bool


class PermissionController:
    """将 Risk Engine 的结果转换为执行策略。

    risk_level 必须由服务端 Risk Engine 提供，不能使用 LLM 或用户
    自行声明的风险等级。operation 用于输入校验及未来操作策略扩展，
    操作识别和风险分类仍由前置模块负责。
    """

    _STRATEGIES = {
        "green": ("allow", False, False),
        "yellow": ("pending_confirmation", True, False),
        "red": ("require_verification", False, True),
    }

    def decide(self, operation: str, risk_level: str) -> PermissionDecision:
        """仅 allow 允许直接执行；其他结果必须等待后续确认或验证流程。"""
        if not isinstance(operation, str) or not operation.strip() or not isinstance(risk_level, str):
            strategy = self._STRATEGIES["red"]
        else:
            strategy = self._STRATEGIES.get(risk_level.strip().lower(), self._STRATEGIES["red"])
        action, confirmation, verification = strategy
        return {
            "action": action,
            "require_confirmation": confirmation,
            "require_verification": verification,
        }
