"""无工具文本的保守审查：区分说明与金融事实，不产生执行凭据或授权。

允许模型自行组织普通交流和概念说明；具体账户事实、操作状态与结构化凭据
必须来自工具。无法审阅的金融断言回退，不声称这是任意语言的形式化验证器。
"""

import re
import unicodedata


def text_policy_code(value: str | None) -> str:
    if not isinstance(value, str) or not value.strip():
        return "MODEL_EMPTY_TEXT"
    if len(value) > 1200:
        return "MODEL_TEXT_TOO_LONG"
    raw = unicodedata.normalize("NFKC", value).lower()
    text = re.sub(r"[\W_]+", "", raw)
    # 不接受模型自己打印的结构化对象或标识/决策字段，避免把文本伪装成凭据。
    if re.search(r"\{[^{}]*\}", raw) or re.search(r'''["']?(?:risk_level|permission_action|[a-z]+_id|execution_result|tool_results|require_confirmation|require_verification)["']?\s*[:=]''', raw):
        return "UNVERIFIED_FINANCIAL_CLAIM"
    # “成功了/搞定了”省略了操作对象，仍然不能在交易追问中充当完成声明。
    if re.search(r"(?:^|[，,。.!！?？;；\n])\s*(?:(?:好的|没问题)[，,\s]*)?(?:已经|已|都|全部|现已)?(?:完成|成功|办妥|办好|处理好|搞定|通过|放行)(?:了|啦)?(?:$|[，,。.!！?？;；\n])", raw):
        return "UNVERIFIED_FINANCIAL_CLAIM"

    # 实际完成/授权/卡片状态，即使没有金额也不能凭模型文本证实。
    claims = (
        r"(?:已|已经).{0,14}(?:完成|执行|办妥|办好|处理|确认|授权|批准|验证|扣款|到账|冻结|解冻|取消订阅)",
        r"(?:已|已经|现已).{0,14}(?:转给|转到|转出|转入|转款|汇给|汇到|汇出|汇入|汇款|打给|打到|打款)",
        r"(?:钱|资金|款项|款)(?:已|已经)?(?:被)?(?:转给|转到|转出|转入|汇给|汇到|汇出|汇入|打给|打到).{0,10}了",
        r"(?:转账|交易|汇款|付款|支付|操作|办理)(?:已)?(?:成功|完成)(?!后|时|前)",
        r"(?:收款|资金|款项|钱)(?:已经|已)?(?:到账|入账)",
        r"(?:验证|授权|认证|权限)(?:状态(?:为|是)?)?(?:已|是|为)?(?:通过|获批|批准|放行|生效)(?!后|时|前|才)",
        r"(?:已|已经|现已)(?:获得|取得|具备).{0,8}(?:权限|授权|许可)",
        r"(?:银行卡|卡片|信用卡|卡)(?:当前|目前|状态|状态为|处于|已|已经|被)*(?:正常|可用|冻结|解冻|挂失|停用|注销)",
        r"(?:银行卡|卡片|信用卡|卡)(?:已|已经)?被.{0,6}(?:冻结|解冻|挂失|停用|注销)",
        r"(?:银行卡|卡片|信用卡).{0,8}(?:正常使用|状态正常|处于冻结|处于挂失)",
        r"(?:余额|balance)(?:是|为|有|剩余|is|has)?[0-9零一二三四五六七八九十百千万亿两]+",
        r"(?:余额|资金)(?:很|十分|非常)?(?:充足|充裕|足够|不足|充沛)",
        r"(?:账户|账号|帐号)[0-9a-z_-]+.{0,12}(?:余额|资金|状态|明细|记录)",
        r"(?:你的|您的|您当前|你当前|当前|该|这个|这笔|本次).{0,12}(?:账户|余额|资金|账单|交易|卡片).{0,8}(?:是|为|有|剩余|充裕|充足|正常|已|成功)",
        r"(?:这些|本月|你的|您的).{0,8}账单.{0,8}(?:有|显示|共计|合计|支出|消费)",
        r"账单.{0,8}(?:包含|共|有|显示|合计|记录了)[0-9零一二三四五六七八九十百千万亿两]+",
        r"(?:交易|转账|支付|流水|回执)(?:编号|号码|号|凭证)(?:是|为)?[0-9a-z]",
        r"(?:未|没有|尚未)(?:发生)?(?:扣款|到账|入账)",
        r"(?:transfer|payment|transaction)(?:is|was|hasbeen)?(?:successful|completed|executed|confirmed|paid)",
        r"(?:authorization|verification|permission)(?:is|was|hasbeen)?(?:approved|passed|granted|verified)",
        r"your(?:account|balance|funds|card).{0,16}(?:is|has|available|frozen|blocked)",
    )
    # 只豁免分句末尾、宾语明确为交流活动的完成声明；不能凭整段出现“介绍”放行。
    claim_source = re.sub(
        r"(?:已经|已)(?:为你|替你|帮你)?(?:完成|结束|做完)(?:了)?(?:这次|本次|刚才的|上述)?(?:介绍|讲解|说明|解释|回答|问候|总结)(?:了)?(?=$|[，,。.!！?？;；\n])",
        "对话说明", raw,
    )
    definition = re.sub(r"[\W_]+", "", claim_source)
    # “余额是账户可用资金”属于定义；金额、余额状态及用户特指事实仍在下方单独拒绝。
    definition = re.sub(r"(?:余额|资金)(?:是|为)(?:指|账户|银行|一种|用于|用来)", "概念定义", definition)
    # 否定的是“执行声明”本身，不把整段带“不”的文本一概放行。
    definition = re.sub(
        r"(?:不代表|不意味着|不等于|不能据此认为|不能宣称|不会声称)(?:已|已经)?(?:替你|为你|帮你)?(?:执行|完成|转账成功|操作成功)(?:业务|操作|转账)?",
        "语义说明", definition,
    )
    if any(re.search(pattern, definition) for pattern in claims):
        return "UNVERIFIED_FINANCIAL_CLAIM"
    if re.search(r"\b(?:account|card)\s*[#:]?\s*[0-9]+", raw):
        return "UNVERIFIED_FINANCIAL_CLAIM"
    if re.search(r"(?:余额|资金).{0,6}[0-9零一二三四五六七八九十百千万亿两]+(?:元|块|cny|rmb)", text):
        return "UNVERIFIED_FINANCIAL_CLAIM"

    # 不能把“绕过确认/验证”作为产品能力，否定式规则解释可以保留。
    for clause in re.split(r"[，,。.!！?？;；\n]+", raw):
        clause = re.sub(r"[\W_]+", "", clause)
        for policy_bypass in re.finditer(r"(?:无需|不用|不必|不需要|不经(?:过)?|未经(?:过)?|免于|跳过|绕过)(?:用户|强)?(?:确认|验证|授权)", clause):
            prefix = clause[:policy_bypass.start()]
            suffix = clause[policy_bypass.end():]
            negated = re.search(r"(?:不能|不得|不会|不允许|无法)$", prefix)
            prerequisite = re.match(r"(?:前|时)?(?:不能|不得|不会|不可|禁止|不允许)", suffix)
            if not negated and not prerequisite:
                return "UNVERIFIED_FINANCIAL_CLAIM"
    for promise in re.finditer(r"(?:我可以|我能|支持|能够)(?:帮助你|帮你|为你|替你|直接|自动|办理|进行)*(?:银行卡|卡片)?(?:申购|赎回|买基金|冻结|挂失|取消订阅|管理订阅|理财)", text):
        prefix = text[max(0, promise.start() - 3):promise.start()]
        if not prefix.endswith(("不", "未", "无法")):
            return "UNSUPPORTED_CAPABILITY_CLAIM"

    financial = bool(re.search(r"余额|账单|账户|账号|帐号|转账|交易|资金|金额|扣款|到账|消费|支出|花销|开销|银行卡|卡片|信用卡|理财|订阅|权限|授权|强验证", text)) or bool(
        re.search(r"\b(?:balance|bill|account|transfer|payment|transaction|card|subscription|authorization)\b", raw)
    )
    # 无工具文本不提供金额事实。数字风险规则交由可信服务端规则说明，不照抄模型数字。
    if re.search(r"[¥￥$]|[0-9零一二三四五六七八九十百千万亿两]+\s*(?:元|块|cny|rmb)", raw):
        if re.search(r"确认|验证|风险|阈值", text) and not re.search(r"余额|账单|卡片|银行卡|账号|帐号", text):
            return "POLICY_REFERENCE_REQUIRED"
        return "UNVERIFIED_FINANCIAL_CLAIM"
    if not financial:
        return "TEXT_ALLOWED"
    # 无单位的数字也可能是余额、笔数或账户标识；无工具说明不照抄这些数值。
    if re.search(r"[0-9]|[零一二三四五六七八九十百千万亿两]{2,}", text):
        return "FINANCIAL_TEXT_UNVERIFIED"
    explanatory = re.search(
        r"可以|能够|支持|实现|帮助|用于|用来|指|表示|代表|记录|反映|通常|一般|需要|必须|应当|不能|不得|不可|禁止|不会|不支持|不代表|尚未|例如|假设|为了|因为|意味着|只能|才能|才可|取决于|限制|保护|区别|概念|就像|(?:余额|账单|账户|转账|权限|授权|验证|卡片)(?:是|为)|isdefined|means|represents|usedto|can|must|requires|cannot|doesnot", text,
    )
    return "TEXT_ALLOWED" if explanatory else "FINANCIAL_TEXT_UNVERIFIED"
