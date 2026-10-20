"""仅由显式离线初始化命令调用的分析表与不可变数据集导入。"""

from dataclasses import asdict, dataclass
from datetime import date, datetime, time, timezone
import hashlib
import json
import re
import sqlite3

from security.daily_limit import BUSINESS_TIMEZONE

CATEGORIES = {"dining": "餐饮", "transport": "交通", "shopping": "购物", "housing": "居住", "other": "其他", "uncategorized": "未分类"}
SOURCE = "synthetic_demo"
MAX_AMOUNT_CENTS = 9_000_000_000_000


class AnalysisError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", value):
        raise AnalysisError("INVALID_ARGUMENTS", "标识无效。")
    return value


def local_date(value):
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise AnalysisError("INVALID_ARGUMENTS", "日期必须为YYYY-MM-DD。")
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise AnalysisError("INVALID_ARGUMENTS", "日期无效。") from None


def stamp(value):
    return value.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def utc_boundary(value):
    try:
        return stamp(datetime.combine(value, time.min, BUSINESS_TIMEZONE))
    except (ValueError, OverflowError):
        raise AnalysisError("INVALID_ARGUMENTS", "日期超出可转换的业务时间范围。") from None


def utc_instant(value):
    try:
        if not isinstance(value, str):
            raise ValueError
        instant = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if instant.tzinfo is None or stamp(instant) != value:
            raise ValueError
        return instant
    except (ValueError, OverflowError):
        raise AnalysisError("INVALID_DATA", "消费记录必须使用规范UTC毫秒时间。") from None


@dataclass(frozen=True)
class Dataset:
    dataset_id: str
    account_id: str
    version: str
    coverage_start: str
    coverage_end_exclusive: str
    source: str = SOURCE
    coverage_note: str = "仅覆盖固定模拟消费样本，不代表真实账户的全部消费，不参与余额核算。"


@dataclass(frozen=True)
class SpendingRecord:
    record_id: str
    account_id: str
    occurred_at: str
    amount_cents: int
    merchant_id: str
    merchant_name: str
    category: str
    payment_instrument_ref: str | None = None
    record_type: str = "purchase"
    status: str = "posted"
    currency: str = "CNY"
    category_source: str = "fixture_v1"


def validate_dataset(dataset):
    for value in (dataset.dataset_id, dataset.account_id, dataset.version):
        identifier(value)
    start, end = local_date(dataset.coverage_start), local_date(dataset.coverage_end_exclusive)
    if not 0 < (end - start).days <= 366 or dataset.source != SOURCE:
        raise AnalysisError("INVALID_DATA", "不支持的数据来源或覆盖范围。")
    if not isinstance(dataset.coverage_note, str) or not 1 <= len(dataset.coverage_note) <= 300:
        raise AnalysisError("INVALID_DATA", "必须声明数据覆盖说明。")
    return start, end


def validate_record(record, dataset):
    for value in (record.record_id, record.account_id, record.merchant_id):
        identifier(value)
    if record.account_id != dataset.account_id:
        raise AnalysisError("ACCOUNT_MISMATCH", "记录与数据集账户不一致。")
    if record.record_type != "purchase" or record.status != "posted" or record.currency != "CNY":
        raise AnalysisError("UNSUPPORTED_RECORD", "仅支持已完成的CNY消费；退款、撤销、失败和待处理记录不能导入。")
    if type(record.amount_cents) is not int or not 0 < record.amount_cents <= MAX_AMOUNT_CENTS:
        raise AnalysisError("INVALID_DATA", "消费金额必须为有效的正整数分。")
    if not isinstance(record.category, str) or record.category not in CATEGORIES or record.category_source != "fixture_v1":
        raise AnalysisError("INVALID_DATA", "分类或分类来源不受支持。")
    if not isinstance(record.merchant_name, str) or not 1 <= len(record.merchant_name.strip()) <= 100:
        raise AnalysisError("INVALID_DATA", "商户名称无效。")
    reference = record.payment_instrument_ref
    if reference is not None and (not isinstance(reference, str) or not re.fullmatch(r"demo-instrument-[a-z][a-z0-9_-]{0,47}", reference) or re.search(r"\d{12,}", reference)):
        raise AnalysisError("INVALID_DATA", "支付工具引用必须是非敏感内部标识。")
    day = utc_instant(record.occurred_at).astimezone(BUSINESS_TIMEZONE).date()
    if not local_date(dataset.coverage_start) <= day < local_date(dataset.coverage_end_exclusive):
        raise AnalysisError("INVALID_DATA", "记录超出数据集覆盖范围。")


SCHEMA = (
    """CREATE TABLE IF NOT EXISTS analysis_datasets (
        dataset_id TEXT PRIMARY KEY,
        account_id TEXT NOT NULL REFERENCES accounts(account_id),
        version TEXT NOT NULL,
        source TEXT NOT NULL CHECK(source='synthetic_demo'),
        coverage_start TEXT NOT NULL,
        coverage_end_exclusive TEXT NOT NULL,
        coverage_note TEXT NOT NULL,
        content_sha256 TEXT NOT NULL,
        UNIQUE(dataset_id,account_id)
    )""",
    """CREATE TABLE IF NOT EXISTS spending_records (
        dataset_id TEXT NOT NULL,
        record_id TEXT NOT NULL,
        account_id TEXT NOT NULL,
        occurred_at TEXT NOT NULL,
        amount_cents INTEGER NOT NULL CHECK(typeof(amount_cents)='integer' AND amount_cents>0 AND amount_cents<=9000000000000),
        merchant_id TEXT NOT NULL,
        merchant_name TEXT NOT NULL,
        category TEXT NOT NULL CHECK(category IN ('dining','transport','shopping','housing','other','uncategorized')),
        payment_instrument_ref TEXT,
        record_type TEXT NOT NULL CHECK(record_type='purchase'),
        status TEXT NOT NULL CHECK(status='posted'),
        currency TEXT NOT NULL CHECK(currency='CNY'),
        category_source TEXT NOT NULL CHECK(category_source='fixture_v1'),
        PRIMARY KEY(dataset_id,record_id),
        FOREIGN KEY(dataset_id,account_id) REFERENCES analysis_datasets(dataset_id,account_id)
    )""",
    "CREATE INDEX IF NOT EXISTS idx_spending_account_time ON spending_records(account_id,dataset_id,occurred_at,record_id)",
)


def fingerprint(dataset, records):
    value = {"dataset": asdict(dataset), "records": sorted((asdict(r) for r in records), key=lambda r: r["record_id"])}
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")).hexdigest()


def import_dataset(conn, dataset, records):
    """显式离线写入；同版本同内容幂等，不覆盖已有记录，不写原有三张表。

    调用者必须提供独立目标库连接；生产服务不得调用本函数。
    所有校验在写入前完成，表创建及数据导入在同一个事务内。
    """
    records = list(records)
    validate_dataset(dataset)
    seen, merchants = set(), {}
    for record in records:
        validate_record(record, dataset)
        if record.record_id in seen:
            raise AnalysisError("DUPLICATE_RECORD", "数据集中存在重复记录ID。")
        seen.add(record.record_id)
        previous = merchants.setdefault(record.merchant_id, record.merchant_name)
        if previous != record.merchant_name:
            raise AnalysisError("INVALID_DATA", "同一商户标识对应了不同名称。")
    digest = fingerprint(dataset, records)
    if conn.in_transaction:
        raise AnalysisError("IMPORT_TRANSACTION", "导入必须使用独立且空闲的数据库连接。")
    try:
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("BEGIN IMMEDIATE")
        for sql in SCHEMA:
            conn.execute(sql)
        existing = conn.execute("SELECT content_sha256 FROM analysis_datasets WHERE dataset_id=?", (dataset.dataset_id,)).fetchone()
        if existing:
            names = tuple(SpendingRecord.__dataclass_fields__)
            rows = conn.execute("SELECT " + ",".join(names) + " FROM spending_records WHERE dataset_id=?", (dataset.dataset_id,)).fetchall()
            actual = [SpendingRecord(**dict(zip(names, row))) for row in rows]
            meta = conn.execute("SELECT " + ",".join(Dataset.__dataclass_fields__) + " FROM analysis_datasets WHERE dataset_id=?", (dataset.dataset_id,)).fetchone()
            stored = Dataset(**dict(zip(Dataset.__dataclass_fields__, meta)))
            if existing[0] != digest or fingerprint(stored, actual) != digest:
                raise AnalysisError("DATASET_CONFLICT", "已有数据集内容不一致，拒绝覆盖；请核对或使用新数据集ID。")
            inserted = 0
        else:
            conn.execute("INSERT INTO analysis_datasets(dataset_id,account_id,version,coverage_start,coverage_end_exclusive,source,coverage_note,content_sha256) VALUES (?,?,?,?,?,?,?,?)", (*asdict(dataset).values(), digest))
            columns = ("dataset_id", *SpendingRecord.__dataclass_fields__)
            conn.executemany("INSERT INTO spending_records(" + ",".join(columns) + ") VALUES (" + ",".join("?" for _ in columns) + ")", [(dataset.dataset_id, *asdict(r).values()) for r in records])
            inserted = len(records)
        conn.commit()
        return {"dataset_id": dataset.dataset_id, "record_count": len(records), "inserted": inserted, "content_sha256": digest}
    except AnalysisError:
        conn.rollback()
        raise
    except sqlite3.Error:
        conn.rollback()
        raise AnalysisError("IMPORT_FAILED", "数据集导入失败，未提交。") from None
