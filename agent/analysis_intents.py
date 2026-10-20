"""有限的消费分析查询语法；日期来自用户原文或服务端时钟，不执行工具。"""

from dataclasses import dataclass
from datetime import timedelta
import re
import unicodedata

from bill_analysis.service import month_range
from bill_analysis.storage import AnalysisError, local_date


@dataclass(frozen=True)
class AnalysisResolution:
    tool: str | None
    arguments: dict
    code: str


def analysis_intent(message):
    text = unicodedata.normalize("NFKC", message)
    if re.match(r"^(?:请)?(?:解释|说明|介绍|为什么|什么是|如何理解)", text):
        return False
    if re.search(r"原始记录(?:ID|编号)?\s*[:：]?\s*[a-zA-Z][a-zA-Z0-9_-]*", text):
        return True
    return bool(re.search(r"消费|花钱|花销|开销|支出|商户|花了多少", text) and re.search(r"分析|统计|分类|排行|排名|最多|异常|关注|比较|对比|记录|明细|原始|花钱|查询|查看|多少", text))


def resolve_analysis_request(message, account_id):
    if not analysis_intent(message):
        return None
    text = unicodedata.normalize("NFKC", message)
    reject = lambda code: AnalysisResolution(None, {}, code)
    if any(value != account_id for value in re.findall(r"(?:账户|账号)\s*([0-9A-Za-z_-]+)", text)):
        return reject("ACCOUNT_MISMATCH")
    if re.search(r"不要|别分析|不统计|假设|如果|引用|昨天说过|转账|汇款|冻结|扣款给", text):
        return reject("INTENT_REJECTED")
    record = re.search(r"(?:记录|明细)(?:ID|编号)?\s*[:：]?\s*([a-zA-Z][a-zA-Z0-9_-]{0,63})", text)
    if record:
        return AnalysisResolution("get_spending_record", {"record_id": record[1]}, "ANALYSIS_READ")
    months = []
    for match in re.finditer(r"(?:(\d{4})[年-])?(\d{1,2})月|\b(\d{4})-(\d{2})(?![-\d])", text):
        year = match[1] or match[3] or (months[-1][:4] if months else None)
        number = match[2] or match[4]
        if year is None:
            return reject("ANALYSIS_DATE_REQUIRED")
        months.append(f"{year}-{int(number):02}")
    try:
        for month in months:
            month_range(month)
        if re.search(r"比较|对比", text):
            if len(months) != 2 or month_range(months[0])[1] != month_range(months[1])[0]:
                return reject("ANALYSIS_DATE_REQUIRED")
            through = re.search(r"前\s*(\d{1,2})\s*天", text)
            return AnalysisResolution("compare_spending", {"month": months[1], "through_day": int(through[1]) if through else None}, "ANALYSIS_READ")
        ranges = re.search(r"(\d{4}-\d{2}-\d{2})\s*(?:至|到)\s*(\d{4}-\d{2}-\d{2})", text)
        if ranges:
            start, end = local_date(ranges[1]), local_date(ranges[2]) + timedelta(days=1)
            period = f"{start.isoformat()}/{end.isoformat()}"
        elif len(months) == 1:
            period = months[0]
        elif len(months) > 1:
            return reject("ANALYSIS_DATE_REQUIRED")
        elif re.search(r"这个月|本月|当月", text):
            period = "this_month"
        elif "上个月" in text or "上月" in text:
            period = "last_month"
        elif "最近" in text:
            period = "recent"
        else:
            return reject("ANALYSIS_DATE_REQUIRED")
    except (AnalysisError, OverflowError):
        return reject("ANALYSIS_DATE_REQUIRED")
    if re.search(r"原始|明细|消费记录", text):
        return AnalysisResolution("list_spending_records", {"period": period, "limit": 20, "offset": 0, "category": None, "merchant_id": None}, "ANALYSIS_READ")
    view = "anomalies" if re.search(r"异常|关注", text) else "merchants" if re.search(r"商户|商家", text) else "summary"
    return AnalysisResolution("analyze_spending", {"period": period, "view": view}, "ANALYSIS_READ")
