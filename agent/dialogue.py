"""非业务回复边界：开放闲聊不能提供账户事实或执行凭据。"""

import re
import unicodedata
from dataclasses import dataclass

from agent.text_policy import text_policy_code

CAPABILITIES = (
    "我可以帮你查询当前账户余额、查看最近账单，以及发起一笔模拟转账。"
    "转账请提供收款账户编号和金额；小额转账需要在风险卡中确认，大额转账会被强验证阻断。"
    "确认与取消必须使用操作按钮，聊天内容不能代替授权。"
    "目前不支持理财、卡片管理、订阅或代扣管理。"
)
CLARIFICATION = "请提供完整的查询需求，或收款账户编号和转账金额；账户数据与执行结果只能通过真实工具获取。"
UNSUPPORTED = "当前尚不支持理财、卡片、订阅或代扣管理，不能替你执行这些操作。你可以让我查余额、看最近账单，或发起模拟转账。"
CONFIRMATION_HINT = "聊天中的确认不会执行任何操作。请在已有任务的风险卡上核对参数，再点击确认执行或取消。"
UNSUPPORTED_WORDS = ("理财", "申购", "赎回", "卡片", "银行卡", "信用卡", "订阅", "代扣", "挂失", "冻结", "解冻", "基金", "投资", "贷款", "定期存款")


def normalized(text: str) -> str:
    value = unicodedata.normalize("NFKC", text).lower()
    return re.sub(r"[\W_]+", "", value, flags=re.UNICODE)


def social_or_help(message: str) -> bool:
    text = normalized(message)
    return bool(re.fullmatch(r"(你好|您好|嗨|谢谢|再见|hello|hi|thanks)[呀啊哦吗呢]*", text)) or capability_question(message)


def capability_question(message: str) -> bool:
    text = normalized(message)
    return bool(re.fullmatch(r"(你|您)(能|可以|会)(帮我)?(干嘛|干什么|做(些|点)?什么)[呀啊呢吗]*", text)) or any(
        phrase in text for phrase in (
            "你能做什么", "你是谁", "支持哪些功能", "有什么功能", "怎么使用", "如何使用",
            "转账需要哪些信息", "需要提供哪些信息才能转账", "转账要提供什么", "怎么用", "继续介绍", "再介绍一下",
        )
    )


def confirmation_message(message: str) -> bool:
    return bool(re.fullmatch(r"(我)?(已经|已)?(确认|确认了|同意|同意了|取消|取消了)(执行|操作)?", normalized(message)))


def unsupported_request(message: str) -> bool:
    text = normalized(message)
    return business_request(message) and any(word in text for word in UNSUPPORTED_WORDS) and not any(word in text for word in ("查询余额", "查余额", "查询账单", "查账单"))


def business_request(message: str, history: list[dict] | None = None) -> bool:
    """只决定是否有业务请求证据，不授权执行；无操作证据的追问默认走对话。

    使用查询/操作语法及显式参数，不枚举“真的吗”等追问句式。否定、引用的
    完整操作仍交给原安全意图检查拒绝；真正执行必须继续校验参数、风险和权限。
    """
    text = normalized(message)
    if confirmation_message(message) or social_or_help(message):
        return False
    # 咨询语法优先于句中被讨论的账户/金额；讨论一条操作不等于发出操作。
    if re.match(r"^(?:请(?:帮我)?|能否|可以)?(?:解释|说明|介绍|为什么|为何|什么是|何谓)", text):
        return False
    if re.search(r"(?:为什么|为何).*(?:确认|验证|授权|规则)", text):
        return False
    polite = r"(?:(?:请|麻烦)?(?:帮我|替我)?(?:我(?:想|要|不想))?(?:不要|别|勿)?(?:模拟)?)"
    if re.match(rf"^{polite}(?:再|重新|继续)?(?:查询|查看|查|看|获取|读取).*(?:余额|账单)", text):
        return True
    if re.fullmatch(r"(?:我的?|当前账户的?|(?:账户|账号)[0-9]+的?)?(?:余额(?:是|有)?多少|账单(?:明细|记录))(?:呢|吗)?", text):
        return True
    if re.search(r"转账|转给|汇款|打款|转[0-9]", text) and re.search(r"(?:给|向|往|到|收款账户|收款账号)(?:账户|账号)?[0-9a-z_-]+", text):
        return True
    if re.match(rf"^{polite}(?:(?:转账|汇款|打款)(?=[0-9]|给|到|$)|转[0-9]|(?:给|向|往).+转[0-9])", text):
        return True
    if re.match(rf"^{polite}(?:冻结|解冻|挂失|取消|申购|赎回|购买|管理|开通)(?!是|为什么|什么)", text):
        return True
    # 只有用户自己明确提出的上一轮请求可以提供补充语境，助手历史不是授权。
    if history and re.fullmatch(r"收款(?:账户|账号)[0-9]+金额[0-9]+元", text):
        previous = next((item["content"] for item in reversed(history) if item["role"] == "user"), "")
        return business_request(previous)
    return False


def transfer_intent(message: str, history: list[dict]) -> bool:
    """保守检查当前请求的写意图；只影响提出任务，绝不产生授权。"""
    text = normalized(message)
    if social_or_help(message) or confirmation_message(message):
        return False
    if re.search(r'''["'“”‘’「」『』《》`?？]''', message):
        return False
    blocked = (
        "不要", "不能", "别", "勿", "禁止", "取消", "撤销", "不转", "不汇", "不想", "不用", "无需", "不需要",
        "假设", "如果", "若", "的话", "足够", "条件", "超过", "低于", "大于", "小于", "就",
        "例如", "举例", "引用", "怎么", "如何", "是否", "吗", "会怎样",
        "已经", "说过", "转过", "转了", "昨天", "上周", "之前", "明天", "后天", "定时", "每天", "每月",
        "分别", "各", "两笔", "两次", "再转", "再给", "和", "或", "以及", "同时", "另外",
    )
    if any(word in text for word in blocked):
        return False
    # 两个金额或两个收款对象均不能由模型任选其一。
    amounts = re.findall(r"[0-9]+(?:\.[0-9]+)?\s*(?:元|块)", message)
    recipients = re.findall(r"(?:给|向|往|到|收款账户|收款账号)\s*(?:账户|账号)?\s*[0-9A-Za-z_-]+", message)
    if len(amounts) > 1 or len(recipients) > 1:
        return False
    if any(word in text for word in ("转账", "转给", "汇款", "打款", "transfer", "remit")) or re.search(r"(给|向|往).+转|转\d+(元|块|到|给)", text):
        return True
    if any(word in text for word in ("查", "余额", "账单", "balance", "bill")):
        return False
    # 补齐上一轮用户自己的转账请求；历史助手文字不具有写意图或授权效力。
    if re.search(r"\d", text) and any(word in text for word in ("账户", "账号", "金额", "元", "块")):
        previous = next((item["content"] for item in reversed(history) if item["role"] == "user"), "")
        return transfer_intent(previous, [])
    return False


@dataclass(frozen=True)
class DialogueContext:
    """本轮服务端执行限制，不从模型、客户端或历史文本推断金融状态。"""

    transfer_pending: bool = False


def text_response(message: str, model_text: str | None, *, context: DialogueContext | None = None) -> dict:
    context = context or DialogueContext()
    policy = text_policy_code(model_text)
    codes = []
    if confirmation_message(message):
        kind, status, reply = "clarification", "needs_input", CONFIRMATION_HINT
    elif unsupported_request(message) and not social_or_help(message):
        kind, status, reply = "unsupported", "completed", UNSUPPORTED
    elif capability_question(message):
        kind, status, reply = "capabilities", "completed", CAPABILITIES
    elif business_request(message):
        kind, status, reply = "clarification", "needs_input", "本轮未获得可用的业务工具结果，尚未执行操作。请明确单笔业务需求；账户数据与执行结果必须通过真实工具取得。"
        codes.append("BUSINESS_RESULT_UNAVAILABLE")
    elif policy == "TEXT_ALLOWED":
        kind, status, reply = "conversation", "completed", model_text.strip()
    elif policy == "POLICY_REFERENCE_REQUIRED":
        # 涉及数字阈值的规则直接引用现有代码常量，模型不能改写金额策略。
        from security.risk_engine import RiskEngine
        threshold = format(RiskEngine.TRANSFER_CONFIRMATION_LIMIT, "f")
        kind, status = "conversation", "completed"
        reply = f"本 Demo 按北京时间业务日统计当前账户已成功转出的金额，再加上本笔金额：正数累计不超过{threshold}元需要在风险卡中确认；累计超过{threshold}元需要强验证，当前没有强验证放行入口。确认执行前会重新统计，聊天不能代替确认或验证。"
        codes.append("TRUSTED_POLICY_EXPLANATION")
    elif social_or_help(message):
        kind, status, reply = "conversation", "completed", "你好！你可以继续交流，或了解当前已实现的银行助手功能。"
        codes.append(policy)
    elif policy in {"MODEL_EMPTY_TEXT", "MODEL_TEXT_TOO_LONG"}:
        kind, status, reply = "conversation", "failed", "模型未返回可用的对话内容，本轮没有执行业务操作。请稍后重新说明。"
        codes.append(policy)
    else:
        kind, status = "conversation", "completed"
        if context.transfer_pending:
            reply = "服务端仍有待确认的转账任务，这轮聊天没有确认或执行它。请通过原风险卡确认或取消；我可以继续解释规则。"
        else:
            reply = "这轮没有业务工具结果，不能据此确认余额、交易、权限或卡片状态。我可以继续解释规则；需要核对实际数据时，请发起查询。"
        codes.append(policy)
    return {"status": status, "reply": reply, "tool_results": [], "response_kind": kind, "diagnostic_codes": codes}


def clarification_response(code: str) -> dict:
    replies = {
        "MISSING_PARAMETERS": "请明确提供收款账户编号和人民币金额，不会猜测缺失参数。尚未执行转账。",
        "INTENT_REJECTED": "请求含有歧义或不是明确的单笔当前转账命令，尚未执行。请直接说明一笔操作及完整参数。",
        "ACCOUNT_MISMATCH": "请求指定的账户与当前服务端账户不一致，不能切换账户办理此操作。尚未执行。",
        "PENDING_EXISTS": "当前已有待确认任务。可以继续聊天和查询，请先用原任务的确认或取消按钮处理，再发起新转账。",
    }
    return {"status": "needs_input", "reply": replies[code], "tool_results": [], "response_kind": "clarification"}


def safe_social_text(value: str | None) -> bool:
    """兼容原调用者；术语本身不是拒绝理由，具体事实与凭据仍须审查。"""
    return text_policy_code(value) == "TEXT_ALLOWED"
