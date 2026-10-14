"""确定性风险分类；仅返回判断，不执行工具、确认用户或验证身份。"""

from decimal import Decimal, InvalidOperation
from typing import Literal, TypedDict

Amount = int | float | str | Decimal | None


class RiskResult(TypedDict):
    risk_level: Literal["green", "yellow", "red"]
    reason: str


class RiskEngine:
    """根据操作及人民币金额判断风险，未知或无效输入保守返回 red。

    后续可扩展操作集合及专用评估方法。理财、卡片管理、订阅管理
    暂不提供执行规则，先作为高风险操作保留。
    """

    QUERY_OPERATIONS = frozenset({
        "query", "query_balance", "query_bill", "query_bills", "get_balance", "get_bills",
    })
    TRANSFER_OPERATIONS = frozenset({"transfer", "transfer_money"})
    HIGH_RISK_OPERATIONS = frozenset({
        "high_risk", "investment", "card_management", "subscription_management",
    })
    TRANSFER_CONFIRMATION_LIMIT = Decimal("1000")

    def evaluate(self, operation: str, amount: Amount = None) -> RiskResult:
        """返回 risk_level 和 reason；金额为元，不进行舍入或资金操作。"""
        if not isinstance(operation, str) or not operation.strip():
            return {"risk_level": "red", "reason": "操作类型无效，需要强验证"}
        operation = operation.strip().lower()
        if operation in self.HIGH_RISK_OPERATIONS:
            return {"risk_level": "red", "reason": "高风险操作需要强验证"}
        if operation in self.QUERY_OPERATIONS:
            return {"risk_level": "green", "reason": "查询类操作可自动执行"}
        if operation in self.TRANSFER_OPERATIONS:
            return self._evaluate_transfer(amount)
        return {"risk_level": "red", "reason": "未识别的操作按高风险处理，需要强验证"}

    def _evaluate_transfer(self, amount: Amount) -> RiskResult:
        # Decimal 按十进制数值比较，避免阈值附近被舍入后降低风险。
        if isinstance(amount, bool) or not isinstance(amount, (int, float, str, Decimal)):
            return {"risk_level": "red", "reason": "转账金额缺失或无效，需要强验证"}
        try:
            value = Decimal(str(amount))
        except (InvalidOperation, ValueError):
            return {"risk_level": "red", "reason": "转账金额缺失或无效，需要强验证"}
        if not value.is_finite() or value <= 0:
            return {"risk_level": "red", "reason": "转账金额必须是有限正数，需要强验证"}
        if value <= self.TRANSFER_CONFIRMATION_LIMIT:
            return {"risk_level": "yellow", "reason": "小额转账需要用户确认"}
        return {"risk_level": "red", "reason": "大额转账需要强验证"}
