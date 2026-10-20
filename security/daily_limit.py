"""账本日累计预评估/事务内复核，共用北京时间业务日和整数分统计。"""

from datetime import datetime, time, timedelta, timezone
from decimal import Decimal
import sqlite3

from database.db import connection, format_money
from security.permission import PermissionController
from security.risk_engine import RiskEngine

# 本 Demo 自行采用现代北京时间 UTC+08:00，名为 Asia/Shanghai；不依赖 Windows tzdata。
BUSINESS_TIMEZONE = timezone(timedelta(hours=8), "Asia/Shanghai")


def utc_now():
    return datetime.now(timezone.utc)


def business_day(now=None):
    instant = utc_now() if now is None else now
    if not isinstance(instant, datetime) or instant.tzinfo is None or instant.utcoffset() is None:
        raise ValueError("业务时钟必须提供带时区的时间")
    local = instant.astimezone(BUSINESS_TIMEZONE)
    start = datetime.combine(local.date(), time.min, BUSINESS_TIMEZONE)

    def stamp(value):
        return value.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")

    return {"business_date": local.date().isoformat(), "time_zone": "Asia/Shanghai",
            "start_utc": stamp(start), "end_utc": stamp(start + timedelta(days=1)),
            "evaluated_at": stamp(instant)}


def outgoing_cents(conn, account_id, day):
    # 数据异常不能被日期过滤静默漏掉、按零处理。
    invalid = conn.execute(
        "SELECT COUNT(*) FROM bills WHERE account_id=? AND kind='transfer_out' AND "
        "(typeof(amount_cents)!='integer' OR amount_cents<=0 OR transfer_id IS NULL OR "
        "created_at NOT GLOB '????-??-??T??:??:??.???Z' OR julianday(created_at) IS NULL)",
        (account_id,),
    ).fetchone()[0]
    if invalid:
        raise ValueError("转出账本数据无法可靠统计")
    value = conn.execute(
        "SELECT COALESCE(SUM(amount_cents),0) FROM bills "
        "WHERE account_id=? AND kind='transfer_out' AND created_at>=? AND created_at<?",
        (account_id, day["start_utc"], day["end_utc"]),
    ).fetchone()[0]
    if type(value) is not int or value < 0:
        raise ValueError("日累计金额无效")
    return value


def transfer_assessment(conn, account_id, amount):
    day = business_day()
    total = outgoing_cents(conn, account_id, day)
    risk = RiskEngine().evaluate("transfer_money", amount, daily_outgoing=Decimal(total) / 100)
    day["executed_cents"] = total
    value = Decimal(str(amount))
    if not value.is_finite() or value <= 0 or value * 100 != (value * 100).to_integral_value():
        raise ValueError("拟转账金额无效")
    day["projected_cents"] = total + int(value * 100)
    reason = (f"北京时间 {day['business_date']} 已成功转出 {format_money(total)} 元，"
              f"含本笔累计 {format_money(day['projected_cents'])} 元；{risk['reason']}")
    permission = PermissionController().decide("transfer_money", risk["risk_level"])
    decision = {"operation": "transfer_money", "risk_level": risk["risk_level"], "risk_reason": reason,
                "permission_action": permission["action"],
                "require_confirmation": permission["require_confirmation"],
                "require_verification": permission["require_verification"]}
    return decision, day


def unavailable_decision(reason=None):
    risk = RiskEngine().evaluate("transfer_money", None)
    permission = PermissionController().decide("transfer_money", risk["risk_level"])
    return {"operation": "transfer_money", "risk_level": risk["risk_level"],
            "risk_reason": reason or "无法可靠核实日累计金额或业务时间，已安全阻断，尚未执行。",
            "permission_action": permission["action"], "require_confirmation": permission["require_confirmation"],
            "require_verification": permission["require_verification"]}


def assess_operation(account_id, action, params):
    """创建任务的只读预评估；不能代替确认事务中的复核。"""
    if action not in RiskEngine.TRANSFER_OPERATIONS:
        return RiskEngine().evaluate(action, params.get("amount"))
    try:
        with connection() as conn:
            decision, day = transfer_assessment(conn, account_id, params.get("amount"))
        return {"risk_level": decision["risk_level"], "reason": decision["risk_reason"], "daily_limit": day}
    except (sqlite3.Error, OSError, ValueError, ArithmeticError, TypeError, OverflowError):
        decision = unavailable_decision()
        return {"risk_level": "red", "reason": decision["risk_reason"], "risk_error": "DAILY_RISK_UNAVAILABLE"}
