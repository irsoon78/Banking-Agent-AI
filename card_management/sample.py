"""显式离线卡片样本初始化；复用既有消费样本，不在运行入口自动调用。"""

import argparse
import json
from pathlib import Path
import sqlite3

from bill_analysis.sample import initialize_demo as initialize_spending_demo
from bill_analysis.storage import AnalysisError
from card_management.storage import Card, CardError, InstrumentLink, import_card_samples

CARD_A = "card-7a4e2d91"
CARD_SPARE = "card-8c6f104b"
CARD_LOST = "card-3e902ad6"
CARD_B = "card-5b71c8f0"
SEED_TIME = "2026-08-31T16:00:00.000Z"


def card_samples():
    cards = [
        Card(CARD_A, "1001", "模拟生活卡A", "0101", False, "normal", 0, SEED_TIME, SEED_TIME),
        Card(CARD_SPARE, "1001", "模拟备用卡", "0202", True, "normal", 0, SEED_TIME, SEED_TIME),
        # 挂失是离线样本初始状态，不是通过API执行或强验证成功产生。
        Card(CARD_LOST, "1001", "模拟已挂失卡", "0303", True, "reported", 0, SEED_TIME, SEED_TIME),
        Card(CARD_B, "1002", "模拟生活卡B", "0404", False, "normal", 0, SEED_TIME, SEED_TIME),
    ]
    links = [InstrumentLink("1001", "demo-instrument-wallet-a", CARD_A), InstrumentLink("1002", "demo-instrument-wallet-b", CARD_B)]
    return cards, links


def initialize_card_demo(path):
    """先验证/初始化明确独立的分析演示库，再事务增补卡片；不重置原状态。"""
    try:
        baseline = initialize_spending_demo(path)
    except AnalysisError as exc:
        raise CardError(exc.code, str(exc)) from None
    conn = sqlite3.connect(Path(path).resolve())
    try:
        cards, links = card_samples()
        imported = import_card_samples(conn, cards, links)
    finally:
        conn.close()
    return {"database": baseline["database"], **imported, "card_count": len(cards), "link_count": len(links),
            "note": "独立模拟卡片；不参与余额或消费重算。临时操作须原会话确认，正式挂失/解挂不开放执行。"}


def main():
    parser = argparse.ArgumentParser(description="显式初始化独立卡片/消费联合演示库，禁止使用正式数据库。")
    parser.add_argument("--database", required=True)
    args = parser.parse_args()
    try:
        print(json.dumps(initialize_card_demo(args.database), ensure_ascii=False, indent=2))
    except (CardError, OSError, sqlite3.Error) as exc:
        message = str(exc) if isinstance(exc, CardError) else "无法初始化独立演示库；未自动重试。"
        parser.exit(1, f"初始化失败：{message}\n")


if __name__ == "__main__":
    main()
