"""严格只读工具契约与确定性展示；API和Agent共用。"""

from datetime import timedelta
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from bill_analysis.service import AnalysisService, date_range, month_range
from bill_analysis.storage import AnalysisError, CATEGORIES
from database.db import database_path
from security.daily_limit import BUSINESS_TIMEZONE, utc_now


class AnalysisArgs(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    period: str = Field(min_length=1, max_length=32, description="YYYY-MM、this_month、last_month、recent，或开始日/不包含的结束日")
    view: Literal["summary", "merchants", "anomalies"]


class CompareArgs(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    month: str = Field(min_length=1, max_length=16, description="较晚月份YYYY-MM或this_month，与相邻上月比较")
    through_day: int | None = Field(ge=1, le=31, description="两个相邻月份都只比较前N天；整月用null")


class RecordsArgs(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    period: str = Field(min_length=1, max_length=32)
    limit: int = Field(ge=1, le=100)
    offset: int = Field(ge=0, le=100000)
    category: Literal["dining", "transport", "shopping", "housing", "other", "uncategorized"] | None
    merchant_id: str | None = Field(min_length=1, max_length=64)


class RecordArgs(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    record_id: str = Field(min_length=1, max_length=64)


ANALYSIS_MODELS = {"analyze_spending": AnalysisArgs, "compare_spending": CompareArgs,
                   "list_spending_records": RecordsArgs, "get_spending_record": RecordArgs}
READ_ONLY_TOOLS = frozenset({"get_balance", "get_bills", *ANALYSIS_MODELS})


def resolved_month(value):
    today = utc_now().astimezone(BUSINESS_TIMEZONE).date()
    if value == "this_month":
        return today.strftime("%Y-%m")
    if value == "last_month":
        return (today.replace(day=1) - timedelta(days=1)).strftime("%Y-%m")
    month_range(value)
    return value


def resolved_period(value):
    if value == "recent":
        today = utc_now().astimezone(BUSINESS_TIMEZONE).date()
        return today - timedelta(days=29), today + timedelta(days=1)
    if "/" in value:
        parts = value.split("/")
        if len(parts) != 2:
            raise AnalysisError("INVALID_ARGUMENTS", "日期范围格式无效。")
        return date_range(*parts)
    return month_range(resolved_month(value))


def execute_analysis_tool(name, account_id, args):
    """account_id由现有服务端调用上下文注入；没有路径、SQL或数据集参数。"""
    try:
        service = AnalysisService(database_path(), account_id=account_id)
        dataset = service.current_dataset()
        dataset_id = dataset["dataset_id"]
        if name == "analyze_spending":
            start, end = resolved_period(args["period"])
            report = service.analyze_range(dataset_id, start.isoformat(), end.isoformat())
            return {"ok": True, "analysis": report, "view": args["view"]}
        if name == "compare_spending":
            result = service.compare_months(dataset_id, resolved_month(args["month"]), through_day=args["through_day"])
            return {"ok": True, "comparison": result}
        if name == "list_spending_records":
            start, end = resolved_period(args["period"])
            page = service.list_records(dataset_id, start.isoformat(), end.isoformat(), **{k: args[k] for k in ("limit", "offset", "category", "merchant_id")})
            page.update(dataset=dataset, time_zone="Asia/Shanghai", period={"start_date": start.isoformat(), "end_date_exclusive": end.isoformat()})
            return {"ok": True, "spending_records": page}
        if name == "get_spending_record":
            result = service.get_record(dataset_id, args["record_id"])
            result.update(dataset=dataset, time_zone="Asia/Shanghai")
            return {"ok": True, "spending_record": result}
        raise AnalysisError("UNKNOWN_TOOL", "不支持的分析工具。")
    except AnalysisError as exc:
        return {"ok": False, "code": exc.code, "error": str(exc)}


def render_analysis(result):
    label = "模拟消费分析样本，不参与当前账户余额核算。"
    if "analysis" in result:
        report = result["analysis"]
        period = report["period"]
        lines = [label, f"北京时间 {period['start_date']} 至 {period['end_date_exclusive']}（结束日不含）：已记录 {report['summary']['count']} 笔，合计 {report['summary']['amount']} 元。", report["coverage"]["note"]]
        if result["view"] == "merchants":
            lines.extend(f"{g['merchant_name']}：{g['amount']} 元，{g['count']}笔。" for g in report["merchants"])
        elif result["view"] == "anomalies":
            lines.extend(f"{f['rule_id']}：{f['reason']} 记录：{', '.join(f['record_ids'])}" for f in report["anomalies"])
            if not report["anomalies"]:
                lines.append("所选范围内未命中已实现的提示规则，不代表不存在其他风险。")
            lines.append(report["anomaly_disclaimer"])
        else:
            lines.extend(f"{g['label']}：{g['amount']} 元，{g['count']}笔。" for g in report["categories"])
        return "\n".join(lines)
    if "comparison" in result:
        c = result["comparison"]
        if not c["comparable"]:
            return label + " 两期数据覆盖不足，不能生成完整月份比较结论。请明确使用有覆盖的相同日期口径。"
        before, after = c["previous"], c["current"]
        return (label + f" 比较口径：{c['mode']}，前{c['through_day']}天。" if c["through_day"] else label + " 比较口径：两个相邻完整月份。") + f"\n前期 {before['summary']['amount']} 元，本期 {after['summary']['amount']} 元；变化 {c['change_amount']} 元，变化率 {c['change_percent'] + '%' if c['change_percent'] is not None else '不适用（前期为零）'}。"
    if "spending_record" in result:
        r = result["spending_record"]["record"]
        return label + f"\n原始记录 {r['record_id']}：{r['business_date']}，{r['merchant_name']}，{CATEGORIES[r['category']]}，{r['amount']}元。"
    page = result["spending_records"]
    return label + f"\n匹配 {page['total_count']} 条，当前返回 {len(page['records'])} 条原始消费记录。"
