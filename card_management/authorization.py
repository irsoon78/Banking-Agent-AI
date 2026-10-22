"""默认无许可：仅由服务端确认管理器领取原会话任务后发放。

许可绑定路径、账户、卡片、操作、版本和action_id，不是HTTP或模型参数。
这不是对任意持有Python执行权/数据库写权限的本机代码的沙箱。
"""

from contextvars import ContextVar
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
import math
from threading import Lock
from time import monotonic

from card_management.storage import CardCommand


@dataclass
class _CardPermit:
    database: str
    command: CardCommand
    expires_at: float
    used: bool = False
    lock: Lock = field(default_factory=Lock)
    logger: object = None
    context: dict = field(default_factory=dict)


_active: ContextVar[_CardPermit | None] = ContextVar("card_confirmed_permit", default=None)
_request: ContextVar[str | None] = ContextVar("card_request_conversation", default=None)


@contextmanager
def card_request_scope(conversation_id):
    """只由服务端会话checkout设置；不是模型参数或授权。"""
    token = _request.set(conversation_id)
    try:
        yield
    finally:
        _request.reset(token)


def current_conversation():
    return _request.get()


@contextmanager
def confirmed_card_scope(account_id, action, params, logger, context):
    """仅供确认管理器在原子领取任务后使用；不对API/模型暴露。"""
    from card_management.tools import CARD_TEMP_OPERATIONS
    from card_management.storage import CardError, validate_command
    from database.db import database_path
    if logger is None or context.get("confirmation_status") != "confirmed" or not context.get("action_id"):
        raise CardError("AUTHORIZATION_REQUIRED", "卡片操作缺少可信确认及审计上下文。")
    command = CardCommand(account_id, params["card_id"], CARD_TEMP_OPERATIONS[action], params["expected_version"], context["action_id"])
    validate_command(command)
    permit = _CardPermit(str(database_path().resolve()), command, monotonic() + 30, logger=logger, context=deepcopy(context))
    token = _active.set(permit)
    try:
        yield
    finally:
        _active.reset(token)


def _claim(database, command):
    permit = _active.get()
    if not isinstance(permit, _CardPermit):
        return None
    with permit.lock:
        if (type(permit.used) is not bool or permit.used or permit.database != str(Path(database).resolve()) or permit.command != command
                or type(permit.expires_at) not in (int, float) or not math.isfinite(permit.expires_at) or monotonic() >= permit.expires_at):
            return None
        permit.used = True
        return permit
