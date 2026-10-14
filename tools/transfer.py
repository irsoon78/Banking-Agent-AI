"""独立模拟转账工具：一个事务完成余额更新及双方记账。"""

import re
import sqlite3
import uuid

from database.db import MAX_BALANCE_CENTS, connection, format_money


def transfer_money(from_account_id: str, to_account_id: str, amount: str) -> dict:
    # 使用十进制字符串及整数运算，拒绝浮点数、指数形式和超过两位的小数。
    if not isinstance(amount, str) or not re.fullmatch(r"[0-9]{1,10}(\.[0-9]{1,2})?", amount):
        return {"ok": False, "code": "INVALID_AMOUNT", "error": "金额须为最多两位小数的正数文本。"}
    whole, _, fraction = amount.partition(".")
    cents = int(whole) * 100 + int(fraction.ljust(2, "0") or "0")
    if cents <= 0:
        return {"ok": False, "code": "INVALID_AMOUNT", "error": "转账金额必须大于零。"}
    if any(not isinstance(value, str) or not value.strip() for value in (from_account_id, to_account_id)):
        return {"ok": False, "code": "INVALID_ACCOUNT", "error": "转出和收款账户编号不能为空。"}
    if from_account_id == to_account_id:
        return {"ok": False, "code": "SAME_ACCOUNT", "error": "不能向同一账户转账。"}
    try:
        with connection() as conn:
            # 先取得写锁再读取余额，避免并发请求基于同一旧余额扣款。
            conn.execute("BEGIN IMMEDIATE")
            sender = conn.execute(
                "SELECT * FROM accounts WHERE account_id = ?", (from_account_id,)
            ).fetchone()
            recipient = conn.execute(
                "SELECT * FROM accounts WHERE account_id = ?", (to_account_id,)
            ).fetchone()
            if sender is None or recipient is None:
                return {"ok": False, "code": "ACCOUNT_NOT_FOUND", "error": "转出或收款账户不存在。"}
            if sender["balance_cents"] < cents:
                return {"ok": False, "code": "INSUFFICIENT_FUNDS", "error": "余额不足，转账未执行。"}
            if recipient["balance_cents"] + cents > MAX_BALANCE_CENTS:
                return {"ok": False, "code": "BALANCE_LIMIT", "error": "收款账户超出演示余额上限。"}
            transfer_id = uuid.uuid4().hex
            conn.execute(
                "UPDATE accounts SET balance_cents = balance_cents - ? WHERE account_id = ?",
                (cents, from_account_id),
            )
            conn.execute(
                "UPDATE accounts SET balance_cents = balance_cents + ? WHERE account_id = ?",
                (cents, to_account_id),
            )
            conn.executemany(
                "INSERT INTO bills (account_id, kind, amount_cents, counterparty_id, transfer_id) "
                "VALUES (?, ?, ?, ?, ?)",
                [
                    (from_account_id, "transfer_out", cents, to_account_id, transfer_id),
                    (to_account_id, "transfer_in", cents, from_account_id, transfer_id),
                ],
            )
            result = {
                "ok": True,
                "simulated": True,
                "transfer_id": transfer_id,
                "from_account_id": from_account_id,
                "to_account_id": to_account_id,
                "amount": format_money(cents),
                "amount_cents": cents,
                "balance": format_money(sender["balance_cents"] - cents),
                "balance_cents": sender["balance_cents"] - cents,
                "currency": "CNY",
            }
        # 仅在连接上下文成功提交之后报告成功。
        return result
    except (sqlite3.Error, OSError):
        return {"ok": False, "code": "DATABASE_ERROR", "error": "转账未完成，数据库操作已回滚。"}
