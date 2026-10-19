"""有界、按账户隔离的内存会话。历史不是账本，不保存金融结果或授权状态。"""

from contextlib import contextmanager
from copy import deepcopy
from dataclasses import dataclass, field
from threading import Lock
from time import monotonic
from uuid import uuid4


class SessionError(Exception):
    def __init__(self, status_code: int, code: str, message: str):
        super().__init__(message)
        self.status_code, self.code = status_code, code


@dataclass
class Conversation:
    conversation_id: str
    account_id: str
    touched_at: float
    history: list[dict] = field(default_factory=list)
    pending_action_id: str | None = None
    busy: bool = False


class SessionStore:
    def __init__(self, ttl_seconds=1200, max_sessions=128, max_turns=6, clock=monotonic):
        self.ttl_seconds, self.max_sessions, self.max_turns = ttl_seconds, max_sessions, max_turns
        self._clock, self._lock, self._sessions = clock, Lock(), {}

    @contextmanager
    def checkout(self, account_id: str, conversation_id: str | None = None):
        with self._lock:
            now = self._clock()
            for key, value in list(self._sessions.items()):
                if not value.busy and now - value.touched_at >= self.ttl_seconds:
                    del self._sessions[key]
            if conversation_id:
                record = self._sessions.get(conversation_id)
                if record is None or record.account_id != account_id:
                    raise SessionError(404, "SESSION_NOT_FOUND", "会话不存在、已过期或不属于当前账户。请开始新对话；已有任务仍需单独确认或取消。")
            else:
                if len(self._sessions) >= self.max_sessions:
                    raise SessionError(429, "SESSION_LIMIT", "会话数量已达上限，请稍后再试。")
                record = Conversation(uuid4().hex, account_id, now)
                self._sessions[record.conversation_id] = record
            if record.busy:
                raise SessionError(409, "SESSION_BUSY", "此会话正在处理另一条消息，请等待结果，不要重复发送。")
            record.busy = True
            record.touched_at = now
        try:
            yield record, deepcopy(record.history)
        finally:
            with self._lock:
                record.busy = False
                record.touched_at = self._clock()

    def remember(self, record: Conversation, message: str, response: dict):
        # 不保存工具消息、模型工具调用、余额、账单或交易凭据；下一轮必须重新查工具。
        if response.get("response_kind") in {"conversation", "capabilities", "clarification", "unsupported"}:
            summary = response["reply"][:1200]
        else:
            summary = f"上一条银行请求的状态为 {response['status']}。金融数据须重新查询；历史状态不代表当前结果或授权。"
        with self._lock:
            record.history.extend([{"role": "user", "content": message}, {"role": "assistant", "content": summary}])
            record.history[:] = record.history[-self.max_turns * 2:]
