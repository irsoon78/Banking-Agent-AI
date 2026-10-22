"""卡片只读查询。创建表、导入样本及状态修改均不属于查询服务。"""

from contextlib import contextmanager
from pathlib import Path
import re
import sqlite3

from bill_analysis.service import AnalysisService
from bill_analysis.storage import AnalysisError
from card_management.storage import CardError, SOURCE, card_from_row, now_stamp, public_card, public_operation, valid_id


class CardService:
    """账户由未来可信服务端上下文注入；本类不提供身份认证。"""
    def __init__(self, db_path, *, account_id):
        self._path = Path(db_path).resolve()
        self._account = valid_id(account_id)

    @contextmanager
    def _read(self):
        conn = None
        try:
            conn = sqlite3.connect(self._path.as_uri() + "?mode=ro", uri=True, timeout=2)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA query_only=ON")
            conn.execute("BEGIN")
            yield conn
        except (sqlite3.Error, OSError):
            raise CardError("CARD_DATABASE_UNAVAILABLE", "卡片数据无法可靠读取，未生成结果。") from None
        finally:
            if conn is not None:
                conn.close()

    def _account_exists(self, conn):
        if conn.execute("SELECT 1 FROM accounts WHERE account_id=?", (self._account,)).fetchone() is None:
            raise CardError("ACCOUNT_NOT_FOUND", "当前账户不存在。")

    def list_cards(self):
        with self._read() as conn:
            self._account_exists(conn)
            rows = conn.execute("SELECT * FROM cards WHERE account_id=? ORDER BY card_id", (self._account,)).fetchall()
            return {"ok": True, "account_id": self._account, "source": SOURCE, "read_at": now_stamp(), "cards": [public_card(card_from_row(row)) for row in rows]}

    def get_card(self, card_id):
        valid_id(card_id)
        with self._read() as conn:
            self._account_exists(conn)
            row = conn.execute("SELECT * FROM cards WHERE account_id=? AND card_id=?", (self._account, card_id)).fetchone()
            if row is None:
                raise CardError("CARD_NOT_FOUND", "当前账户不存在此卡片。")
            return {"ok": True, "account_id": self._account, "source": SOURCE, "read_at": now_stamp(), "card": public_card(card_from_row(row))}

    def resolve_card_target(self, display_name, last_four):
        """当前账户内完整名称及展示尾号同时精确匹配；不是认证或执行授权。"""
        if not isinstance(display_name, str) or not 1 <= len(display_name) <= 60 or not isinstance(last_four, str) or not re.fullmatch(r"[0-9]{4}", last_four):
            raise CardError("INVALID_ARGUMENTS", "请提供完整卡名及四位展示尾号。")
        result = self.list_cards()
        matches = [card for card in result["cards"] if card["display_name"] == display_name and card["last_four"] == last_four]
        if not matches:
            raise CardError("CARD_TARGET_NOT_FOUND", "当前账户内卡名和尾号未同时匹配，请重新查询并核对。尚未执行。")
        if len(matches) != 1:
            raise CardError("CARD_TARGET_AMBIGUOUS", "当前账户内存在多个相同卡名及尾号，请查询后提供唯一card_id。尚未执行。")
        return {**{key: value for key, value in result.items() if key != "cards"}, "card": matches[0]}

    def card_for_consumption(self, record_id, *, dataset_id=None):
        """按可信原始记录取引用，再按账户查不可变映射；不会猜测名称或尾号。"""
        valid_id(record_id)
        try:
            analysis = AnalysisService(self._path, account_id=self._account)
            if dataset_id is None:
                dataset_id = analysis.current_dataset()["dataset_id"]
            record = analysis.get_record(dataset_id, record_id)["record"]
        except AnalysisError as exc:
            raise CardError(exc.code, str(exc)) from None
        reference = record["payment_instrument_ref"]
        result = {"ok": True, "account_id": self._account, "source": SOURCE, "dataset_id": dataset_id,
                  "record_id": record_id, "payment_instrument_ref": reference, "read_at": now_stamp()}
        if reference is None:
            return {**result, "linked": False, "reason_code": "NO_PAYMENT_INSTRUMENT", "card": None}
        with self._read() as conn:
            row = conn.execute(
                "SELECT c.* FROM card_instrument_links l JOIN cards c ON c.account_id=l.account_id AND c.card_id=l.card_id "
                "WHERE l.account_id=? AND l.payment_instrument_ref=?", (self._account, reference),
            ).fetchone()
            if row is None:
                return {**result, "linked": False, "reason_code": "NO_MAPPING", "card": None}
            return {**result, "linked": True, "reason_code": "LINKED", "card": public_card(card_from_row(row))}

    def get_operation(self, action_id):
        valid_id(action_id)
        with self._read() as conn:
            self._account_exists(conn)
            row = conn.execute("SELECT * FROM card_operations WHERE account_id=? AND action_id=?", (self._account, action_id)).fetchone()
            if row is None:
                raise CardError("OPERATION_NOT_FOUND", "当前账户不存在此卡片操作凭据。")
            result = public_operation(row)
            audit = conn.execute("SELECT request_id FROM audit_logs WHERE account_id=? AND action_id=? AND event_type='card_operation_committed' ORDER BY id LIMIT 1", (self._account, action_id)).fetchone()
            if audit:
                result.update(request_id=audit["request_id"], audit_id=audit["request_id"])
            return result
