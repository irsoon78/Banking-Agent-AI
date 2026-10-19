"""非业务回复边界：开放闲聊不能提供账户事实或执行凭据。"""

import re
import unicodedata

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
    return bool(re.fullmatch(r"(你好|您好|嗨|谢谢|再见|hello|hi|thanks)[呀啊哦吗呢]*", text)) or any(
        phrase in text for phrase in (
            "你能做什么", "你是谁", "支持哪些功能", "有什么功能", "怎么使用", "如何使用",
            "转账需要哪些信息", "需要提供哪些信息才能转账", "转账要提供什么", "怎么用", "继续介绍", "再介绍一下",
        )
    )


def confirmation_message(message: str) -> bool:
    return bool(re.fullmatch(r"(我)?(已经|已)?(确认|确认了|同意|同意了|取消|取消了)(执行|操作)?", normalized(message)))


def unsupported_request(message: str) -> bool:
    text = normalized(message)
    return any(word in text for word in UNSUPPORTED_WORDS) and not any(word in text for word in ("查询余额", "查余额", "查询账单", "查账单"))


def transfer_intent(message: str, history: list[dict]) -> bool:
    """保守检查当前请求的写意图；只影响提出任务，绝不产生授权。"""
    text = normalized(message)
    if social_or_help(message) or confirmation_message(message):
        return False
    if any(word in text for word in ("不要", "不能", "别", "取消", "撤销", "假设", "如果", "例如", "举例", "引用", "怎么", "如何", "不转", "不汇", "不想", "不用", "无需", "不需要", "已经", "转过", "转了", "昨天", "上周", "之前")):
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


def text_response(message: str, model_text: str | None) -> dict:
    text = normalized(message)
    if confirmation_message(message):
        kind, status, reply = "clarification", "needs_input", CONFIRMATION_HINT
    elif unsupported_request(message) and not social_or_help(message):
        kind, status, reply = "unsupported", "completed", UNSUPPORTED
    elif social_or_help(message):
        if any(word in text for word in ("功能", "能做", "使用", "信息", "提供", "你是谁", "怎么用", "介绍")):
            kind, status, reply = "capabilities", "completed", CAPABILITIES
        else:
            kind, status = "conversation", "completed"
            reply = model_text.strip() if safe_social_text(model_text) else "你好！很高兴见到你。你可以问我支持哪些功能，或者告诉我想办理什么业务。"
    elif any(word in text for word in ("余额", "账单", "转账", "转给", "汇款", "账户", "账号", "balance", "transfer")):
        kind, status, reply = "clarification", "needs_input", CLARIFICATION
    elif safe_social_text(model_text):
        kind, status, reply = "conversation", "completed", model_text.strip()
    else:
        kind, status, reply = "clarification", "needs_input", CLARIFICATION
    return {"status": status, "reply": reply, "tool_results": [], "response_kind": kind}


def safe_social_text(value: str | None) -> bool:
    """保守的文本输出策略，而非金融事实验证器；涉及业务的内容使用可信模板。

    未授权的模型字段永不进入结构化业务结果。无法可靠审阅的金融自由文本回退，
    避免将普通回复渲染为交易完成。语义/同义词覆盖不是开放文本的绝对防幻觉保证。
    """
    if not isinstance(value, str) or not value.strip() or len(value) > 1200:
        return False
    text = normalized(value)
    forbidden = (
        "余额", "账单", "转账", "转入", "转出", "扣款", "收款", "到账", "交易", "付款", "支付",
        "理财", "申购", "赎回", "卡片", "银行卡", "信用卡", "订阅", "代扣", "账户", "账号", "帐号",
        "已完成", "已执行", "成功执行", "执行成功", "已确认", "验证通过", "操作成功", "办理成功", "已办妥", "已处理",
        "存款", "资金", "本金", "手续费", "收益", "人民币", "金额", "挂失", "冻结", "解冻", "贷款", "基金", "投资",
        "balance", "bill", "transfer", "payment", "paid", "transaction", "account", "investment", "card",
        "subscription", "confirmed", "verified", "executed", "success", "risklevel", "permission", "actionid",
    )
    return not any(word in text for word in forbidden) and not re.search(r"[¥￥$]|\d+\s*(元|cny|rmb)", value, re.IGNORECASE)
