"""确定性风险分类；仅返回判断，不执行工具、确认用户或验证身份。"""

from decimal import Decimal, InvalidOperation, localcontext
from typing import Literal, TypedDict

Amount = int | float | str | Decimal | None


class RiskResult(TypedDict):
    risk_level: Literal["green", "yellow", "red"]
    reason: str


class RiskEngine:
    """根据操作、人民币本笔金额及可信日累计判断风险，未知或无效输入返回 red。

    后续可扩展操作集合及专用评估方法。理财、卡片管理、订阅管理
    暂不提供执行规则，先作为高风险操作保留。
    """

    QUERY_OPERATIONS = frozenset({
        "query", "query_balance", "query_bill", "query_bills", "get_balance", "get_bills",
        "analyze_spending", "compare_spending", "list_spending_records", "get_spending_record",
        "list_cards", "get_card", "get_card_for_spending_record", "get_card_operation",
    })
    CARD_TEMP_OPERATIONS = frozenset({"temporarily_freeze_card", "remove_temporary_freeze"})
    CARD_RED_OPERATIONS = frozenset({"report_card_loss", "cancel_card_loss_report"})
    TRANSFER_OPERATIONS = frozenset({"transfer", "transfer_money"})
    HIGH_RISK_OPERATIONS = frozenset({
        "high_risk", "investment", "card_management", "subscription_management",
    })
    TRANSFER_CONFIRMATION_LIMIT = Decimal("1000")

    def evaluate(self, operation: str, amount: Amount = None, *, daily_outgoing: Amount = 0) -> RiskResult:
        """金额单位为元；daily_outgoing 由服务端账本提供，默认零供纯策略测试使用。"""
        if not isinstance(operation, str) or not operation.strip():
            return {"risk_level": "red", "reason": "操作类型无效，需要强验证"}
        operation = operation.strip().lower()
        if operation in self.CARD_RED_OPERATIONS:
            return {"risk_level": "red", "reason": "正式挂失或解挂需要强验证，当前没有放行能力"}
        if operation in self.CARD_TEMP_OPERATIONS:
            return {"risk_level": "yellow", "reason": "模拟银行卡临时状态操作需要用户明确确认"}
        if operation in self.HIGH_RISK_OPERATIONS:
            return {"risk_level": "red", "reason": "高风险操作需要强验证"}
        if operation in self.QUERY_OPERATIONS:
            return {"risk_level": "green", "reason": "查询类操作可自动执行"}
        if operation in self.TRANSFER_OPERATIONS:
            return self._evaluate_transfer(amount, daily_outgoing)
        return {"risk_level": "red", "reason": "未识别的操作按高风险处理，需要强验证"}

    def _evaluate_transfer(self, amount: Amount, daily_outgoing: Amount) -> RiskResult:
        # Decimal 按十进制数值比较，避免阈值附近被舍入后降低风险。
        if isinstance(amount, bool) or not isinstance(amount, (int, float, str, Decimal)):
            return {"risk_level": "red", "reason": "转账金额缺失或无效，需要强验证"}
        try:
            value = Decimal(str(amount))
        except (InvalidOperation, ValueError):
            return {"risk_level": "red", "reason": "转账金额缺失或无效，需要强验证"}
        if not value.is_finite() or value <= 0:
            return {"risk_level": "red", "reason": "转账金额必须是有限正数，需要强验证"}
        if isinstance(daily_outgoing, bool) or not isinstance(daily_outgoing, (int, float, str, Decimal)):
            return {"risk_level": "red", "reason": "日累计金额无效，安全阻断"}
        try:
            total = Decimal(str(daily_outgoing))
        except (InvalidOperation, ValueError):
            return {"risk_level": "red", "reason": "日累计金额无效，安全阻断"}
        if not total.is_finite() or total < 0:
            return {"risk_level": "red", "reason": "日累计金额无效，安全阻断"}
        # 不让 Decimal 当前精度把阈值附近的非零尾数舍掉。
        with localcontext() as ctx:
            ctx.prec = max(28, len(value.as_tuple().digits) + abs(value.as_tuple().exponent) +
                           len(total.as_tuple().digits) + abs(total.as_tuple().exponent) + 4)
            within_limit = total + value <= self.TRANSFER_CONFIRMATION_LIMIT
        if within_limit:
            return {"risk_level": "yellow", "reason": "日累计转账不超过1000元，需要用户确认"}
        return {"risk_level": "red", "reason": "日累计转账超过1000元，需要强验证"}
