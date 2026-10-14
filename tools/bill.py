"""独立账单查询工具。"""

import sqlite3

from database.db import connection, format_money


def get_bills(account_id: str, limit: int = 10) -> dict:
    if not isinstance(account_id, str) or not account_id.strip():
        return {"ok": False, "code": "INVALID_ACCOUNT", "error": "账户编号不能为空。"}
    if type(limit) is not int or not 1 <= limit <= 100:
        return {"ok": False, "code": "INVALID_LIMIT", "error": "账单条数必须为 1 到 100 的整数。"}
    try:
        with connection() as conn:
            if conn.execute(
                "SELECT 1 FROM accounts WHERE account_id = ?", (account_id,)
            ).fetchone() is None:
                return {"ok": False, "code": "ACCOUNT_NOT_FOUND", "error": "账户不存在。"}
            rows = conn.execute(
                "SELECT * FROM bills WHERE account_id = ? ORDER BY id DESC LIMIT ?",
                (account_id, limit),
            ).fetchall()
            items = []
            for row in rows:
                item = dict(row)
                item["amount"] = format_money(row["amount_cents"])
                item["direction"] = "out" if row["kind"] == "transfer_out" else "in"
                items.append(item)
            return {"ok": True, "account_id": account_id, "currency": "CNY", "bills": items}
    except (sqlite3.Error, OSError):
        return {"ok": False, "code": "DATABASE_ERROR", "error": "数据库暂时不可用。"}
