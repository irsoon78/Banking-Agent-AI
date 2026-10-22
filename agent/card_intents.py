"""小范围卡片指令语法。只识别请求，既不授权也不从助手历史猜测目标。"""

from dataclasses import dataclass
import re
import unicodedata


@dataclass(frozen=True)
class CardResolution:
    tool: str | None
    arguments: dict
    code: str


def resolve_card_request(message, account_id):
    text = unicodedata.normalize("NFKC", message).strip()
    if not re.search(r"银行卡|卡片|信用卡|我的卡|这张卡|冻结|解冻|挂失|解挂|card-[a-zA-Z0-9_-]+|卡操作", text):
        return None
    reject = lambda code: CardResolution(None, {}, code)
    if any(value != account_id for value in re.findall(r"(?:账户|账号)\s*([0-9]+)", text)):
        return reject("ACCOUNT_MISMATCH")
    # 先区分只读请求与写请求：“以及”和问号不代表列表查询含有多个写操作。
    # 只豁免这类中性语法；否定、假设、引用等仍拒绝，写意图继续使用完整限制。
    read_query = bool(re.search(r"查询|查看|列出|有哪些|列表|看看", text)) and not re.search(r"冻结|解冻|挂失|解挂|转账|转给|汇款|打款|扣款", text)
    blocked = re.findall(r"不要|别|不想|不需要|禁止|假设|如果|假如|若|的话|只是问|怎么|如何|为什么|是否|吗|盗刷|停掉|昨天|已经|说过|明天|定时|每天|分别|同时|以及|然后|或者|[\"'“”‘’`?？]", text)
    if blocked and (not read_query or any(part not in {"以及", "?", "？"} for part in blocked)):
        return reject("CARD_INTENT_REQUIRED")
    ids = re.findall(r"card-[a-zA-Z0-9_-]+", text)
    if len(ids) > 1:
        return reject("CARD_TARGET_REQUIRED")
    operation = re.search(r"卡片?操作(?:ID|编号)?\s*[:：]?\s*([a-zA-Z0-9][a-zA-Z0-9_-]{0,63})", text)
    if operation and re.search(r"查询|查看|结果|凭据", text) and not re.search(r"冻结|挂失|解挂", text):
        return CardResolution("get_card_operation", {"action_id": operation[1]}, "CARD_READ")
    record = re.search(r"(?:原始)?(?:消费)?记录(?:ID|编号)?\s*[:：]?\s*([a-zA-Z][a-zA-Z0-9_-]{0,63})", text)
    if record and re.search(r"对应|关联|银行卡|卡片", text) and not re.search(r"冻结|解冻|挂失|解挂", text):
        return CardResolution("get_card_for_spending_record", {"record_id": record[1]}, "CARD_READ")
    if re.search(r"冻结|解冻|挂失|解挂", text):
        # 整句匹配避免把一句引用、多动作或复合条件缩减为单笔命令。
        prefix = r"(?:请|麻烦)?(?:帮我|替我)?(?:把)?"
        patterns = (
            ("remove_temporary_freeze", r"解除临时冻结"),
            ("temporarily_freeze_card", r"临时冻结"),
            ("cancel_card_loss_report", r"(?:解除挂失|取消挂失|解挂)"),
            ("report_card_loss", r"(?:正式挂失|挂失)"),
        )
        if not ids:
            # 名称仅提取原文，不做 NFKC、大小写或相似匹配；尾号必须明确且为四位。
            # 此处不产生 card_id，随后须由当前账户的只读服务唯一解析。
            target = r"(?P<display_name>[^,，。!！?？;；\r\n]{1,60}?)\s*[,，]\s*(?:银行卡|卡片|卡)?尾号\s*(?P<last_four>[0-9]{4})"
            for tool, verb in patterns:
                for command in (verb + r"\s*" + target, target + r"\s*" + verb):
                    match = re.fullmatch(prefix + command + r"[。!！\s]*", message.strip())
                    if match:
                        return CardResolution(tool, match.groupdict(), "CARD_NAMED_TARGET")
            return reject("CARD_TARGET_REQUIRED")
        target = r"(?:银行卡|卡片|卡)?\s*" + re.escape(ids[0])
        for tool, verb in patterns:
            if re.fullmatch(prefix + "(?:" + verb + r"\s*" + target + "|" + target + r"\s*" + verb + r")[。!！\s]*", text):
                return CardResolution(tool, {"card_id": ids[0]}, "CARD_WRITE_REQUEST")
        return reject("CARD_INTENT_REQUIRED")
    if ids and re.search(r"查询|查看|状态|详情|看一下", text):
        return CardResolution("get_card", {"card_id": ids[0]}, "CARD_READ")
    if re.search(r"查询|查看|列出|有哪些|列表|看看", text) and re.search(r"银行卡|卡片|我的卡", text):
        return CardResolution("list_cards", {}, "CARD_READ")
    return reject("CARD_INTENT_REQUIRED")
