"""独立模拟转账工具：一个事务完成余额更新及双方记账。"""

import re
import sqlite3
import uuid

from database.db import MAX_BALANCE_CENTS, connection, format_money
from security.audit import AuditError
from security.daily_limit import transfer_assessment, unavailable_decision
from security.transfer_grant import claim_transfer


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
    grant = claim_transfer(from_account_id, to_account_id, amount)
    if grant is None:
        return {"ok": False, "code": "SECURE_ENTRYPOINT_REQUIRED", "error": "转账必须通过服务端待确认任务的正式确认；没有有效的一次性许可。"}
    writing = False
    try:
        with connection() as conn:
            # 取得写锁后才取业务时间、统计累计、复核风险；与资金写入使用同一连接。
            conn.execute("BEGIN IMMEDIATE")
            decision, day = transfer_assessment(conn, from_account_id, amount)
            grant.context.update(risk_level=decision["risk_level"], permission_action=decision["permission_action"],
                                 confirmation_status="verification_required" if decision["require_verification"] else "confirmed")
            if grant.logger:
                grant.logger.log_event("risk_evaluated", grant.context, db_connection=conn,
                                       details={"reason": decision["risk_reason"], "stage": "confirmation", "daily_limit": day})
                grant.logger.log_event("permission_decided", grant.context, db_connection=conn,
                                       details={**decision, "stage": "confirmation", "daily_limit": day})
            if decision["permission_action"] != "pending_confirmation":
                result = {"ok": False, "code": "VERIFICATION_REQUIRED", "error": decision["risk_reason"] + "。尚未执行。"}
            else:
                writing = True
                result = _write_transfer(conn, from_account_id, to_account_id, cents, day["evaluated_at"])
            result.update(security_decision=decision, daily_limit=day)
        # 仅在连接上下文成功提交之后报告成功。
        return result
    except (sqlite3.Error, OSError, ValueError, ArithmeticError, TypeError, OverflowError, AuditError) as exc:
        # 写入/提交/关闭期间异常不能宣称已回滚，可能已经产生副作用；任务与许可均已消费。
        reason = ("资金事务异常，执行结果需核对账单；请勿自动重试。" if writing else
                  "确认阶段审计不可用，已安全阻断，尚未执行。" if isinstance(exc, AuditError) else
                  "确认阶段数据库或日累计风险复核失败，已安全阻断，尚未执行。")
        decision = unavailable_decision(reason)
        grant.context.update(risk_level="red", permission_action="require_verification", confirmation_status="verification_required")
        return {"ok": False, "code": "AUDIT_UNAVAILABLE" if isinstance(exc, AuditError) else "DATABASE_ERROR", "error": decision["risk_reason"],
                "execution_unknown": writing, "security_decision": decision}


def _write_transfer(conn, sender_id, recipient_id, cents, booked_at):
    """仅由上方事务内风险与确认复核成功后调用，不另开连接或提交。"""
    sender = conn.execute("SELECT * FROM accounts WHERE account_id=?", (sender_id,)).fetchone()
    recipient = conn.execute("SELECT * FROM accounts WHERE account_id=?", (recipient_id,)).fetchone()
    if sender is None or recipient is None:
        return {"ok": False, "code": "ACCOUNT_NOT_FOUND", "error": "转出或收款账户不存在。"}
    if sender["balance_cents"] < cents:
        return {"ok": False, "code": "INSUFFICIENT_FUNDS", "error": "余额不足，转账未执行。"}
    if recipient["balance_cents"] + cents > MAX_BALANCE_CENTS:
        return {"ok": False, "code": "BALANCE_LIMIT", "error": "收款账户超出演示余额上限。"}
    transfer_id = uuid.uuid4().hex
    conn.execute("UPDATE accounts SET balance_cents=balance_cents-? WHERE account_id=?", (cents, sender_id))
    conn.execute("UPDATE accounts SET balance_cents=balance_cents+? WHERE account_id=?", (cents, recipient_id))
    conn.executemany(
        "INSERT INTO bills (account_id,kind,amount_cents,counterparty_id,transfer_id,created_at) VALUES (?,?,?,?,?,?)",
        [(sender_id, "transfer_out", cents, recipient_id, transfer_id, booked_at),
         (recipient_id, "transfer_in", cents, sender_id, transfer_id, booked_at)],
    )
    return {"ok": True, "simulated": True, "transfer_id": transfer_id, "from_account_id": sender_id,
            "to_account_id": recipient_id, "amount": format_money(cents), "amount_cents": cents,
            "balance": format_money(sender["balance_cents"] - cents), "balance_cents": sender["balance_cents"] - cents,
            "currency": "CNY"}
