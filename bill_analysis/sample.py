"""固定消费样本。只能显式运行 python -m bill_analysis.sample --database 独立路径。"""

import argparse
from contextlib import closing
from datetime import datetime
import json
import os
from pathlib import Path
import sqlite3

from dotenv import dotenv_values

from database.db import PROJECT_DIR, database_path, init_db
from security.daily_limit import BUSINESS_TIMEZONE
from bill_analysis.storage import AnalysisError, Dataset, SpendingRecord, import_dataset, stamp

DEMO_APPLICATION_ID = 0x42414E41
DATASET_ID = "sample-spending-1001-v1"

# 手工定义的独立预期值，不从样本列表运行时推导，供回归核对。
EXPECTED = {
    "2026-09": {
        "total_cents": 214049, "count": 8,
        "categories": {"dining": 11700, "transport": 550, "shopping": 21799, "housing": 180000},
        "merchants": {"noodle": 6850, "cafe": 4850, "metro": 550, "market": 21799, "rent": 180000},
    },
    "2026-10": {
        "total_cents": 273701, "count": 10,
        "categories": {"dining": 27200, "transport": 700, "shopping": 65801, "housing": 180000},
        "merchants": {"noodle": 3800, "cafe": 23400, "metro": 700, "market": 15901, "rent": 180000, "electronics": 49900},
    },
}

MERCHANTS = {"noodle": "演示面馆", "cafe": "演示咖啡店", "metro": "演示轨道交通", "market": "演示超市", "rent": "演示公寓", "electronics": "演示数码店"}


def sample_datasets():
    """固定日期不随运行时间漂移；2026-10-21为不包含的覆盖结束日期。"""
    dataset = Dataset(DATASET_ID, "1001", "v1", "2026-09-01", "2026-10-21")
    entries = (
        ("sep01", "2026-09-01T12:00", 3250, "noodle", "dining"),
        ("sep02", "2026-09-02T08:00", 250, "metro", "transport"),
        ("sep05", "2026-09-05T17:00", 12999, "market", "shopping"),
        ("sep10", "2026-09-10T12:00", 4850, "cafe", "dining"),
        ("sep15", "2026-09-15T10:00", 180000, "rent", "housing"),
        ("sep20", "2026-09-20T08:00", 300, "metro", "transport"),
        ("sep25", "2026-09-25T17:00", 8800, "market", "shopping"),
        ("sep30", "2026-09-30T12:00", 3600, "noodle", "dining"),
        ("oct01", "2026-10-01T12:00", 3800, "noodle", "dining"),
        ("oct02", "2026-10-02T08:00", 300, "metro", "transport"),
        ("oct05", "2026-10-05T17:00", 15900, "market", "shopping"),
        ("oct10", "2026-10-10T12:00", 5800, "cafe", "dining"),
        ("oct15", "2026-10-15T10:00", 180000, "rent", "housing"),
        ("oct18", "2026-10-18T17:00", 49900, "electronics", "shopping"),
        ("oct20-a", "2026-10-20T12:00", 8800, "cafe", "dining"),
        ("oct20-b", "2026-10-20T12:02", 8800, "cafe", "dining"),
        ("oct20-c", "2026-10-20T23:59", 400, "metro", "transport"),
        ("oct20-d", "2026-10-20T00:00", 1, "market", "shopping"),
    )
    records = [SpendingRecord(key, "1001", stamp(datetime.fromisoformat(local).replace(tzinfo=BUSINESS_TIMEZONE)), cents, merchant, MERCHANTS[merchant], category, "demo-instrument-wallet-a") for key, local, cents, merchant, category in entries]
    second = Dataset("sample-spending-1002-v1", "1002", "v1", "2026-09-01", "2026-10-21")
    second_records = [
        SpendingRecord("sep10", "1002", "2026-09-10T04:00:00.000Z", 12345, "market", MERCHANTS["market"], "shopping", "demo-instrument-wallet-b"),
        SpendingRecord("oct11", "1002", "2026-10-11T04:00:00.000Z", 678, "cafe", MERCHANTS["cafe"], "dining", "demo-instrument-wallet-b"),
    ]
    return [(dataset, records), (second, second_records)]


def initialize_demo(path):
    """仅供离线CLI使用：新建独立库或重复导入带专属标记的样本库。

    现有普通数据库一律拒绝；不读取或复制正式数据库。
    使用原init_db创建独立账户账本，分析样本本身只写新增分析表。
    """
    target = Path(path).resolve()
    # 同时保护默认路径、进程配置和.env配置；不修改.env或永久环境变量。
    protected = {(PROJECT_DIR / "database" / "banking.db").resolve(), database_path().resolve()}
    configured = dotenv_values(PROJECT_DIR / ".env").get("BANK_DB_PATH")
    if configured:
        configured = Path(configured)
        protected.add((configured if configured.is_absolute() else PROJECT_DIR / configured).resolve())
    if target in protected:
        raise AnalysisError("UNSAFE_TARGET", "禁止向正式演示数据库导入样本。")
    if target.exists():
        try:
            with closing(sqlite3.connect(target.as_uri() + "?mode=ro", uri=True)) as check:
                if check.execute("PRAGMA application_id").fetchone()[0] != DEMO_APPLICATION_ID:
                    raise AnalysisError("UNSAFE_TARGET", "现有数据库不属于独立分析样本库，拒绝修改。")
        except sqlite3.Error:
            raise AnalysisError("UNSAFE_TARGET", "无法确认独立样本库，拒绝修改。") from None
    else:
        target.parent.mkdir(parents=True, exist_ok=True)
        # 排他创建，不能覆盖检查后由其他进程创建的数据库。
        with target.open("xb"):
            pass
        previous = os.environ.get("BANK_DB_PATH")
        try:
            os.environ["BANK_DB_PATH"] = str(target)
            init_db()
            with closing(sqlite3.connect(target)) as conn:
                conn.execute(f"PRAGMA application_id={DEMO_APPLICATION_ID}")
        finally:
            if previous is None:
                os.environ.pop("BANK_DB_PATH", None)
            else:
                os.environ["BANK_DB_PATH"] = previous
    conn = sqlite3.connect(target)
    try:
        results = [import_dataset(conn, dataset, records) for dataset, records in sample_datasets()]
    finally:
        conn.close()
    return {"database": str(target), "source": "synthetic_demo", "balance_effect": "none", "datasets": results}


def main():
    parser = argparse.ArgumentParser(description="显式初始化独立账单分析演示库；禁止使用正式数据库。")
    parser.add_argument("--database", required=True, help="新建独立数据库路径，或此前由本命令创建的样本库")
    args = parser.parse_args()
    try:
        print(json.dumps(initialize_demo(args.database), ensure_ascii=False, indent=2))
    except (AnalysisError, OSError) as exc:
        parser.exit(1, f"初始化失败：{exc}\n")


if __name__ == "__main__":
    main()
