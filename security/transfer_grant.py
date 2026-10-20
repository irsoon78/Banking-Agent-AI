"""仅供服务端确认流程使用的一次性许可；不是 API 字段或强验证凭据。"""

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from threading import Lock


@dataclass
class _Grant:
    account_id: str
    recipient: str | None
    amount: str | None
    logger: object
    context: dict
    used: bool = False
    lock: Lock = field(default_factory=Lock)


_active: ContextVar[_Grant | None] = ContextVar("confirmed_transfer_grant", default=None)


@contextmanager
def confirmed_transfer_scope(account_id: str, params: dict, logger=None, context: dict | None = None):
    """只能在服务端已领取用户确认任务后建立；不接受模型提供的授权。"""
    grant = _Grant(account_id, params.get("to_account_id"), params.get("amount"), logger,
                   context if context is not None else {})
    token = _active.set(grant)
    try:
        yield
    finally:
        _active.reset(token)


def claim_transfer(account_id: str, recipient: str, amount: str):
    grant = _active.get()
    if grant is None:
        return None
    with grant.lock:
        if grant.used or (account_id, recipient, amount) != (grant.account_id, grant.recipient, grant.amount):
            return None
        grant.used = True
    return grant
