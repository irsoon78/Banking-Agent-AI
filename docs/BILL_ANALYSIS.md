# 账单分析：数据核心与Agent/API/前端接入

第一批提供数据与确定性计算核心；第二批复用它接入Agent、只读HTTP接口和前端`/analysis`页面。只支持固定来源`synthetic_demo`，全部为CNY已完成消费样本；不宣称为真实银行消费，也不参与余额核算。退款、撤销、失败及待处理记录一律拒绝导入，不提供净消费或退款分析。

## 与第一阶段的隔离

- 保留`accounts`、`bills`、`audit_logs`及其初始化、转账执行、日累计风险和审计代码不变。
- 消费数据只存在于新增的`analysis_datasets`和`spending_records`，绝不写入`bills`或更新余额。
- 不在服务启动、模块导入、聊天或API读取时自动建分析表、导入样本。
- 普通`database.db.init_db()`只初始化原有业务数据。下方离线命令在新独立库调用它建立演示账户，再导入消费样本。
- 当前服务尚未认证用户。`AnalysisService(..., account_id=...)`要求未来调用层从可信服务端账户上下文注入；方法中没有可切换账户参数，所有查询均过滤该账户。Python参数不是身份认证凭据。

## 数据结构

`analysis_datasets`：`dataset_id`主键、账户外键、版本、受限来源、北京时间覆盖起始日和不包含的结束日、覆盖说明、规范内容SHA256。数据集ID与账户组成唯一键供复合外键使用。

`spending_records`：数据集ID与稳定记录ID组成主键，账户通过复合外键与所属数据集绑定；UTC毫秒时间、正整数分金额、商户ID/名称、分类代码/来源、可空的非敏感支付工具引用、`purchase`类型、`posted`状态、`CNY`币种。索引为账户/数据集/时间/记录ID。

金额在SQLite及Python内为整数分；所有结果中的`amount_cents`和`change_cents`为十进制字符串，`amount`为两位小数展示字符串。比例由Decimal计算，ROUND_HALF_UP保留两位小数；分类占比单独舍入，合计可能相差0.01个百分点。前端不能用浮点数重算账单。

只支持确定性分类`dining/transport/shopping/housing/other/uncategorized`，分类来源为`fixture_v1`。未知分类必须显式标为`uncategorized`或拒绝，不由LLM猜测。支付工具引用如`demo-instrument-wallet-a`，不是卡号，不代表已实现卡片能力。将来增加卡片表后需补充引用归属校验与外键。

同ID、同元数据、同内容的导入幂等；记录顺序不影响SHA256。内容或版本改变必须使用新数据集ID，禁止静默覆盖。重复记录ID、不同账户记录、不一致商户名、无效金额/时间/来源在写入前拒绝。单个数据集的表创建与记录导入使用同一事务，失败回滚；两份固定账户数据集分别提交，不声称整次CLI跨数据集原子提交。

## 明确的离线初始化命令

在项目根目录执行；下方路径为独立、被Git忽略的演示库，不是正式`database/banking.db`：

```powershell
.\.venv\Scripts\python.exe -B -m bill_analysis.sample --database .cache\bill-analysis-demo\banking.db
```

`--database`必须显式提供。命令拒绝默认正式路径、当前进程配置路径和`.env`中的数据库路径；其他已存在的数据库必须带有本命令创建的SQLite application_id，否则拒绝修改。新目标使用排他创建，不能覆盖已有文件。再次执行相同命令只核验并跳过已有样本。不删除或重置损坏的样本库。

初始化临时设置本CLI进程内`BANK_DB_PATH`，建立独立演示账户后恢复原值；不修改`.env`或系统环境变量。只读服务不读取或复制正式库。独立库初始余额为1001账户10000元、1002账户5000元；消费导入不改变这些余额。

## 固定样本及独立预期值

日期固定，不随运行日期漂移。覆盖`[2026-09-01, 2026-10-21)`的北京时间业务日；这是样本声明的覆盖范围，不是从首末交易时间猜出的真实账户完整性。9月完整覆盖，10月只覆盖1日至20日。覆盖日期内无记录表示样本中无消费；未覆盖日期必须为未知。

账户1001数据集`sample-spending-1001-v1`，18条记录：

| 月份 | 笔数 | 已记录金额 | 餐饮 | 交通 | 购物 | 居住 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 2026-09 | 8 | 2140.49 | 117.00 | 5.50 | 217.99 | 1800.00 |
| 2026-10（仅1—20日） | 10 | 2737.01 | 272.00 | 7.00 | 658.01 | 1800.00 |

| 商户ID | 9月金额 | 10月已记录金额 |
| --- | ---: | ---: |
| noodle | 68.50 | 38.00 |
| cafe | 48.50 | 234.00 |
| metro | 5.50 | 7.00 |
| market | 217.99 | 159.01 |
| rent | 1800.00 | 1800.00 |
| electronics | 0.00 | 499.00 |

上述值在`sample.EXPECTED`手工固定，测试独立核对样本计算结果。账户1002使用独立数据集`sample-spending-1002-v1`和另一支付工具引用：9月购物123.45元、10月餐饮6.78元，用于验证账户隔离。

## 只读服务接口

```python
from bill_analysis.service import AnalysisService

service = AnalysisService(
    '.cache/bill-analysis-demo/banking.db',
    account_id='1001',  # 后续由服务端账户上下文提供，不能放入LLM工具参数
)
report = service.analyze_month('sample-spending-1001-v1', '2026-09')
report = service.analyze_range('sample-spending-1001-v1', '2026-10-01', '2026-10-21')
comparison = service.compare_months('sample-spending-1001-v1', '2026-10', through_day=20)
page = service.list_records(
    'sample-spending-1001-v1', '2026-09-01', '2026-10-01',
    limit=20, offset=0, category='dining', merchant_id='noodle',
)
record = service.get_record('sample-spending-1001-v1', 'sep01')
```

- 日期区间为左闭右开，1至366天。时间存UTC，沿用第一阶段现代北京时间固定UTC+08:00（名为Asia/Shanghai）的项目规则，不声称比赛官方指定该时区，不依赖Windows tzdata。
- 报告包含数据集版本及来源、期间本地/UTC边界、覆盖说明、总额与笔数、分类、商户排行、每日趋势及异常记录引用。汇总查询全部匹配记录，与分页明细相互独立。
- 每次读取使用`mode=ro`、`query_only`及显式读取事务。月度对比两期在同一连接快照内完成。缺少数据库或分析表时明确失败，不创建文件、表或样本。
- `coverage.complete`仅表示请求日期完整落入该模拟数据集声明的覆盖范围，不表示真实账户消费完整。未覆盖日趋势金额/笔数为`None`；`summary`始终是已记录样本合计，不能据此把未记录日期解释为零。
- 10月整月对比返回`comparable=False`和`INCOMPLETE_COVERAGE`，变化金额/百分比为空。显式比较两个相邻月份前20天：9月2016.49元、10月2737.01元，增加720.52元，35.73%。上月为零时百分比为空，不返回无穷大。
- 明细返回总匹配笔数、稳定排序的分页结果；`get_record`可追溯异常中引用的原始记录。商户名称仅是数据，后续前端按纯文本展示，不能作为模型指令。
- 参数、数据或查询异常抛出带稳定`code`的`AnalysisError`，不伪造成功或零值，不返回底层SQLite路径或SQL错误。未来API适配层负责转换为HTTP协议及审计。

## 异常规则

只提供需核对提示，不确认欺诈或重复扣款，不执行任何卡片/资金操作，不影响Risk Engine。

- `LARGE_PURCHASE_V1`：单条消费不少于100000分，含记录ID和阈值。1000元是该分析规则阈值，与日累计转账授权无关。
- `SIMILAR_PAYMENT_V1`：所选时间范围内，同一商户同一金额的相邻两条消费相距不超过300秒，返回两条ID。仅在选定范围内检查，不声称检查了区间外记录。
- 不使用均值/标准差等小样本统计推断。一条小额消费不异常；一条大额消费仍可命中明确金额规则。10月预期命中`oct15`大额及`oct20-a/oct20-b`相似支付。

## 回归

新测试为纯同步、临时数据库测试，不依赖FastAPI异步客户端或真实模型。先运行本批测试；完整后端回归需在本机PowerShell执行。以下变量只在当前PowerShell进程临时调整并在结束后恢复，不使用管理员权限或改变系统配置：

本批实际验证：48项新增测试，加26项既有Risk Engine/Permission Controller测试，共74/74通过。显式CLI初始化独立`.cache/bill-analysis-demo/banking.db`后，程序计算与上述月份、分类、商户预期值一致；两个账户仍为初始余额，原账本仅两条初始入账、审计记录为零。未在Codex环境执行完整异步回归，也未进行模型或浏览器验收。若既有228项数量不变，完整后端预期共276项，实际数量以本机运行输出为准。

```powershell
$analysisTemp = Join-Path (Get-Location) '.cache\bill-analysis-test-temp'
New-Item -ItemType Directory -Force -Path $analysisTemp | Out-Null
$previousTemp, $previousTmp, $previousTmpDir = $env:TEMP, $env:TMP, $env:TMPDIR
try {
    $env:TEMP = $analysisTemp
    $env:TMP = $analysisTemp
    $env:TMPDIR = $analysisTemp
    .\.venv\Scripts\python.exe -B -m unittest tests.test_bill_analysis -v
    if ($LASTEXITCODE -ne 0) { throw '账单分析测试失败' }
    .\.venv\Scripts\python.exe -B -m unittest discover -s tests -v
    if ($LASTEXITCODE -ne 0) { throw '完整后端回归失败' }
} finally {
    $env:TEMP = $previousTemp
    $env:TMP = $previousTmp
    $env:TMPDIR = $previousTmpDir
}
Push-Location frontend
try {
    npm test
    if ($LASTEXITCODE -ne 0) { throw '前端测试失败' }
    npm run build
    if ($LASTEXITCODE -ne 0) { throw 'TypeScript或Vite构建失败' }
} finally { Pop-Location }
git diff --check
```

## 第二批接入位置

- `agent/llm_agent.py`：新工具schema、参数验证、账户注入、明确的结果渲染分支；禁止把分析结果落入旧账单渲染分支。
- `agent/dialogue.py`、`agent/prompt.py`、必要时`agent/intents.py`：识别分析查询、澄清日期、声明来源；不继续优化闲聊，不从助手历史推断金融事实。
- `main.py`、`api/responses.py`：共用服务的只读API、结构化响应、错误映射、审计；不得将初始化器暴露为API或模型工具。
- `security/risk_engine.py`及`main.py`中旧接口只读白名单：精确登记新分析操作，不默认放行未知工具；不改资金风险判断。
- `frontend/src/api/{types,banking}.ts`、`state/DemoProvider.tsx`、必要状态测试、`components/ui.tsx`或独立分析组件、`pages`新增页面、`App.tsx`与样式：来源/范围/覆盖说明、统计卡片、明细追溯及旧请求覆盖保护。
- 增加模拟模型/API契约与浏览器验收，重点验证已有pending任务不受分析查询影响，分析请求资金工具调用次数为零。

## 第二批闭环与实际指令

自然语言 → `BankingAgent.run` → 分析意图/日期解析 → 严格参数验证 → `secure_execute_tool` → `PendingActionManager.prepare_action` → Risk Engine green → Permission Controller allow → `execute_tool` → `AnalysisService` → 结构化结果及确定性文字 → 聊天分析卡片。分析完成即返回，不再调用模型解释或把商户文本当下一轮指令。

服务端解析原文中的期间；模型参数通过严格校验后，日期和查询类型仍以原文解析结果为准。本月/上月/最近30天由服务器北京时间解释。明确请求遇到模型正常但未选工具时，可以有限只读兜底；未知工具、无效参数、模型异常不触发兜底。缺少日期时澄清，不生成报告。不声称任意中文都能可靠识别；比较需按时间先后写明相邻月份，缺少年份仅允许从同句前一个明确月份继承。

可用指令：

- `分析一下我2026年10月的消费。`
- `我这个月在哪些方面花钱最多？`
- `查看2026年9月的商户消费排行。`
- `最近有没有值得注意的异常消费？`
- `比较2026年9月和10月前20天的消费。`
- `比较2026年9月和10月的消费。`（覆盖不足，不生成整月增幅）
- `统计2026-10-01至2026-10-20的消费。`（自然语言结束日包含，转换成不含10月21日的范围）
- `查看2026年9月消费明细。`
- `查看消费原始记录 oct20-a。`

只新增四个明确只读操作：`analyze_spending/compare_spending/list_spending_records/get_spending_record`。模型参数不能包含账户、数据集、路径、SQL、金额总数或风险等级。数据集由服务端选择当前账户唯一的`synthetic_demo`版本；多个版本拒绝自动选择/合并。原有默认执行器仍只读，旧`/chat`仍阻断写入，分析不会覆盖会话中待确认任务。

## HTTP接口与前端

```text
GET /accounts/1001/bill-analysis?period=2026-09&source=synthetic_demo
GET /accounts/1001/bill-analysis?period=2026-10&view=merchants
GET /accounts/1001/bill-analysis?period=recent&view=anomalies
GET /accounts/1001/bill-analysis?period=2026-10&mode=compare&through_day=20
GET /accounts/1001/bill-analysis?period=2026-10-01/2026-10-21
GET /accounts/1001/analysis-records?period=2026-09&limit=10&offset=0&category=dining
GET /accounts/1001/analysis-records?record_id=oct20-a
```

API只允许声明的查询字段；来源仅为`synthetic_demo`。period支持YYYY-MM、this_month、last_month、recent或开始日/不包含的结束日。比较时period为较晚月份，through_day省略表示整月。明细limit为1—100，offset为0—100000，日期范围最多366天。账户沿用现有Demo请求上下文，不是生产级身份认证。

成功响应包含`analysis/comparison/spending_records/spending_record`之一，以及`request_id/audit_id/tool/decisions/data_status`。部分统计返回HTTP200及`incomplete_coverage`，不得把部分金额当整月消费；覆盖内空报告为`no_records`。数据集/原始记录不存在为404，参数/日期错误为422，多版本歧义为409，数据/数据库异常为503，不用零值替代失败。进入工具执行的错误保留审计ID；框架在执行前拒绝的非法查询不伪造审计ID。

读取保留request_received、risk_evaluated、permission_decided、tool_execution_started、tool_executed及request_returned事件；审计失败沿用既有阻断/警告策略。商户文本作为数据转义展示，不产生指令或执行凭据。

导航新增“账单分析”。可选本月、上月、最近30天、指定月份或日期范围，以及相邻上月/相同前N天比较。页面展示来源、版本、期间、覆盖说明、已记录金额/笔数、分类、商户排行、每日趋势、异常规则及明细。点击分类/商户/异常记录追溯原始数据；摘要和明细均有审计入口，聊天复用相同组件。

持续标明“模拟消费分析样本，不参与当前账户余额核算。”未覆盖日期为斜纹未知状态。金额只格式化服务端字符串，图形使用服务端计算的占比。筛选变化清空旧报告并启动新读取，旧响应和已卸载组件不能发布结果；明细筛选重新挂载并有请求失效保护。分类、商户占比分别舍入，展示合计可能相差0.01个百分点。

## 独立演示环境与人工验收

在新PowerShell窗口、项目根目录执行。先初始化独立样本库，再为后端进程设置路径；不更改.env：

```powershell
.\.venv\Scripts\python.exe -B -m bill_analysis.sample --database .cache\bill-analysis-demo\banking.db
if ($LASTEXITCODE -ne 0) { throw '样本初始化失败，请核对路径，不要覆盖数据库' }
$previousBankDb = $env:BANK_DB_PATH
try {
    $env:BANK_DB_PATH = (Resolve-Path '.cache\bill-analysis-demo\banking.db').Path
    .\.venv\Scripts\python.exe -B -m uvicorn main:app --host 127.0.0.1 --port 8000
} finally { $env:BANK_DB_PATH = $previousBankDb }
```

另一窗口在frontend目录运行`npm run dev`，打开`http://127.0.0.1:5173/analysis`。不用安装依赖。分析页和GET接口不需要模型联网；聊天使用既有模型配置，本轮没有真实DeepSeek验证。不要在正式库进行转账验收。

验收顺序：9月8笔2140.49元 → 10月已记录10笔2737.01元且覆盖不足 → 两月前20天增加720.52元/35.73% → 整月比较拒绝给出增幅 → 点击商户/异常明细及审计 → 快速切换月份核对旧结果不覆盖。样本日期固定，“本月/最近”超过覆盖区间应明确提示，不能自动补数据。

## 第二批验证边界

Codex环境的asyncio初始化探测在6秒后终止，没有修改业务异步代码，也没有执行完整ASGI回归。新测试包含可执行的同步路由/工具测试、模拟await边界的真实Agent协程控制流测试，以及需本机事件循环运行的HTTP契约。同步Agent测试不代表真实SDK、线程调度或ASGI传输已验证。

本机完整后端仍运行上文unittest discover命令，自动包含新增测试。前端npm test保留原13项，并增加查询/状态测试和真实React渲染组件测试；npm run build执行TypeScript与Vite构建。真实模型、浏览器点击和完整异步回归仍待人工验收。

第二批最终实际结果：新增28项后端测试，其中27项同步测试已执行，通过；1项真实ASGI传输测试留待本机。包含既有策略/分析测试的同步回归166/166通过。完整后端仅收集到304项，没有宣称304项已通过。前端新增10项，总计23/23通过；TypeScript和Vite生产构建通过。Python语法、空白及敏感模式检查通过，正式数据库和.env哈希未变。

本批修改既有文件：README.md、agent/dialogue.py、agent/llm_agent.py、agent/prompt.py、agent/text_policy.py、api/responses.py、main.py、security/risk_engine.py、frontend/package.json、frontend/src/App.tsx、frontend/src/api/banking.ts、frontend/src/api/client.ts、frontend/src/api/types.ts、frontend/src/components/ui.tsx、frontend/src/styles.css。扩展第一批未提交文件bill_analysis/service.py及本文档。新增agent/analysis_intents.py、bill_analysis/tools.py、tests/test_analysis_integration.py、frontend/src/components/AnalysisView.tsx、frontend/src/components/AnalysisView.test.mjs、frontend/src/pages/AnalysisPage.tsx、frontend/src/state/analysisQuery.ts、frontend/src/state/analysisQuery.test.ts。
