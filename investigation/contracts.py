"""输入和合法步骤契约。账户、结果、风险和数据库位置不属于计划参数。"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from bill_analysis.service import date_range
from bill_analysis.storage import AnalysisError

ReadTool = Literal["analyze_spending", "get_spending_record", "get_card_for_spending_record", "get_card"]
READ_TOOLS = frozenset({"analyze_spending", "get_spending_record", "get_card_for_spending_record", "get_card"})
RULE_FOCUS = {"SIMILAR_PAYMENT_V1": "similar", "LARGE_PURCHASE_V1": "large"}
MAX_FINDINGS = 64
MAX_DISCOVERED_RECORDS = 128


class InvestigationError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


class InvestigationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    start_date: str
    end_date_exclusive: str
    focus: Literal["similar", "large", "all"] = "all"
    max_records: int = Field(default=6, ge=1, le=6)
    max_tool_calls: int = Field(default=20, ge=1, le=20)

    @model_validator(mode="after")
    def valid_range(self):
        # 复用日期校验与366天跨度限制，不接受含糊的月份或模型时钟。
        try:
            date_range(self.start_date, self.end_date_exclusive)
        except AnalysisError as exc:
            raise ValueError(str(exc)) from None
        return self


class StepCommand(BaseModel):
    """第二批可选的规划接缝：只能选择本次调查已生成且依赖就绪的步骤。"""
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    step_id: str = Field(pattern=r"^step-[a-f0-9]{32}$")
    tool: ReadTool
    evidence_ref: str | None = Field(default=None, pattern=r"^evidence-[a-f0-9]{32}$")


class PlannerSelection(StepCommand):
    focus: Literal["similar", "large", "all"]
    reason_code: Literal["follow_dependency", "prioritize_similar", "prioritize_large", "check_card", "continue"]
