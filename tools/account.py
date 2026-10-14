"""独立余额查询工具。"""

import sqlite3

from database.db import connection, format_money


def get_balance(account_id: str) -> dict:
    if not isinstance(account_id, str) or not account_id.strip():
        return {"ok": False, "code": "INVALID_ACCOUNT", "error": "账户编号不能为空。"}
    try:
        with connection() as conn:
            row = conn.execute(
                "SELECT account_id, owner, balance_cents FROM accounts WHERE account_id = ?",
                (account_id,),
            ).fetchone()
            if row is None:
                return {"ok": False, "code": "ACCOUNT_NOT_FOUND", "error": "账户不存在。"}
            return {
                "ok": True,
                "account_id": row["account_id"],
                "owner": row["owner"],
                "balance": format_money(row["balance_cents"]),
                "balance_cents": row["balance_cents"],
                "currency": "CNY",
            }
    except (sqlite3.Error, OSError):
        return {"ok": False, "code": "DATABASE_ERROR", "error": "数据库暂时不可用。"}
