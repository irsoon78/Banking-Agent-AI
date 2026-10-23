"""有界调查快照；恢复仅查询，不把快照重新加载为可执行计划。"""

from contextlib import contextmanager
from copy import deepcopy
import json
from pathlib import Path
import re
import sqlite3
from uuid import uuid4

from database.db import database_path
from investigation.contracts import InvestigationError

PROCESS_OWNER = uuid4().hex
MAX_SNAPSHOT_BYTES = 262144
SCHEMA = """
CREATE TABLE IF NOT EXISTS investigation_reports (
    investigation_id TEXT PRIMARY KEY,
    account_id TEXT NOT NULL REFERENCES accounts(account_id),
    conversation_id TEXT,
    owner_id TEXT NOT NULL,
    execution_status TEXT NOT NULL,
    coverage_status TEXT NOT NULL,
    planner_mode TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    finished_at TEXT,
    revision INTEGER NOT NULL CHECK(revision > 0),
    snapshot_json TEXT NOT NULL CHECK(length(CAST(snapshot_json AS BLOB)) <= 262144)
)
"""
ROOT_FIELDS = frozenset({
    "schema_version", "orchestration", "investigation_id", "account_id", "focus", "status",
    "started_at", "finished_at", "period", "dataset", "coverage", "analysis_summary",
    "discovered_record_ids", "unselected_finding_count", "findings", "errors", "limits",
    "audit", "disclaimer", "safety", "steps", "evidence", "budget", "interrupted",
    "execution_status", "coverage_status", "planner_mode", "planning", "persistence_status",
    "privacy_redacted",
})
FORBIDDEN = frozenset({"api_key", "apikey", "authorization", "password", "pin", "cvv", "pan",
                       "card_number", "permit", "raw_response", "messages", "model_context"})


def bounded_snapshot(report):
    """只接受核心结构。限制总大小、集合大小；不持久化模型原文/认证字段。"""
    value = deepcopy(report)
    if not isinstance(value, dict) or set(value) - ROOT_FIELDS:
        raise InvestigationError("SNAPSHOT_INVALID", "调查快照字段无效。")
    if (len(value.get("steps", [])) > 32 or len(value.get("findings", [])) > 64
            or len(value.get("discovered_record_ids", [])) > 128
            or len(value.get("planning", {}).get("events", [])) > 20
            or any(len(value.get("evidence", {}).get(k, [])) > 6 for k in ("records", "links", "cards"))):
        raise InvestigationError("SNAPSHOT_LIMIT_EXCEEDED", "调查快照超出限制，未保存。")

    def inspect(item):
        if isinstance(item, dict):
            if any(str(k).lower() in FORBIDDEN for k in item):
                raise InvestigationError("SNAPSHOT_SENSITIVE_FIELD", "调查快照包含不允许的字段。")
            for child in item.values():
                inspect(child)
        elif isinstance(item, list):
            for child in item:
                inspect(child)
    inspect(value)
    encoded = json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    if len(encoded.encode("utf-8")) > MAX_SNAPSHOT_BYTES:
        raise InvestigationError("SNAPSHOT_LIMIT_EXCEEDED", "调查快照超出限制，未保存。")
    return value, encoded


class InvestigationStore:
    def __init__(self, path=None, *, owner_id=PROCESS_OWNER):
        self.path = Path(path or database_path()).resolve()
        self.owner_id = owner_id

    @contextmanager
    def _connection(self, *, write=False):
        # 不创建数据库，不执行迁移；schema由现有init_db统一建立。
        conn = sqlite3.connect(self.path.as_uri() + ("?mode=rw" if write else "?mode=ro"), uri=True, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        if not write:
            conn.execute("PRAGMA query_only = ON")
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def save(self, report, *, conversation_id=None):
        value, encoded = bounded_snapshot(report)
        investigation_id, account = value["investigation_id"], value["account_id"]
        with self._connection(write=True) as conn:
            conn.execute("BEGIN IMMEDIATE")
            previous = conn.execute("SELECT * FROM investigation_reports WHERE investigation_id=?", (investigation_id,)).fetchone()
            if previous and (previous["account_id"] != account or previous["owner_id"] != self.owner_id
                             or previous["conversation_id"] != conversation_id):
                raise InvestigationError("SNAPSHOT_OWNER_MISMATCH", "调查快照归属不一致。")
            if previous and previous["execution_status"] != "running":
                if previous["snapshot_json"] == encoded:
                    return value
                raise InvestigationError("SNAPSHOT_FINALIZED", "已完成快照不能被覆盖。")
            from card_management.storage import now_stamp
            conn.execute("""INSERT INTO investigation_reports VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(investigation_id) DO UPDATE SET
                execution_status=excluded.execution_status, coverage_status=excluded.coverage_status,
                planner_mode=excluded.planner_mode, updated_at=excluded.updated_at,
                finished_at=excluded.finished_at, revision=investigation_reports.revision+1,
                snapshot_json=excluded.snapshot_json""",
                (investigation_id, account, conversation_id, self.owner_id, value["execution_status"],
                 value["coverage_status"], value["planner_mode"], value["started_at"], now_stamp(),
                 value["finished_at"], 1, encoded))
        return value

    def _decode(self, row):
        value, _ = bounded_snapshot(json.loads(row["snapshot_json"]))
        if (value["account_id"] != row["account_id"] or value["investigation_id"] != row["investigation_id"]
                or value["execution_status"] != row["execution_status"]):
            raise InvestigationError("SNAPSHOT_INVALID", "调查快照与归属元数据不一致。")
        if row["execution_status"] == "running" and row["owner_id"] != self.owner_id:
            value["execution_status"] = "interrupted"
            value["status"] = "partial" if value.get("dataset") else "failed"
            value["interrupted"] = True
            value["errors"].append({"step_id": None, "code": "INVESTIGATION_INTERRUPTED", "message": "原服务进程已结束；仅恢复已保存报告，未自动续跑。"})
            for step in value["steps"]:
                if step["status"] in {"running", "planned"}:
                    step["status"] = "not_executed"
                    step["code"] = "INVESTIGATION_INTERRUPTED"
        value["recovery"] = {"source": "stored_snapshot", "revision": row["revision"], "updated_at": row["updated_at"], "rerun": False}
        return value

    def get(self, account_id, investigation_id):
        if not isinstance(investigation_id, str) or not re.fullmatch(r"[a-f0-9]{32}", investigation_id):
            raise InvestigationError("INVESTIGATION_NOT_FOUND", "未找到当前账户的调查。")
        with self._connection() as conn:
            row = conn.execute("SELECT * FROM investigation_reports WHERE account_id=? AND investigation_id=?", (account_id, investigation_id)).fetchone()
        if row is None:
            raise InvestigationError("INVESTIGATION_NOT_FOUND", "未找到当前账户的调查。")
        return self._decode(row)

    def list(self, account_id, *, limit=20, offset=0):
        if type(limit) is not int or type(offset) is not int or not 1 <= limit <= 20 or not 0 <= offset <= 1000:
            raise InvestigationError("INVALID_ARGUMENTS", "分页参数无效。")
        with self._connection() as conn:
            rows = conn.execute("SELECT * FROM investigation_reports WHERE account_id=? ORDER BY created_at DESC, investigation_id DESC LIMIT ? OFFSET ?", (account_id, limit, offset)).fetchall()
            count = conn.execute("SELECT COUNT(*) FROM investigation_reports WHERE account_id=?", (account_id,)).fetchone()[0]
        reports = [self._decode(row) for row in rows]
        keys = ("investigation_id", "period", "focus", "dataset", "execution_status", "coverage_status", "planner_mode", "started_at", "finished_at", "recovery")
        return {"items": [{key: report.get(key) for key in keys} for report in reports], "total": count, "limit": limit, "offset": offset}
