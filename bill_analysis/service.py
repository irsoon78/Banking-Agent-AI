"""按服务端给定账户读取分析样本；不创建库、不写表、不调用资金工具。"""

from collections import defaultdict
from contextlib import contextmanager
from dataclasses import asdict
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP, localcontext
from pathlib import Path
import re
import sqlite3

from database.db import format_money
from security.daily_limit import BUSINESS_TIMEZONE
from bill_analysis.storage import (
    AnalysisError, CATEGORIES, Dataset, SpendingRecord, identifier, local_date,
    utc_boundary, utc_instant, validate_dataset, validate_record,
)

LARGE_PURCHASE_CENTS = 100_000
SIMILAR_PAYMENT_SECONDS = 300
DISCLAIMER = "规则命中仅提示核对，不代表诈骗、欺诈或重复扣款已被确认。"


def date_range(start_date, end_date_exclusive):
    start, end = local_date(start_date), local_date(end_date_exclusive)
    if not 0 < (end - start).days <= 366:
        raise AnalysisError("INVALID_ARGUMENTS", "查询范围必须为1至366天，结束日期不包含在内。")
    return start, end


def month_range(month):
    if not isinstance(month, str) or not re.fullmatch(r"\d{4}-\d{2}", month):
        raise AnalysisError("INVALID_ARGUMENTS", "月份必须为YYYY-MM。")
    start = local_date(month + "-01")
    try:
        end = date(start.year + 1, 1, 1) if start.month == 12 else date(start.year, start.month + 1, 1)
    except ValueError:
        raise AnalysisError("INVALID_ARGUMENTS", "月份超出支持范围。") from None
    return start, end


def percentage(numerator, denominator):
    if not denominator:
        return None
    with localcontext() as ctx:
        ctx.prec = max(28, len(str(abs(numerator))) + len(str(abs(denominator))) + 10)
        return str((Decimal(numerator) * 100 / Decimal(denominator)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def amount_fields(cents):
    # 累计分值用十进制字符串传输，避免未来JS客户端丢失大整数精度。
    return {"amount_cents": str(cents), "amount": format_money(cents)}


def public_record(record):
    value = asdict(record)
    value.update(amount_fields(record.amount_cents))
    value["business_date"] = utc_instant(record.occurred_at).astimezone(BUSINESS_TIMEZONE).date().isoformat()
    return value


class AnalysisService:
    """account_id只能由未来服务端上下文注入，不能直接采用LLM工具参数。

    本类实施账户范围过滤，不负责登录认证。db_path必须显式提供。
    每次读取使用独立只读连接和单个快照，异常返回AnalysisError而非虚构零值。
    """

    def __init__(self, db_path, *, account_id):
        self._path = Path(db_path).resolve()
        self._account_id = identifier(account_id)

    @contextmanager
    def _read(self):
        conn = None
        try:
            conn = sqlite3.connect(self._path.as_uri() + "?mode=ro", uri=True, timeout=2)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA query_only=ON")
            conn.execute("BEGIN")
            yield conn
        except (sqlite3.Error, OSError):
            raise AnalysisError("ANALYSIS_UNAVAILABLE", "分析数据库暂时无法可靠读取，未生成统计结果。") from None
        finally:
            if conn is not None:
                conn.close()

    def _dataset(self, conn, dataset_id):
        identifier(dataset_id)
        row = conn.execute("SELECT * FROM analysis_datasets WHERE dataset_id=? AND account_id=?", (dataset_id, self._account_id)).fetchone()
        if row is None:
            raise AnalysisError("DATASET_NOT_FOUND", "当前账户没有此分析数据集。")
        dataset = Dataset(**{name: row[name] for name in Dataset.__dataclass_fields__})
        validate_dataset(dataset)
        # 损坏的时间不能被范围过滤静默排除后当成零；检查整个所选数据集。
        invalid = conn.execute(
            "SELECT COUNT(*) FROM spending_records WHERE dataset_id=? AND "
            "(account_id!=? OR occurred_at NOT GLOB '????-??-??T??:??:??.???Z' OR "
            "julianday(occurred_at) IS NULL OR occurred_at<? OR occurred_at>=? OR "
            "typeof(amount_cents)!='integer' OR amount_cents<=0 OR "
            "record_type!='purchase' OR status!='posted' OR currency!='CNY')",
            (dataset_id, self._account_id, utc_boundary(local_date(dataset.coverage_start)), utc_boundary(local_date(dataset.coverage_end_exclusive))),
        ).fetchone()[0]
        if invalid:
            raise AnalysisError("INVALID_DATA", "数据集含无法可靠统计的记录，未生成分析结果。")
        return dataset

    def current_dataset(self):
        """只选择当前账户唯一的受支持样本；不让模型指定数据集或猜测版本。"""
        with self._read() as conn:
            rows = conn.execute("SELECT dataset_id FROM analysis_datasets WHERE account_id=? AND source=?", (self._account_id, "synthetic_demo")).fetchall()
            if not rows:
                raise AnalysisError("DATASET_NOT_FOUND", "当前账户没有消费分析样本，请先离线初始化独立演示库。")
            if len(rows) != 1:
                raise AnalysisError("DATASET_AMBIGUOUS", "当前账户有多个样本版本，需要服务端明确选择，未自动合并。")
            return asdict(self._dataset(conn, rows[0]["dataset_id"]))

    @staticmethod
    def _record(row, dataset):
        record = SpendingRecord(**{name: row[name] for name in SpendingRecord.__dataclass_fields__})
        validate_record(record, dataset)
        return record

    def _report(self, conn, dataset, start, end):
        rows = conn.execute(
            "SELECT * FROM spending_records WHERE account_id=? AND dataset_id=? AND occurred_at>=? AND occurred_at<? ORDER BY occurred_at,record_id",
            (self._account_id, dataset.dataset_id, utc_boundary(start), utc_boundary(end)),
        ).fetchall()
        records = [self._record(row, dataset) for row in rows]
        coverage_start, coverage_end = local_date(dataset.coverage_start), local_date(dataset.coverage_end_exclusive)
        complete = coverage_start <= start and end <= coverage_end
        covered_days = max(0, (min(end, coverage_end) - max(start, coverage_start)).days)
        total = sum(r.amount_cents for r in records)
        categories, merchants, days = {}, {}, defaultdict(lambda: [0, 0])
        for record in records:
            category = categories.setdefault(record.category, {"category": record.category, "label": CATEGORIES[record.category], "count": 0, "cents": 0})
            merchant = merchants.setdefault(record.merchant_id, {"merchant_id": record.merchant_id, "merchant_name": record.merchant_name, "count": 0, "cents": 0})
            if merchant["merchant_name"] != record.merchant_name:
                raise AnalysisError("INVALID_DATA", "商户标识与名称不一致，无法可靠统计。")
            for group in (category, merchant):
                group["count"] += 1
                group["cents"] += record.amount_cents
            day = utc_instant(record.occurred_at).astimezone(BUSINESS_TIMEZONE).date()
            days[day][0] += record.amount_cents
            days[day][1] += 1

        def groups(values, tie_key):
            result = []
            for item in sorted(values, key=lambda v: (-v["cents"], v[tie_key])):
                cents = item["cents"]
                result.append({**{key: value for key, value in item.items() if key != "cents"}, **amount_fields(cents), "share_percent": percentage(cents, total)})
            return result

        trend = []
        for index in range((end - start).days):
            day = start + timedelta(days=index)
            covered = coverage_start <= day < coverage_end
            cents, count = days[day]
            trend.append({"date": day.isoformat(), "covered": covered, "count": count if covered else None,
                          "amount_cents": str(cents) if covered else None, "amount": format_money(cents) if covered else None,
                          "share_percent": percentage(cents, total) if covered else None})
        return {
            "ok": True, "account_id": self._account_id, "dataset": asdict(dataset), "currency": "CNY", "time_zone": "Asia/Shanghai",
            "period": {"start_date": start.isoformat(), "end_date_exclusive": end.isoformat(), "start_utc": utc_boundary(start), "end_utc_exclusive": utc_boundary(end)},
            "coverage": {"complete": complete, "requested_days": (end - start).days, "covered_days": covered_days,
                         "code": "COVERED_SAMPLE_PERIOD" if complete else "INCOMPLETE_COVERAGE",
                         "note": dataset.coverage_note + ("" if complete else " 请求范围有未覆盖日期，统计仅为已记录金额，不能解释为整段总消费。")},
            "summary": {"count": len(records), **amount_fields(total), "basis": "recorded_sample_purchases"},
            "categories": groups(categories.values(), "category"), "merchants": groups(merchants.values(), "merchant_id"),
            "daily_trend": trend, "anomalies": self._anomalies(records), "anomaly_disclaimer": DISCLAIMER,
        }

    @staticmethod
    def _anomalies(records):
        findings, previous = [], {}
        for record in records:
            if record.amount_cents >= LARGE_PURCHASE_CENTS:
                findings.append({"rule_id": "LARGE_PURCHASE_V1", "record_ids": [record.record_id],
                                 "reason": "该条已记录消费达到或超过1000.00元，请核对用途。此阈值不属于转账风险规则。",
                                 "threshold_cents": str(LARGE_PURCHASE_CENTS)})
            key = (record.merchant_id, record.amount_cents)
            prior = previous.get(key)
            if prior:
                interval = utc_instant(record.occurred_at) - utc_instant(prior.occurred_at)
                if interval <= timedelta(seconds=SIMILAR_PAYMENT_SECONDS):
                    findings.append({"rule_id": "SIMILAR_PAYMENT_V1", "record_ids": [prior.record_id, record.record_id],
                                     "reason": "所选范围内同商户同金额的两条消费间隔不超过5分钟，请核对是否符合实际需求。",
                                     "window_seconds": SIMILAR_PAYMENT_SECONDS})
            previous[key] = record
        return findings

    def analyze_range(self, dataset_id, start_date, end_date_exclusive):
        start, end = date_range(start_date, end_date_exclusive)
        with self._read() as conn:
            return self._report(conn, self._dataset(conn, dataset_id), start, end)

    def analyze_month(self, dataset_id, month):
        start, end = month_range(month)
        return self.analyze_range(dataset_id, start.isoformat(), end.isoformat())

    def compare_months(self, dataset_id, month, *, through_day=None):
        """与相邻上月比较；through_day明确限定两个月相同的前N天。"""
        current_start, current_end = month_range(month)
        try:
            previous_start, previous_end = month_range((current_start - timedelta(days=1)).strftime("%Y-%m"))
        except OverflowError:
            raise AnalysisError("INVALID_ARGUMENTS", "没有可比较的上月。") from None
        if through_day is not None:
            if type(through_day) is not int or not 1 <= through_day <= min((current_end - current_start).days, (previous_end - previous_start).days):
                raise AnalysisError("INVALID_ARGUMENTS", "对比天数必须在两个月内均有效。")
            current_end, previous_end = current_start + timedelta(days=through_day), previous_start + timedelta(days=through_day)
        with self._read() as conn:
            dataset = self._dataset(conn, dataset_id)
            previous = self._report(conn, dataset, previous_start, previous_end)
            current = self._report(conn, dataset, current_start, current_end)
        comparable = previous["coverage"]["complete"] and current["coverage"]["complete"]
        before, after = int(previous["summary"]["amount_cents"]), int(current["summary"]["amount_cents"])
        delta = after - before
        return {"ok": True, "account_id": self._account_id, "mode": "same_first_n_days" if through_day is not None else "full_month",
                "through_day": through_day, "comparable": comparable,
                "reason_code": "INCOMPLETE_COVERAGE" if not comparable else "ZERO_BASELINE" if before == 0 else "COMPARABLE",
                "previous": previous, "current": current,
                "change_cents": str(delta) if comparable else None, "change_amount": format_money(delta) if comparable else None,
                "change_percent": percentage(delta, before) if comparable else None}

    def list_records(self, dataset_id, start_date, end_date_exclusive, *, limit=20, offset=0, category=None, merchant_id=None):
        start, end = date_range(start_date, end_date_exclusive)
        if type(limit) is not int or not 1 <= limit <= 100 or type(offset) is not int or offset < 0:
            raise AnalysisError("INVALID_ARGUMENTS", "分页参数无效。")
        if category is not None and (not isinstance(category, str) or category not in CATEGORIES):
            raise AnalysisError("INVALID_ARGUMENTS", "分类无效。")
        if merchant_id is not None:
            identifier(merchant_id)
        where = "account_id=? AND dataset_id=? AND occurred_at>=? AND occurred_at<?"
        params = [self._account_id, dataset_id, utc_boundary(start), utc_boundary(end)]
        if category is not None:
            where += " AND category=?"
            params.append(category)
        if merchant_id is not None:
            where += " AND merchant_id=?"
            params.append(merchant_id)
        with self._read() as conn:
            dataset = self._dataset(conn, dataset_id)
            count = conn.execute("SELECT COUNT(*) FROM spending_records WHERE " + where, params).fetchone()[0]
            rows = conn.execute("SELECT * FROM spending_records WHERE " + where + " ORDER BY occurred_at DESC,record_id LIMIT ? OFFSET ?", (*params, limit, offset)).fetchall()
            items = [public_record(self._record(row, dataset)) for row in rows]
        return {"ok": True, "account_id": self._account_id, "dataset_id": dataset_id, "total_count": count, "limit": limit, "offset": offset, "records": items}

    def get_record(self, dataset_id, record_id):
        identifier(record_id)
        with self._read() as conn:
            dataset = self._dataset(conn, dataset_id)
            row = conn.execute("SELECT * FROM spending_records WHERE account_id=? AND dataset_id=? AND record_id=?", (self._account_id, dataset_id, record_id)).fetchone()
            if row is None:
                raise AnalysisError("RECORD_NOT_FOUND", "当前数据集中没有此消费记录。")
            return {"ok": True, "account_id": self._account_id, "dataset_id": dataset_id, "record": public_record(self._record(row, dataset))}
