"""模拟卡片模型、纯状态机与显式离线建表；不改变账户或消费账本。"""

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import re
import sqlite3

from bill_analysis.storage import identifier, utc_instant, AnalysisError

SOURCE = "synthetic_card_demo_v1"
MAX_VERSION = 9_223_372_036_854_775_807
TEMPORARY_OPERATIONS = frozenset({"temporary_freeze", "temporary_unfreeze"})
RED_OPERATIONS = frozenset({"report_loss", "cancel_loss_report"})


class CardError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def valid_id(value):
    try:
        return identifier(value)
    except AnalysisError:
        raise CardError("INVALID_ARGUMENTS", "标识无效。") from None


def now_stamp():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


@dataclass(frozen=True)
class Card:
    card_id: str
    account_id: str
    display_name: str
    last_four: str
    temporary_frozen: bool
    loss_status: str
    version: int
    created_at: str
    updated_at: str
    source: str = SOURCE


@dataclass(frozen=True)
class InstrumentLink:
    account_id: str
    payment_instrument_ref: str
    card_id: str


@dataclass(frozen=True)
class CardCommand:
    account_id: str
    card_id: str
    operation: str
    expected_version: int
    action_id: str


def validate_card(card):
    valid_id(card.account_id)
    valid_id(card.card_id)
    if not card.card_id.startswith("card-") or re.search(r"\d{12,}", card.card_id):
        raise CardError("INVALID_DATA", "卡片必须使用非敏感内部标识。")
    if not isinstance(card.display_name, str) or not 1 <= len(card.display_name.strip()) <= 60 or re.search(r"\d{12,}", card.display_name):
        raise CardError("INVALID_DATA", "模拟卡片名称无效。")
    if not isinstance(card.last_four, str) or not re.fullmatch(r"[0-9]{4}", card.last_four):
        raise CardError("INVALID_DATA", "只允许四位展示尾号。")
    if type(card.temporary_frozen) is not bool or card.loss_status not in ("normal", "reported") or type(card.version) is not int or not 0 <= card.version <= MAX_VERSION or card.source != SOURCE:
        raise CardError("INVALID_DATA", "卡片状态或来源无法可靠解释。")
    try:
        created, updated = utc_instant(card.created_at), utc_instant(card.updated_at)
        if updated < created:
            raise ValueError
    except (AnalysisError, ValueError):
        raise CardError("INVALID_DATA", "卡片时间无效。") from None


def validate_link(link):
    valid_id(link.account_id)
    valid_id(link.card_id)
    reference = link.payment_instrument_ref
    if not isinstance(reference, str) or not re.fullmatch(r"demo-instrument-[a-z][a-z0-9_-]{0,47}", reference) or re.search(r"\d{12,}", reference):
        raise CardError("INVALID_DATA", "支付工具引用必须是非敏感内部标识。")


def validate_command(command):
    if not isinstance(command, CardCommand):
        raise CardError("INVALID_ARGUMENTS", "卡片命令格式无效。")
    for value in (command.account_id, command.card_id, command.action_id):
        valid_id(value)
    if not isinstance(command.operation, str):
        raise CardError("INVALID_ARGUMENTS", "操作类型无效。")
    if command.operation in RED_OPERATIONS:
        raise CardError("VERIFICATION_REQUIRED", "正式挂失和解挂需要强验证，当前不开放执行。")
    if command.operation not in TEMPORARY_OPERATIONS:
        raise CardError("UNSUPPORTED_OPERATION", "不支持此卡片状态操作。")
    if type(command.expected_version) is not int or not 0 <= command.expected_version <= MAX_VERSION:
        raise CardError("INVALID_ARGUMENTS", "卡片版本无效。")


def effective_state(temporary_frozen, loss_status):
    return "reported_lost" if loss_status == "reported" else "temporarily_frozen" if temporary_frozen else "active"


def public_card(card):
    validate_card(card)
    value = asdict(card)
    value.update(effective_status=effective_state(card.temporary_frozen, card.loss_status),
                 usable_in_demo=not card.temporary_frozen and card.loss_status == "normal")
    return value


def plan_transition(card, command):
    """纯计算，不产生授权、不访问数据库。拒绝重复操作，不隐式解挂。"""
    validate_card(card)
    validate_command(command)
    if card.account_id != command.account_id or card.card_id != command.card_id:
        raise CardError("CARD_NOT_FOUND", "当前账户不存在此卡片。")
    if card.version != command.expected_version:
        raise CardError("VERSION_CONFLICT", "卡片状态已变化，请重新查看并发起操作。")
    if card.version == MAX_VERSION:
        raise CardError("VERSION_LIMIT", "卡片版本超出可更新范围，已阻断。")
    if card.loss_status != "normal":
        raise CardError("CARD_REPORTED_LOST", "卡片已正式挂失，临时操作不能解除挂失或恢复使用。")
    target = command.operation == "temporary_freeze"
    if card.temporary_frozen == target:
        raise CardError("ALREADY_FROZEN" if target else "NOT_TEMPORARILY_FROZEN", "卡片已临时冻结，无需重复操作。" if target else "卡片没有临时冻结，不能重复解除。")
    return {"temporary_frozen": target, "loss_status": card.loss_status, "version": card.version + 1}


SCHEMA = (
    """CREATE TABLE IF NOT EXISTS cards (
        card_id TEXT PRIMARY KEY,
        account_id TEXT NOT NULL REFERENCES accounts(account_id),
        display_name TEXT NOT NULL CHECK(length(display_name) BETWEEN 1 AND 60),
        last_four TEXT NOT NULL CHECK(last_four GLOB '[0-9][0-9][0-9][0-9]' AND length(last_four)=4),
        temporary_frozen INTEGER NOT NULL CHECK(typeof(temporary_frozen)='integer' AND temporary_frozen IN (0,1)),
        loss_status TEXT NOT NULL CHECK(loss_status IN ('normal','reported')),
        version INTEGER NOT NULL CHECK(typeof(version)='integer' AND version>=0),
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        source TEXT NOT NULL CHECK(source='synthetic_card_demo_v1'),
        UNIQUE(account_id,card_id)
    )""",
    """CREATE TABLE IF NOT EXISTS card_instrument_links (
        account_id TEXT NOT NULL,
        payment_instrument_ref TEXT NOT NULL,
        card_id TEXT NOT NULL,
        PRIMARY KEY(account_id,payment_instrument_ref),
        FOREIGN KEY(account_id,card_id) REFERENCES cards(account_id,card_id)
    )""",
    """CREATE TABLE IF NOT EXISTS card_operations (
        operation_id TEXT PRIMARY KEY,
        action_id TEXT NOT NULL UNIQUE,
        account_id TEXT NOT NULL,
        card_id TEXT NOT NULL,
        operation TEXT NOT NULL CHECK(operation IN ('temporary_freeze','temporary_unfreeze')),
        before_temporary_frozen INTEGER NOT NULL CHECK(before_temporary_frozen IN (0,1)),
        after_temporary_frozen INTEGER NOT NULL CHECK(after_temporary_frozen IN (0,1)),
        before_loss_status TEXT NOT NULL CHECK(before_loss_status='normal'),
        after_loss_status TEXT NOT NULL CHECK(after_loss_status=before_loss_status),
        before_version INTEGER NOT NULL CHECK(typeof(before_version)='integer' AND before_version>=0),
        after_version INTEGER NOT NULL CHECK(typeof(after_version)='integer' AND after_version=before_version+1),
        executed_at TEXT NOT NULL,
        result_status TEXT NOT NULL CHECK(result_status='completed'),
        UNIQUE(account_id,card_id,after_version),
        CHECK((operation='temporary_freeze' AND before_temporary_frozen=0 AND after_temporary_frozen=1) OR
              (operation='temporary_unfreeze' AND before_temporary_frozen=1 AND after_temporary_frozen=0)),
        FOREIGN KEY(account_id,card_id) REFERENCES cards(account_id,card_id)
    )""",
    "CREATE INDEX IF NOT EXISTS idx_cards_account ON cards(account_id,card_id)",
    "CREATE INDEX IF NOT EXISTS idx_card_operations_account ON card_operations(account_id,executed_at,operation_id)",
    """CREATE TRIGGER IF NOT EXISTS card_links_no_update BEFORE UPDATE ON card_instrument_links
       BEGIN SELECT RAISE(ABORT,'instrument link is immutable'); END""",
    """CREATE TRIGGER IF NOT EXISTS card_links_no_delete BEFORE DELETE ON card_instrument_links
       BEGIN SELECT RAISE(ABORT,'instrument link is immutable'); END""",
    """CREATE TRIGGER IF NOT EXISTS card_receipts_no_update BEFORE UPDATE ON card_operations
       BEGIN SELECT RAISE(ABORT,'card receipt is immutable'); END""",
    """CREATE TRIGGER IF NOT EXISTS card_receipts_no_delete BEFORE DELETE ON card_operations
       BEGIN SELECT RAISE(ABORT,'card receipt is immutable'); END""",
    """CREATE TRIGGER IF NOT EXISTS cards_guard_update BEFORE UPDATE ON cards
       WHEN NEW.card_id!=OLD.card_id OR NEW.account_id!=OLD.account_id OR NEW.display_name!=OLD.display_name
         OR NEW.last_four!=OLD.last_four OR NEW.source!=OLD.source OR NEW.created_at!=OLD.created_at
         OR NEW.loss_status!=OLD.loss_status OR OLD.loss_status!='normal'
         OR NEW.temporary_frozen=OLD.temporary_frozen OR NEW.version!=OLD.version+1
       BEGIN SELECT RAISE(ABORT,'invalid card state transition'); END""",
)


def card_from_row(row):
    value = {name: row[name] for name in Card.__dataclass_fields__}
    if type(value["temporary_frozen"]) is not int or value["temporary_frozen"] not in (0, 1):
        raise CardError("INVALID_DATA", "临时冻结状态无效。")
    value["temporary_frozen"] = bool(value["temporary_frozen"])
    card = Card(**value)
    validate_card(card)
    return card


def public_operation(row):
    before = {"temporary_frozen": bool(row["before_temporary_frozen"]), "loss_status": row["before_loss_status"], "version": row["before_version"]}
    after = {"temporary_frozen": bool(row["after_temporary_frozen"]), "loss_status": row["after_loss_status"], "version": row["after_version"]}
    return {"ok": True, "source": SOURCE, "operation_id": row["operation_id"], "action_id": row["action_id"],
            "account_id": row["account_id"], "card_id": row["card_id"], "operation": row["operation"],
            "before": before, "after": after, "executed_at": row["executed_at"], "result_status": row["result_status"]}


def import_card_samples(conn, cards, links):
    """显式离线导入；只增补卡片/映射，不重置已有运行状态，不写旧表。"""
    cards, links = list(cards), list(links)
    card_ids, link_ids = set(), set()
    for card in cards:
        validate_card(card)
        if card.card_id in card_ids:
            raise CardError("SEED_CONFLICT", "样本卡片ID重复。")
        card_ids.add(card.card_id)
    for link in links:
        validate_link(link)
        key = (link.account_id, link.payment_instrument_ref)
        if key in link_ids:
            raise CardError("SEED_CONFLICT", "样本支付工具引用重复。")
        link_ids.add(key)
    if conn.in_transaction:
        raise CardError("IMPORT_TRANSACTION", "样本导入必须使用独立空闲连接。")
    inserted = 0
    old_factory = conn.row_factory
    try:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("BEGIN IMMEDIATE")
        for sql in SCHEMA:
            conn.execute(sql)
        for card in cards:
            row = conn.execute("SELECT * FROM cards WHERE card_id=?", (card.card_id,)).fetchone()
            if row:
                current = card_from_row(row)
                if any(getattr(current, key) != getattr(card, key) for key in ("account_id", "display_name", "last_four", "loss_status", "created_at", "source")):
                    raise CardError("SEED_CONFLICT", "已有卡片身份或样本定义不同，拒绝覆盖。")
            else:
                conn.execute("INSERT INTO cards(card_id,account_id,display_name,last_four,temporary_frozen,loss_status,version,created_at,updated_at,source) VALUES(?,?,?,?,?,?,?,?,?,?)", tuple(asdict(card).values()))
                inserted += 1
        for link in links:
            old = conn.execute("SELECT card_id FROM card_instrument_links WHERE account_id=? AND payment_instrument_ref=?", (link.account_id, link.payment_instrument_ref)).fetchone()
            if old and old["card_id"] != link.card_id:
                raise CardError("SEED_CONFLICT", "支付工具已关联其他卡片，禁止迁移历史关系。")
            if old is None:
                conn.execute("INSERT INTO card_instrument_links(account_id,payment_instrument_ref,card_id) VALUES(?,?,?)", tuple(asdict(link).values()))
        conn.commit()
        return {"cards_inserted": inserted, "source": SOURCE}
    except CardError:
        conn.rollback()
        raise
    except sqlite3.Error:
        conn.rollback()
        raise CardError("IMPORT_FAILED", "卡片样本导入失败，未提交。") from None
    finally:
        conn.row_factory = old_factory
