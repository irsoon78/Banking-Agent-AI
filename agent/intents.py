"""有限的请求解析和参数出处检查；不做风险判断、不访问数据库、不执行工具。

兜底只完整匹配当前用户原文，不剥除引号、条件或小数点，不从历史或模型文本补参数。
模型提出转账时另检查参数是否来自用户；只允许一轮明确标注的用户补充信息。
"""

from dataclasses import dataclass
from decimal import Decimal
import re
import unicodedata

from agent.dialogue import transfer_intent, business_request


@dataclass(frozen=True)
class IntentResolution:
    tool: str | None
    arguments: dict
    code: str


# 数字内部不接受空格；不使用 normalized()，防止 500.00 被变成 50000。
ACCOUNT = r"[0-9]{1,64}"
AMOUNT = r"[0-9]{1,10}(?:\.[0-9]{1,2})?"
TRANSFER = re.compile(
    rf"(?:请\s*)?(?:帮我\s*)?(?:从\s*(?:账户|账号)?\s*(?P<sender>{ACCOUNT})\s*)?"
    rf"(?:给|向|往)\s*(?:账户|账号)?\s*(?P<recipient>{ACCOUNT})\s*"
    rf"(?:模拟\s*)?(?:转账|转|汇款|打款)\s*(?P<amount>{AMOUNT})\s*(?:元|块)"
)
READ = re.compile(
    rf"(?:请\s*)?(?:帮我\s*)?(?:查询|查看|查|看)\s*(?:一下|下)?\s*"
    rf"(?:我(?:的)?\s*|(?:账户|账号)\s*(?P<account>{ACCOUNT})\s*(?:的\s*)?)?"
    r"(?:最近|当前)?\s*(?:的\s*)?(?:(?P<count>[0-9]{1,3})\s*条\s*)?(?P<kind>余额|账单)"
)
RECIPIENT = re.compile(r"(?:给|向|往|到|收款账户|收款账号)\s*(?:账户|账号)?\s*([0-9A-Za-z_-]{1,64})(?![0-9A-Za-z_-])")
MONEY = re.compile(rf"(?<![0-9A-Za-z_.,+\-−])({AMOUNT})\s*(?:元|块)")
SENDER = re.compile(r"(?:从|使用|用|转出(?:账户|账号))\s*(?:账户|账号)?\s*([0-9A-Za-z_-]+)")


def command_text(message: str) -> str:
    return unicodedata.normalize("NFKC", message).strip().rstrip("。!").strip()


def resolve_request(message: str, account_id: str) -> IntentResolution | None:
    if not business_request(message):
        return None
    text = command_text(message)
    match = TRANSFER.fullmatch(text)
    if match:
        if not transfer_intent(message, []):
            return IntentResolution(None, {}, "INTENT_REJECTED")
        if match["sender"] and match["sender"] != account_id:
            return IntentResolution(None, {}, "ACCOUNT_MISMATCH")
        if Decimal(match["amount"]) <= 0:
            return IntentResolution(None, {}, "INTENT_REJECTED")
        return IntentResolution("transfer_money", {"to_account_id": match["recipient"], "amount": match["amount"]}, "TRANSFER_FALLBACK")
    match = READ.fullmatch(text)
    if match:
        if match["account"] and match["account"] != account_id:
            return IntentResolution(None, {}, "ACCOUNT_MISMATCH")
        if match["kind"] == "余额":
            return None if match["count"] else IntentResolution("get_balance", {}, "READ_FALLBACK")
        count = int(match["count"] or 10)
        return IntentResolution("get_bills", {"limit": count}, "READ_FALLBACK") if 1 <= count <= 100 else None
    # 只有明确缺少参数才标注 MISSING_PARAMETERS；完整但不认识的表达不能断言缺信息。
    if transfer_intent(message, []):
        senders = SENDER.findall(text)
        if any(sender != account_id for sender in senders):
            return IntentResolution(None, {}, "ACCOUNT_MISMATCH")
        if not RECIPIENT.search(text) or not MONEY.search(text):
            return IntentResolution(None, {}, "MISSING_PARAMETERS")
    elif re.search(r"转账|转给|汇款|打款|转\s*[0-9]", text):
        return IntentResolution(None, {}, "INTENT_REJECTED")
    return None


def transfer_request_issue(message: str, account_id: str, arguments: dict, history: list[dict]) -> str | None:
    """校验模型参数的用户出处；不会把历史助手文本当作参数或授权。"""
    if not transfer_intent(message, history):
        return "INTENT_REJECTED"
    text = command_text(message)
    # 多轮仅接受直接上一轮用户明确提出转账后的完整、标注补充；兜底不使用此分支。
    if not transfer_intent(message, []) and history:
        previous = next((item["content"] for item in reversed(history) if item["role"] == "user"), "")
        if not re.fullmatch(rf"收款(?:账户|账号)\s*{ACCOUNT}\s*[,，]\s*金额\s*{AMOUNT}\s*(?:元|块)", text):
            return "INTENT_REJECTED"
        if any(sender != account_id for sender in SENDER.findall(command_text(previous))):
            return "ACCOUNT_MISMATCH"
    if any(sender != account_id for sender in SENDER.findall(text)):
        return "ACCOUNT_MISMATCH"
    recipient_matches, amount_matches = list(RECIPIENT.finditer(text)), list(MONEY.finditer(text))
    recipients = [match[1] for match in recipient_matches]
    amounts = [match[1] for match in amount_matches]
    if not recipients or not amounts:
        return "MISSING_PARAMETERS"
    if len(recipients) != 1 or len(amounts) != 1:
        return "INTENT_REJECTED"
    if max(recipient_matches[0].start(1), amount_matches[0].start(1)) < min(recipient_matches[0].end(1), amount_matches[0].end(1)):
        return "INTENT_REJECTED"
    try:
        same_amount = Decimal(amounts[0]) == Decimal(arguments["amount"]) > 0
    except ArithmeticError:
        return "INTENT_REJECTED"
    if recipients[0] != arguments["to_account_id"] or not same_amount:
        return "INTENT_REJECTED"
    account_matches = list(re.finditer(r"(?:账户|账号)\s*([0-9A-Za-z_-]+)", text))
    references = [match[1] for match in account_matches]
    if any(reference not in {account_id, arguments["to_account_id"]} for reference in references):
        return "ACCOUNT_MISMATCH"
    # 未归属的数字可能是第二个收款人或备选金额，不能由模型忽略后任选一笔。
    spans = [match.span(1) for match in (*recipient_matches, *amount_matches, *account_matches, *SENDER.finditer(text))]
    for number in re.finditer(r"[0-9]+(?:\.[0-9]+)?", text):
        if not any(start <= number.start() and number.end() <= end for start, end in spans):
            return "INTENT_REJECTED"
    return None
