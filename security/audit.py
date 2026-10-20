"""SQLite 持久化审计；事件内容由服务端代码生成，不调用 LLM。"""

import json
import logging
import sqlite3
from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
from uuid import uuid4

from database.db import connection

_request_context: ContextVar[dict | None] = ContextVar("bank_audit_context", default=None)
AUDIT_WARNING = "操作结果已产生，但部分审计写入失败；请核对结果，不要重复转账。"


class AuditError(Exception):
    """只包含可对外显示的固定错误，不暴露数据库路径或底层异常。"""


class AuditLogger:
    def context(self, account_id: str, operation: str | None = None) -> dict:
        active = _request_context.get()
        base = deepcopy(active) if active and active["account_id"] == account_id else {
            "request_id": uuid4().hex, "account_id": account_id, "user_request": "",
        }
        return {
            **base, "operation": operation,
            "risk_level": None, "permission_action": None,
            "confirmation_status": "not_required", "execution_result": "not_executed",
        }

    @contextmanager
    def request_context(self, account_id: str, user_request: str, operation: str | None = None):
        base = {"request_id": uuid4().hex, "account_id": account_id, "user_request": user_request}
        token = _request_context.set(base)
        try:
            yield self.context(account_id, operation)
        finally:
            _request_context.reset(token)

    def log_event(self, event_type: str, context: dict, *, details: dict | None = None,
                  db_connection: sqlite3.Connection | None = None, **changes) -> int:
        record = {**context, **changes}
        record.setdefault("request_id", uuid4().hex)
        if not isinstance(event_type, str) or not event_type.strip():
            raise ValueError("event_type 不能为空")
        if not isinstance(record.get("account_id"), str) or not record["account_id"].strip():
            raise ValueError("account_id 不能为空")
        if not isinstance(record.get("user_request", ""), str):
            raise ValueError("user_request 必须为字符串")
        encoded = json.dumps(details or {}, ensure_ascii=False, default=str, allow_nan=False)

        def insert(conn):
            cursor = conn.execute(
                "INSERT INTO audit_logs (request_id, action_id, event_type, account_id, "
                "user_request, operation, risk_level, permission_action, confirmation_status, "
                "execution_result, details) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (record["request_id"], record.get("action_id"), event_type, record["account_id"],
                 record.get("user_request", ""), record.get("operation"), record.get("risk_level"),
                 record.get("permission_action"), record.get("confirmation_status", "not_required"),
                 record.get("execution_result", "not_executed"), encoded),
            )
            return cursor.lastrowid

        try:
            if db_connection is not None:
                return insert(db_connection)  # 使用调用者事务，不能另开连接争抢写锁。
            with connection() as conn:
                event_id = insert(conn)
            return event_id
        except (sqlite3.Error, OSError):
            raise AuditError("审计数据库暂时不可用。") from None

    def log_after_execution(self, event_type: str, context: dict, **kwargs) -> bool:
        """执行后写入失败不能覆盖真实结果或触发重试。"""
        try:
            self.log_event(event_type, context, **kwargs)
            return True
        except (AuditError, TypeError, ValueError):
            logging.getLogger(__name__).error("Audit write failed after an operation; do not retry the operation.")
            return False

    def query(self, account_id: str, limit: int = 100, offset: int = 0, request_id: str | None = None) -> list[dict]:
        if not isinstance(account_id, str) or not account_id.strip():
            raise ValueError("account_id 不能为空")
        if type(limit) is not int or not 1 <= limit <= 500 or type(offset) is not int or offset < 0:
            raise ValueError("limit 必须为 1–500，offset 必须为非负整数")
        if request_id is not None and (not isinstance(request_id, str) or not request_id.strip()):
            raise ValueError("request_id 必须为非空字符串")
        sql = "SELECT * FROM audit_logs WHERE account_id = ?"
        params: list = [account_id]
        if request_id is not None:
            sql += " AND request_id = ?"
            params.append(request_id)
        sql += " ORDER BY id DESC LIMIT ? OFFSET ?"
        params.extend((limit, offset))
        try:
            with connection() as conn:
                rows = conn.execute(sql, params).fetchall()
            result = []
            for row in rows:
                item = dict(row)
                item["details"] = json.loads(item["details"])
                result.append(item)
            return result
        except (sqlite3.Error, OSError, ValueError):
            raise AuditError("审计日志暂时无法读取。") from None
