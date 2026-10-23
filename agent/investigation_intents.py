"""调查专用入口。日期和用户范围由服务端限定，优先于单项卡片/分析路由。"""

from dataclasses import dataclass
from datetime import timedelta
import re
import unicodedata

from bill_analysis.service import date_range, month_range
from bill_analysis.storage import AnalysisError, local_date
from bill_analysis.tools import resolved_period
from investigation.contracts import InvestigationRequest


@dataclass(frozen=True)
class InvestigationIntent:
    options: InvestigationRequest | None
    code: str


def resolve_investigation_request(message, account_id):
    text = unicodedata.normalize("NFKC", message).strip()
    if not (re.search(r"调查|检查", text) and re.search(r"消费|重复扣款", text)):
        return None
    reject = lambda code: InvestigationIntent(None, code)
    if any(value != account_id for value in re.findall(r"(?:账户|账号)\s*([0-9A-Za-z_-]+)", text)):
        return reject("ACCOUNT_MISMATCH")
    if re.search(r'''不要|别调查|别检查|不想|假设|如果|假如|引用|昨天|说过|["'“”‘’`]|冻结|解冻|挂失|解挂|转账|转给|汇款|打款|扣款给|确认执行''', text):
        return reject("INVESTIGATION_INTENT_REJECTED")
    try:
        dates = re.search(r"(\d{4}-\d{2}-\d{2})\s*(?:至|到)\s*(\d{4}-\d{2}-\d{2})", text)
        if re.search(r"只看|仅限|排除|除了|仅调查.*(?:餐饮|交通|购物)|只调查.*(?:餐饮|交通|购物)", text):
            return reject("INVESTIGATION_SCOPE_UNSUPPORTED")
        if dates:
            if len(re.findall(r"\d{4}-\d{2}-\d{2}", text)) != 2 or re.search(r"上月|本月|这个月|最近|\d{4}年|之前|之后|以来|截至|截止", text):
                return reject("INVESTIGATION_DATE_REQUIRED")
            # 用户“至”采用包含结束日；内部始终是左闭右开。
            start, end = local_date(dates[1]), local_date(dates[2]) + timedelta(days=1)
            _ = date_range(start.isoformat(), end.isoformat())
        else:
            # 不把无法完整解释的日范围或多个时间条件悄悄扩大成整月。
            if re.search(r"\d{4}-\d{2}-\d{2}|\d{1,2}\s*[日号]|前\s*\d+\s*天|截至|截止|之前|之后|以后", text):
                return reject("INVESTIGATION_DATE_REQUIRED")
            month_matches = list(re.finditer(r"(\d{4})年\s*(\d{1,2})月|\b(\d{4})-(\d{2})(?![-\d])", text))
            months = [match.groups() for match in month_matches]
            if len(months) == 1:
                match = month_matches[0]
                remainder = re.sub(r"(?:账户|账号)\s*[0-9A-Za-z_-]+", "", text[:match.start()] + text[match.end():])
                if re.search(r"\d|上个月|上月|本月|这个月|最近", remainder):
                    return reject("INVESTIGATION_DATE_REQUIRED")
                year, month, iso_year, iso_month = months[0]
                start, end = month_range(f"{year or iso_year}-{int(month or iso_month):02}")
            elif months:
                return reject("INVESTIGATION_DATE_REQUIRED")
            elif re.search(r"\d{1,2}月", text):
                return reject("INVESTIGATION_DATE_REQUIRED")
            elif re.search(r"\d", re.sub(r"(?:账户|账号)\s*[0-9A-Za-z_-]+", "", text)) or len(set(re.findall(r"这个月|本月|上个月|上月|最近", text))) > 1:
                return reject("INVESTIGATION_DATE_REQUIRED")
            elif re.search(r"这个月|本月", text):
                start, end = resolved_period("this_month")
            elif re.search(r"上个月|上月", text):
                start, end = resolved_period("last_month")
            elif "最近" in text:
                start, end = resolved_period("recent")
            else:
                return reject("INVESTIGATION_DATE_REQUIRED")
        similar = bool(re.search(r"相似|重复", text))
        large = "大额" in text
        focus = "all" if similar == large else "similar" if similar else "large"
        return InvestigationIntent(InvestigationRequest(start_date=start.isoformat(), end_date_exclusive=end.isoformat(), focus=focus), "INVESTIGATION_REQUEST")
    except (AnalysisError, ValueError, OverflowError):
        return reject("INVESTIGATION_DATE_REQUIRED")


def investigation_clarification(code):
    return {"status": "needs_input", "response_kind": "clarification", "tool_results": [], "diagnostic_codes": [code],
        "reply": ("请明确调查年份和日期范围，例如调查2026年10月的相似消费；不会由模型补造年份。" if code == "INVESTIGATION_DATE_REQUIRED" else
                  "调查账户与当前模拟账户不一致，未查询其他账户。" if code == "ACCOUNT_MISMATCH" else
                  "调查只进行只读核对。请单独明确调查范围，不会把否定、假设或复合处置请求作为执行授权。")}
