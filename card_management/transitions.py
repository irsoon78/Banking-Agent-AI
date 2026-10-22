"""卡片专用许可保护的事务内核；生产调用由确认管理器领取任务后进入。

卡片状态、操作凭据与必要审计共用事务；不直接注册为Agent工具。
"""

from pathlib import Path
import sqlite3
from time import monotonic
from uuid import uuid4

from card_management.authorization import _claim
from card_management.storage import CardError, card_from_row, now_stamp, plan_transition, public_operation, validate_command
from security.audit import AuditError


def apply_temporary_action(db_path, command):
    """不是工具入口。无绑定的一次性许可时不打开数据库，不允许任意目标状态。"""
    validate_command(command)
    path = Path(db_path).resolve()
    permit = _claim(path, command)
    if permit is None:
        raise CardError("AUTHORIZATION_REQUIRED", "未获得绑定此次操作的服务端确认许可，卡片状态未修改。")
    conn = None
    commit_started = False
    try:
        # mode=rw禁止因路径错误创建新库；不创建目录或任何表。
        conn = sqlite3.connect(path.as_uri() + "?mode=rw", uri=True, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("BEGIN IMMEDIATE")
        # 等待写锁后再次检查时效，不能使用已过期许可。
        if monotonic() >= permit.expires_at:
            raise CardError("AUTHORIZATION_EXPIRED", "等待期间确认许可已过期，未执行。")
        existing = conn.execute("SELECT * FROM card_operations WHERE action_id=?", (command.action_id,)).fetchone()
        if existing:
            if (existing["account_id"], existing["card_id"], existing["operation"], existing["before_version"]) != (command.account_id, command.card_id, command.operation, command.expected_version):
                raise CardError("ACTION_ID_CONFLICT", "操作编号与原请求不一致，拒绝重复使用。")
            result = {**public_operation(existing), "replayed": True}
            conn.rollback()
            return result
        row = conn.execute("SELECT * FROM cards WHERE account_id=? AND card_id=?", (command.account_id, command.card_id)).fetchone()
        if row is None:
            raise CardError("CARD_NOT_FOUND", "当前账户不存在此卡片。")
        card = card_from_row(row)
        after = plan_transition(card, command)
        decision = None
        if permit.logger is not None:
            from security.risk_engine import RiskEngine
            from security.permission import PermissionController
            from card_management.tools import CARD_TEMP_OPERATIONS
            action = next(key for key, value in CARD_TEMP_OPERATIONS.items() if value == command.operation)
            risk = RiskEngine().evaluate(action)
            permission = PermissionController().decide(action, risk["risk_level"])
            decision = {"operation": action, "risk_level": risk["risk_level"], "risk_reason": risk["reason"],
                        "permission_action": permission["action"], "require_confirmation": permission["require_confirmation"],
                        "require_verification": permission["require_verification"]}
            context = {**permit.context, "operation": action, "risk_level": risk["risk_level"], "permission_action": permission["action"]}
            if permission["require_verification"]:
                context["confirmation_status"] = "verification_required"
            permit.logger.log_event("risk_evaluated", context, db_connection=conn, details={"stage": "card_confirmation", "reason": risk["reason"]})
            permit.logger.log_event("permission_decided", context, db_connection=conn, details={**permission, "stage": "card_confirmation"})
            if risk["risk_level"] != "yellow" or permission["action"] != "pending_confirmation":
                # 保留实际阻断决策；尚未变更任何卡片或凭据。
                commit_started = True
                conn.commit()
                blocked = CardError("VERIFICATION_REQUIRED", "最新卡片权限不允许执行，需要重新核对验证。")
                blocked.decision = decision
                raise blocked
        executed_at = now_stamp()
        if executed_at < card.updated_at:
            raise CardError("CLOCK_ERROR", "服务端时间早于卡片更新时间，已阻断。")
        changed = conn.execute(
            "UPDATE cards SET temporary_frozen=?,version=version+1,updated_at=? WHERE account_id=? AND card_id=? AND version=? AND loss_status=? AND temporary_frozen=?",
            (int(after["temporary_frozen"]), executed_at, command.account_id, command.card_id, command.expected_version, "normal", int(card.temporary_frozen)),
        ).rowcount
        if changed != 1:
            raise CardError("VERSION_CONFLICT", "卡片状态已变化，未执行覆盖。")
        conn.execute(
            "INSERT INTO card_operations(operation_id,action_id,account_id,card_id,operation,before_temporary_frozen,after_temporary_frozen,before_loss_status,after_loss_status,before_version,after_version,executed_at,result_status) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (uuid4().hex, command.action_id, command.account_id, command.card_id, command.operation, int(card.temporary_frozen), int(after["temporary_frozen"]), card.loss_status, after["loss_status"], card.version, after["version"], executed_at, "completed"),
        )
        result = {**public_operation(conn.execute("SELECT * FROM card_operations WHERE action_id=?", (command.action_id,)).fetchone()), "replayed": False}
        if permit.logger is not None:
            permit.logger.log_event("card_operation_committed", context, db_connection=conn, execution_result="success",
                                    details={"operation_id": result["operation_id"], "card_id": command.card_id,
                                             "before": result["before"], "after": result["after"]})
            result.update(request_id=context["request_id"], audit_id=context["request_id"], security_decision=decision)
        commit_started = True
        conn.commit()
        return result
    except CardError:
        if conn is not None:
            try:
                conn.rollback()
            except sqlite3.Error:
                raise CardError("EXECUTION_UNKNOWN", "回滚结果无法确认，请核对卡片和操作凭据，不要自动重试。") from None
        raise
    except (sqlite3.Error, OSError, ValueError, OverflowError, TypeError, AuditError):
        if conn is not None:
            try:
                conn.rollback()
            except sqlite3.Error:
                raise CardError("EXECUTION_UNKNOWN", "事务结果未知，请查询卡片状态和操作凭据，不要自动重试。") from None
        if commit_started:
            raise CardError("EXECUTION_UNKNOWN", "提交结果需核对，请查询卡片状态和操作凭据，不要自动重试。") from None
        raise CardError("CARD_TRANSACTION_FAILED", "卡片事务未提交，状态修改和凭据写入均未生效。") from None
    finally:
        if conn is not None:
            try:
                conn.close()
            except sqlite3.Error:
                raise CardError("EXECUTION_UNKNOWN", "连接结束异常，请核对卡片和操作凭据，不要自动重试。") from None
