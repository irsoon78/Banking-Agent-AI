"""数据库连接、初始化和金额显示。金额统一以整数分存储。"""

import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent
MAX_BALANCE_CENTS = 9_000_000_000_000


def database_path() -> Path:
    path = Path(os.getenv("BANK_DB_PATH", "database/banking.db"))
    return path if path.is_absolute() else PROJECT_DIR / path


@contextmanager
def connection():
    """正常退出提交，异常退出回滚；最后始终关闭连接。"""
    path = database_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        with conn:
            yield conn
    finally:
        conn.close()


def format_money(cents: int) -> str:
    sign = "-" if cents < 0 else ""
    value = abs(cents)
    return f"{sign}{value // 100}.{value % 100:02d}"


def init_db() -> None:
    """首次创建演示数据，后续启动不会重置余额或重复生成账单。"""
    with connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(f"""
            CREATE TABLE IF NOT EXISTS accounts (
                account_id TEXT PRIMARY KEY,
                owner TEXT NOT NULL,
                balance_cents INTEGER NOT NULL
                    CHECK (balance_cents BETWEEN 0 AND {MAX_BALANCE_CENTS})
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS bills (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                account_id TEXT NOT NULL REFERENCES accounts(account_id),
                kind TEXT NOT NULL
                    CHECK (kind IN ('opening', 'transfer_out', 'transfer_in')),
                amount_cents INTEGER NOT NULL CHECK (amount_cents > 0),
                counterparty_id TEXT REFERENCES accounts(account_id),
                transfer_id TEXT,
                created_at TEXT NOT NULL DEFAULT
                    (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_bills_account_id
            ON bills(account_id, id DESC)
        """)
        for account_id, owner, cents in (
            ("1001", "张三", 1_000_000),
            ("1002", "李四", 500_000),
        ):
            inserted = conn.execute(
                "INSERT OR IGNORE INTO accounts VALUES (?, ?, ?)",
                (account_id, owner, cents),
            ).rowcount
            if inserted:
                conn.execute(
                    "INSERT INTO bills (account_id, kind, amount_cents) "
                    "VALUES (?, 'opening', ?)",
                    (account_id, cents),
                )


if __name__ == "__main__":
    init_db()
    print(f"数据库已初始化：{database_path()}")
