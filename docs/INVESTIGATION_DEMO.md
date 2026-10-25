# 第四阶段第三批：调查界面与比赛演示

> 历史记录说明：本文保留阶段开发时的测试数量、待验收及未提交状态，不代表v0.4.1的发布结果。当前部署见[根README](../README.md)，本次实测见[发布说明](RELEASE_NOTES_v0.4.1.md)。命令中的`<项目根目录>`须替换为实际解压/克隆目录。

本批只开发React界面、API类型和只读查询状态管理，没有修改后端、风控、确认机制或现有数据库。真实DeepSeek和浏览器人工验收由用户执行；自动化使用模拟fetch、真实React静态渲染及Vite转换，不消耗模型额度。

## 页面与调用链

原Hash路由`#/chat`新增调查指令示例。发送仍走既有`DemoProvider.sendMessage → banking.chat → POST /chat/secure`。响应含`investigation`时，`ResponseContent → InvestigationView`展示结构化报告，避免通用partial文案把覆盖不足描述成所有步骤失败。

报告包含调查ID、范围、重点、独立样本及版本、覆盖、执行状态、规划模式、异常线索、原始消费、支付工具、关联卡及读取时间。可展开实际步骤和模型选择，不展示伪造实时进度。金额/笔数/异常时间差直接使用后端字段，格式化金额不经过浮点累计。覆盖日期只把服务端请求区间与数据集区间的交集转换为含结束日的显示形式，不重新计算消费或异常。

`execution_status`和`coverage_status`独立展示：10月可以“调查步骤已完成”且“数据覆盖不足”。页面明确显示本次样本覆盖2026-10-01至2026-10-20，不把21—31日当成没有异常。

`planner_mode`分别展示模型参与受限规划、确定性降级和无需模型选择。`planning.events`仅表示计划接受/拒绝，`steps`才表示实际工具执行；计划被接受不能替代成功状态。等待、执行中、成功、失败、跳过和未执行有不同文案。缺失字段显示“未提供”，非法账户或证据结构拒绝展示。

所有商户和规则文本通过React文本节点呈现，不执行HTML或把它们作为操作指令。调查组件没有转账、冻结、解冻、挂失或确认按钮，也不把报告ID当作操作凭据。

## 历史恢复与卡片快照

聊天面板顶部“历史调查”自动读取当前账户最近20份报告；新调查返回后会刷新列表，也有手动刷新按钮。

- `GET /accounts/{account_id}/investigations?limit=20&offset=0`
- `GET /accounts/{account_id}/investigations/{investigation_id}`

点击历史报告使用`#/chat?investigation_id=...&investigation_account=...`。刷新页面后再次GET相同快照，不调用chat/model、不重新执行调查。URL账户参数仅用于避免切账户后展示旧链接，不是身份认证或服务端授权。账户切换会清除旧选择，列表和详情即时按scope过滤；读取完成、失败以及卸载均有代际检查，A→B→A也不能接受第一次A的迟到响应。

“重新读取此报告（只读）”可在查询失败后重读，或读取running任务的新快照；它只GET同一ID，不重跑调查。页面不自动轮询。刷新后模拟账户仍遵循原应用默认值，不会依据URL自动切换身份；其他账户需主动切换后从其历史列表打开报告。

列表和详情是组件局部只读状态，没有建立第二套业务任务管理器。不会修改既有pending_action、conversation_id、一次性许可或未知结果锁。已有资金/卡片操作未知时，历史GET仍可查，但不能借此清除安全限制。

卡片部分始终标“调查快照／历史状态”，显示服务端版本和读取时间。冻结版本3会显示“读取时已临时冻结，本次调查未重复申请冻结”。“查询最新卡片状态”仅在用户点击后调用原`GET /accounts/{account_id}/cards/{card_id}`；结果单独展示，不覆盖旧报告，失败也不能把旧状态冒充最新结果。目标、账户和读取时间必须匹配。可前往现有银行卡管理页，原始记录入口保留record_id；后续处置仍走原黄色确认或红色强验证阻断。

## Windows独立演示环境

以下命令由用户手动执行。本批未执行复制、初始化或启动后端，也未改动已有银行卡演示库。建议停止占用8000端口的旧演示后端，再准备新的第四阶段副本。不要重跑样本导入器、重置余额或把已有卡片改成特定状态。

先在项目根目录只读检查已有银行卡演示库：

```powershell
Set-Location '<项目根目录>'
@'
from contextlib import closing
from pathlib import Path
import sqlite3
source = Path('.cache/card-management-demo/banking.db').resolve()
if not source.is_file():
    raise SystemExit('源演示库不存在：停止，不创建或导入样本。')
with closing(sqlite3.connect(source.as_uri() + '?mode=ro', uri=True)) as conn:
    conn.execute('PRAGMA query_only=ON')
    print('只读检查数据库:', source)
    print('完整性:', conn.execute('PRAGMA quick_check').fetchone()[0])
    print('模拟卡片:', conn.execute('SELECT card_id,account_id,display_name,last_four,temporary_frozen,loss_status,version FROM cards ORDER BY account_id,card_id').fetchall())
    print('样本覆盖:', conn.execute('SELECT dataset_id,account_id,version,coverage_start,coverage_end_exclusive FROM analysis_datasets ORDER BY account_id').fetchall())
    print('相似消费:', conn.execute("SELECT record_id,amount_cents,occurred_at,payment_instrument_ref FROM spending_records WHERE account_id=? AND record_id IN (?,?) ORDER BY record_id", ('1001','oct20-a','oct20-b')).fetchall())
'@ | .\.venv\Scripts\python.exe -B -
```

检查实际状态。若生活卡A不是预期冻结版本3，不得通过改库伪造；按实际状态记录差异。

仅首次创建新的`.cache/cross-scenario-demo/banking.db`。SQLite backup从只读源取得一致副本；目标存在时明确失败，绝不覆盖。若失败，保留文件并人工检查，不自动删除或重建。

```powershell
@'
from contextlib import closing
from pathlib import Path
import sqlite3
source = Path('.cache/card-management-demo/banking.db').resolve()
target = Path('.cache/cross-scenario-demo/banking.db').resolve()
if not source.is_file():
    raise SystemExit('源库不存在，停止。')
if target.exists():
    raise SystemExit('第四阶段库已存在：不覆盖，请直接检查并启动该库。')
target.parent.mkdir(parents=True, exist_ok=True)
with target.open('xb'):
    pass
with closing(sqlite3.connect(source.as_uri() + '?mode=ro', uri=True)) as src:
    src.execute('PRAGMA query_only=ON')
    with closing(sqlite3.connect(target.as_uri() + '?mode=rw', uri=True)) as dst:
        src.backup(dst)
print('仅新建独立副本:', target)
'@ | .\.venv\Scripts\python.exe -B -
```

后续重启跳过复制。只读检查新库时，将第一个脚本的source改为`.cache/cross-scenario-demo/banking.db`。不执行任何样本初始化命令。

启动后端，BANK_DB_PATH仅在当前PowerShell进程临时生效；Ctrl+C退出后恢复。单worker，不启用reload：

```powershell
Set-Location '<项目根目录>'
$invDb = (Resolve-Path '.cache\cross-scenario-demo\banking.db' -ErrorAction Stop).Path
$invPreviousDb = $env:BANK_DB_PATH
try {
    $env:BANK_DB_PATH = $invDb
    .\.venv\Scripts\python.exe -B -m uvicorn main:app --host 127.0.0.1 --port 8000
} finally {
    $env:BANK_DB_PATH = $invPreviousDb
}
```

原启动机制只在新副本增加调查元数据表，不导入消费或卡片样本。调查和GET审计会在该副本写元数据；“只读调查”指业务数据不变，不是不写审计。现有.env用于读取已配置的模型连接，不需要编辑或打印Key。

另开PowerShell启动前端，复用已有依赖，不执行npm install：

```powershell
Set-Location '<项目根目录>\frontend'
npm.cmd run dev
```

访问`http://127.0.0.1:5173/#/chat`。默认`/api`代理到本机8000端口。若旧进程占用端口，应先确认并自行停止对应演示进程，不任意结束其他程序。历史查询不需要模型可用，但服务端和新副本必须可用。

## 自动化与人工验收

前端命令（已有依赖）：

```powershell
Set-Location '<项目根目录>\frontend'
npm.cmd test
npm.cmd run build
```

build包括`tsc --noEmit`和Vite生产构建。自动化使用React静态渲染、真实API适配器配模拟fetch和与组件共用的读取代际控制器，不把这些测试宣称为真实浏览器点击验收。

|验收|操作与预期|是否需要真实模型|
|---|---|---|
|A 一句话调查|发送“帮我调查2026年10月有没有可疑消费，特别是重复扣款。”；出现真实调查ID、范围、重点、版本、只读声明|需要，验证真实Agent入口与规划质量|
|B 核对规划及执行|分别展开“模型计划选择”和“实际工具步骤”；检查mode、step_id、依赖、时间、green/allow和审计；接受计划不等于步骤成功|真实规划需模型；展示可离线模拟|
|C 原始证据|核对oct20-a、oct20-b各88.00元、演示咖啡店、2分钟、SIMILAR_PAYMENT_V1及支付工具引用；不宣称已证实盗刷|旧报告展示不需要|
|D 关联卡片|核对明确关联生活卡A、尾号0101、冻结版本和读取时间；点击“查询最新卡片状态”，结果单独显示并有查询审计|不需要|
|E 无自动处置|调查前后在银行卡页刷新核对同一卡状态/版本，未创建黄色任务、操作凭据或新增冻结；已有待确认任务不会被调查替换|调查入口需要模型；零写入性质另有离线后端测试|
|F 刷新恢复|历史列表打开同一ID，保留带ID的地址并刷新；网络请求仅GET历史/详情及普通页面读取，不发chat/confirm；报告带历史快照标记|不需要|
|G 降级|若真实返回deterministic_fallback，展示原因和确定性降级，不能标自主规划成功。未出现则记录“真实降级未触发”；用模拟模型后端用例与前端测试核验，不改Key制造故障|展示可离线；真实降级未必触发|
|H 原功能回归|普通问候；查询余额；9月分析2140.49元；银行卡列表；500元黄色后取消；5000元按实际日累计红色阻断；正式挂失不提供放行按钮|自然语言入口需要模型；页面只读接口不需要|

额外检查：切换到1002后没有1001历史/证据，旧请求完成也不能覆盖新账户；无映射显示未关联；部分失败保留成功证据；中断报告不显示完成；手机宽度可展开步骤。只在新副本进行人工操作，不修改源库或正式库。不要通过断网真实资金操作来测试未知结果。

## 交付范围与限制

新增`InvestigationView.tsx`、`InvestigationHistory.tsx`、`investigationRead.ts`、`Investigation.test.mjs`和本文档。

修改前端`api/types.ts`、`api/banking.ts`、`components/ui.tsx`、`pages/ChatPage.tsx`、`state/DemoProvider.tsx`、`state/latestRead.ts`、`styles.css`、`package.json`。没有新增依赖，package-lock未改。原确认、取消、风险面板及银行卡页面逻辑未改；LatestRead仅增加失效方法供新查询使用。

不支持前端实时流、后台断点续跑、自动重试调查、自动冻结、工作流编辑器或生产身份认证。历史入口目前显示最近20份，未增加复杂筛选。后端恢复仍是上一批的单进程语义。POST通信未知时保留原安全锁，查询历史不能解锁金融操作。历史报告也不会恢复会话或待确认许可。

用户已报告后端539/539通过；本批未修改后端，不将该结果写成Codex重新运行通过。真实DeepSeek、浏览器端到端及移动端视觉表现尚待人工验收。

本批实际验证：新增38项前端测试，完整93/93通过（原55项保留）；严格TypeScript检查及Vite生产构建通过。测试覆盖报告/证据/三种规划模式、计划与实际分离、覆盖不足、失败与中断、历史GET恢复、迟到成功/错误及A→B→A隔离、手动卡片查询、未知结果锁和待确认任务保留。

最终`git diff --check`及新增文件尾随空白检查通过；待交付内容未发现疑似Key/私钥或禁止文件。开发前后SHA256核对：.env、正式database/banking.db、已有账单分析库及银行卡演示库均未变化。前两批全部文件哈希未变，package-lock亦未变；测试日志和构建产物在Git忽略目录内。

Git保持feature/cross-scenario-v0.1，HEAD为9544cbcd19523012fe5d97686169c4a6323e8a2e。当前共29个未提交文件（12个已跟踪修改、17个未跟踪，含前两批16个文件）；暂存区为空。本批没有提交、推送、合并或创建标签。
