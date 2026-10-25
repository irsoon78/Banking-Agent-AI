# Banking-Agent-AI · v0.4.1

基于 Python、FastAPI、SQLite 和 LLM Tool Calling 的模拟银行智能体比赛 Demo。模型负责理解自然语言及受限调查规划，Python 工具提供可信业务数据，服务端独立决定风险、权限和执行条件。前端采用 React + TypeScript + Vite。

当前已实现智能转账、账单智能分析、模拟银行卡管理及跨场景消费调查。**不是实际银行服务，不连接真实资金或银行卡支付网络，也没有生产级登录和持卡人认证。**

- GitHub 仓库：[irsoon78/Banking-Agent-AI](https://github.com/irsoon78/Banking-Agent-AI)（访问需具备仓库权限）。
- 本次交付版本：`v0.4.1`，文档与源码包修订，不新增业务功能；实际本地标签/远端发布结果以Git查询及交付报告为准。
- 保留原稳定标签：`v0.4.0-cross-scenario-mvp` → `6f36a32cb10ef0a1381f8d385ff36678590cce2d`。
- 修订起点：`f4349a8010a30e2baafa54b825e41fd156263fac`（新版README提交）。
- [v0.4.1发布说明](docs/RELEASE_NOTES_v0.4.1.md)：本次实测、包验证及已知限制。
- 当前开发分支：`feature/cross-scenario-v0.1`。
- 版本标识说明：上述为项目稳定标签；`main.py` 的 FastAPI/OpenAPI 版本、前端页眉仍显示 `V0.3.0`，`frontend/package.json` 仍为 `0.1.0`，它们是沿用的组件标识，与整体交付版本分开；本次没有修改组件或业务版本字段。
- [项目正式存档（2026-10-23）](docs/PROJECT_ARCHIVE_2026-10-23.md)保留当时验收及发布状态；后续本地标签或远端状态应以实际 Git 查询为准。

## 1. 当前功能与安全策略

| 场景 | 已实现能力 | 执行边界 |
| --- | --- | --- |
| 自然对话与查询 | 基础交流、能力说明、余额与最近账单查询、账户隔离的有限会话 | 金融事实来自工具；普通文本不能授权交易 |
| 智能转账 | 意图与参数校验、日累计风控、确认/取消、事务记账、审计 | 黄色确认后执行；红色无强验证放行能力 |
| 账单分析 | 分类、商户排行、每日趋势、同期比较、异常提示、原始记录分页追溯 | 独立模拟消费样本，不参与账户余额核算 |
| 银行卡管理 | 列表、详情、状态、消费关联、临时冻结与解除临时冻结 | 临时操作黄色待确认；正式挂失与解挂红色阻断 |
| 跨场景调查 | 异常分析 → 原始记录核验 → 可信卡片关联 → 卡片状态查询 → 报告与历史恢复 | 仅四种业务只读工具，不自动发起处置 |
| 风险与审计 | 服务端风险/权限决策、一次性许可、凭据、请求与步骤追溯 | 模型文本、调查报告ID和客户端按钮均不是授权凭据 |

### 转账按日累计判断，不按单笔金额独立判断

项目采用 `Asia/Shanghai` 北京时间业务日（现代 UTC+08:00），这是项目规则，不宣称由比赛官方指定。计算：

`当前账户当日已提交成功的转出金额 + 本笔拟转账金额`

- 正数累计不超过 **1000.00 元**：`yellow / pending_confirmation`，API 返回 `status: pending`，正式确认前不执行。
- 累计超过 **1000.00 元**：`red / require_verification`，不创建可确认任务，不扣款。
- 零累计时500元为黄色、5000元为红色；已有转出时，以真实账本重新评估，不能拆分绕过。
- 确认时在同一个 `BEGIN IMMEDIATE` 事务内查询完整累计、复核风险/权限并记账。待确认、取消、失败、回滚不计入；已提交资金即使后续响应或审计失败，仍计入累计。
- 金额使用整数分或 Decimal。旧 `/chat` 和独立 Agent 默认执行器继续禁止业务写入。

银行卡临时操作需明确唯一目标，可使用内部 card_id，或服务端在当前账户内严格匹配的完整卡名及尾号。名称/尾号仅用于定位，不是身份认证。卡片任务5分钟有效，确认时使用专用一次性许可，并在事务内复核账户、状态版本及转换条件；解除临时冻结不能解除正式挂失。

## 2. 技术架构与项目结构

```text
浏览器 React / HashRouter
  → FastAPI /chat/secure + 服务端会话
  → BankingAgent 意图识别、严格参数校验
  → RiskEngine → PermissionController
  → 绿色只读 / 黄色待确认 / 红色强验证阻断
  → 可信工具结果、业务凭据、审计

调查专用模式：
  固定账户/日期/重点 → 服务端就绪步骤集合
  → 模型选择合法步骤 → 服务端再次核验依赖与证据
  → 四种只读工具 → 调查快照持久化 → 历史GET恢复
```

```text
Banking-Agent-AI/
├── main.py                 # FastAPI、配置加载、会话与安全API
├── deepseek_client.py      # OpenAI SDK连接DeepSeek的HTTP兼容层
├── agent/                  # 对话、工具参数、意图路由、有界会话
├── security/               # 风险、权限、日累计、确认取消、审计和许可
├── tools/                  # 余额、账单、受保护的模拟转账
├── database/               # SQLite连接、基础表与账户初始化
├── bill_analysis/          # 独立消费数据集、确定性统计、异常规则、离线样本
├── card_management/        # 模拟卡、明确映射、状态版本、事务及操作凭据
├── investigation/          # 调查契约、可信证据、受限规划、执行和存储
├── api/                    # 响应组装与统一错误协议
├── frontend/               # React + TypeScript + Vite页面及前端测试
├── tests/                  # 临时数据库、模拟模型及HTTP回归
├── docs/                   # 各阶段说明与正式存档
├── .env.example            # 空密钥及配置示例
└── requirements.txt        # Python固定依赖；SQLite使用标准库
```

后端使用 FastAPI、Pydantic、SQLite、httpx、python-dotenv 和 OpenAI SDK；后者通过配置的 `base_url` 连接 DeepSeek，不代表使用 OpenAI 模型。前端使用 HashRouter、Context/reducer、fetch 和普通 CSS，无新图表库、通用工作流引擎或额外模型服务。

## 3. 环境与首次配置

已验证运行环境：Windows PowerShell、Python **3.14.7**、Node.js **24.22.0**。requirements.txt的直接依赖要求Python **>=3.10**，其他Python组合未逐一验收。Vite 7要求 Node.js `^20.19.0 || >=22.12.0`，前端测试还需要支持 `--experimental-strip-types`，建议复用已验证的Node 24环境。

以下命令均为操作说明；**已有虚拟环境、依赖和.env时跳过首次准备，不重复安装或覆盖配置。**

首次获取源码可使用以下命令，在存放项目的父目录执行；不要在已有项目目录内再次克隆：

```powershell
git clone --branch v0.4.1 https://github.com/irsoon78/Banking-Agent-AI.git Banking-Agent-AI
Set-Location .\Banking-Agent-AI
```

需远端已发布该标签且当前Git身份可访问仓库；按标签克隆处于分离HEAD，适合运行，不在此说明中切换开发分支。如果远端不可达或标签不存在，停止检查，不重置当前工作区。

从源码包解压得到顶层`Banking-Agent-AI/`；以下`<项目根目录>`须替换为该目录的实际路径（克隆目录同理），不要原样执行占位符：

```powershell
Set-Location '<项目根目录>'
```

在项目根目录可看到main.py、requirements.txt和frontend目录。后端终端进入根目录，前端终端进入其frontend子目录；启动不依赖开发者电脑的路径。

仅首次在项目根目录准备后端：

```powershell
$bankSetupTemp = Join-Path (Get-Location).Path '.cache\setup-temp'
New-Item -ItemType Directory -Force -Path $bankSetupTemp | Out-Null
$bankSetupPrevious = @{}
foreach ($name in @('TEMP', 'TMP', 'TMPDIR')) {
    $bankSetupPrevious[$name] = [Environment]::GetEnvironmentVariable($name, 'Process')
    [Environment]::SetEnvironmentVariable($name, $bankSetupTemp, 'Process')
}
try {
    python -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw '虚拟环境创建失败，保留错误，不覆盖已有环境。' }
    .\.venv\Scripts\python.exe -m pip install -r requirements.txt
    if ($LASTEXITCODE -ne 0) { throw '依赖安装失败，请保留错误信息。' }
} finally {
    foreach ($name in $bankSetupPrevious.Keys) {
        [Environment]::SetEnvironmentVariable($name, $bankSetupPrevious[$name], 'Process')
    }
}
if (-not (Test-Path -LiteralPath '.env')) {
    Copy-Item -LiteralPath '.env.example' -Destination '.env' -ErrorAction Stop
}
```

由用户在本机编辑新建的.env，填写自己的DeepSeek Key。不要在截图、聊天、源码或前端配置中暴露Key。项目读取以下变量：

| 变量 | 当前代码默认值/作用 |
| --- | --- |
| `DEEPSEEK_API_KEY` | 空；未配置时可启动服务，但自然语言聊天不可用 |
| `DEEPSEEK_BASE_URL` | `https://api.deepseek.com/beta`，基础地址，不附加 `/chat/completions` |
| `DEEPSEEK_MODEL` | `deepseek-flash`，模型是否可用取决于服务端及账户配置 |
| `BANK_DB_PATH` | `database/banking.db`；相对路径基于项目根目录 |
| `VITE_API_BASE_URL` | 前端默认 `/api`；通常无需配置，不能填写模型密钥 |

这些模型地址和名称是当前项目默认配置，不是对供应商最新可用模型的保证。现有工具使用strict schema；兼容层调整发送用schema并保留本地验证，不应随意更换接口地址。服务端进程环境变量优先于.env，修改配置后须重启。没有Key时 `/health` 返回 `llm_configured: false`，聊天入口返回 `503 / LLM_NOT_CONFIGURED`；它不是联网凭据校验。

仅首次在前端目录使用现有锁文件安装依赖：

```powershell
Set-Location '<项目根目录>\frontend'
npm.cmd ci
```

数据库、.env、.venv、node_modules、缓存和dist不随Git分发。代码恢复或克隆不会自动恢复运行数据、凭据、会话或待确认任务。

## 4. Windows PowerShell启动：先选择数据库

**普通运行与跨场景演示使用相同代码，区别是服务进程的 `BANK_DB_PATH`。没有独立的“演示模式”开关。每次只选择下面一种后端启动方式，占用8000端口。**

| 用途 | 数据库 | 样本与影响 |
| --- | --- | --- |
| 普通运行 | `database/banking.db`，或用户明确选择的普通库 | 基础账户/转账账本与审计；不会自动导入消费和卡片 |
| 跨场景演示 | `.cache/cross-scenario-demo/banking.db` | 独立消费/银行卡样本及调查报告；不使用普通库 |
| 旧阶段演示 | `.cache/bill-analysis-demo/banking.db`、`.cache/card-management-demo/banking.db` | 保留已有状态，不用作本轮初始化目标 |

服务启动会在所选库建立缺少的基础表、调查元数据表及缺少的Demo账户；不会重置已有余额。新建基础库的1001/1002初始余额分别为10000.00/5000.00元，**现有库余额必须查询，不能沿用初始值**。普通查询通常也会写审计；只读调查仅保证业务数据不变，仍会写审计和快照。

### A. 普通运行后端（明确使用普通库）

普通运行会使用 `database/banking.db`，运行期间业务和审计都落在该库。以下命令会覆盖本次服务进程从.env读取的数据库选择；如果希望避免影响普通数据库，请使用下方“B. 跨场景演示后端”的独立演示数据库启动方式。

在终端A执行：

```powershell
Set-Location '<项目根目录>'
$bankRunDb = Join-Path (Get-Location).Path 'database\banking.db'
$bankPreviousDb = $env:BANK_DB_PATH
try {
    $env:BANK_DB_PATH = $bankRunDb
    Write-Host "本次普通运行数据库：$env:BANK_DB_PATH"
    .\.venv\Scripts\python.exe -B -m uvicorn main:app --host 127.0.0.1 --port 8000
} finally {
    $env:BANK_DB_PATH = $bankPreviousDb
}
```

### B. 跨场景演示后端（使用已有独立库）

在终端A执行。路径不存在即停止；不要去掉检查后让SQLite创建空库，也不要为重现截图重导样本、重置余额或篡改卡片状态。

```powershell
Set-Location '<项目根目录>'
$bankDemoDb = (Resolve-Path -LiteralPath '.cache\cross-scenario-demo\banking.db' -ErrorAction Stop).Path
if (-not (Test-Path -LiteralPath $bankDemoDb -PathType Leaf)) {
    throw '独立演示数据库不存在或不是文件，停止启动。'
}
$bankPreviousDb = $env:BANK_DB_PATH
try {
    $env:BANK_DB_PATH = $bankDemoDb
    Write-Host "本次独立演示数据库：$env:BANK_DB_PATH"
    .\.venv\Scripts\python.exe -B -m uvicorn main:app --host 127.0.0.1 --port 8000
} finally {
    $env:BANK_DB_PATH = $bankPreviousDb
}
```

以上只临时修改当前进程环境，退出后恢复，不修改.env或系统配置。保持**单worker，不使用 `--reload`**；重载会丢失内存会话和待确认任务。

独立库不存在时，不要启动普通库替代比赛演示：

- 若已有银行卡演示库并需要保留其状态，按[独立演示说明](docs/INVESTIGATION_DEMO.md)的只读检查及拒绝覆盖的SQLite backup步骤创建新副本；不能复制正在变化的数据库主文件冒充一致备份。
- 若是全新克隆且没有任何演示库，可由用户明确执行下面的**首次离线初始化**。目标必须尚不存在，不能指向.env/当前进程选中的数据库；不要在服务启动、聊天或普通查询中调用它。

```powershell
Set-Location '<项目根目录>'
if (Test-Path -LiteralPath '.cache\cross-scenario-demo\banking.db') {
    throw '目标已存在，禁止覆盖或重新初始化；请使用已有库。'
}
.\.venv\Scripts\python.exe -B -m card_management.sample --database .cache\cross-scenario-demo\banking.db
if ($LASTEXITCODE -ne 0) { throw '离线初始化失败，保留现场，不删除或重建数据库。' }
```

该CLI会为新独立库创建基础账户、消费样本、卡片及映射。新样本的生活卡A初始为正常状态、版本0；此前人工验收库的“已冻结、版本3”是实际历史状态，不能要求新库凭空具有同样状态。初始化不是挂失/强验证执行，也不会恢复旧调查报告。

### 启动前端与访问页面

另开终端B，使用已有依赖：

```powershell
Set-Location '<项目根目录>\frontend'
npm.cmd run dev
```

- 主页面：[http://127.0.0.1:5173/#/home](http://127.0.0.1:5173/#/home)
- AI对话及历史调查：[http://127.0.0.1:5173/#/chat](http://127.0.0.1:5173/#/chat)
- 账单分析：`http://127.0.0.1:5173/#/analysis`
- 银行卡管理：`http://127.0.0.1:5173/#/cards`
- 风险确认：`http://127.0.0.1:5173/#/risk`；审计：`http://127.0.0.1:5173/#/audit`
- 后端健康检查：[http://127.0.0.1:8000/health](http://127.0.0.1:8000/health)
- Swagger：[http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs)

Vite将 `/api/*` 代理到 `http://127.0.0.1:8000/*` 并去掉 `/api` 前缀。**不要直接双击dist/index.html或把前端URL改为8000端口。**

停止服务：分别在终端B、终端A按 **Ctrl+C**，等待退出和后端finally恢复环境变量；浏览器关页不会停止服务。先等当前请求返回；有待确认任务时先明确处理或记录状态。重启不能用来推断未知写操作未执行，也不能以刷新解除安全限制。

构建预览（不是必需启动步骤）：

```powershell
Set-Location '<项目根目录>\frontend'
npm.cmd run build
npm.cmd run preview
```

预览地址为 `http://127.0.0.1:4173/#/chat`，仍需单独运行后端。生产部署需配置同等 `/api` 反向代理；本项目尚未实现生产部署和跨域认证方案。

## 5. 使用与数据口径

页面可在1001/1002模拟账户间切换；不得把可选择account_id当作真实身份认证。推荐先在独立演示环境尝试只读指令：

- “你能做什么？”
- “查询账户1001的当前余额。”
- “帮我查询一下我名下有哪些银行卡，以及它们目前的状态。”
- “分析一下我2026年10月的消费。”
- “比较2026年9月和10月前20天的消费。”
- “帮我调查2026年10月有没有可疑消费，特别是重复扣款。”
- “检查2026年10月的大额消费，并看看关联的是哪些银行卡。”

自然语言请求统一使用 `POST /chat/secure`。服务器返回 `conversation_id`，后续消息回传同一编号。模型/API可用性、表述和数据是否存在仍影响结果，不保证任意自然语言均能识别。

待确认转账和卡片操作必须在风险面板点击正式“确认执行”或“取消”。普通“好的”“确认了”不能执行；确认成功分别展示真实 `transfer_id` 或 `card_operation` 凭据。卡片确认/取消须携带原账户、会话和action_id，不能修改原参数。红色 `require_verification` 没有放行按钮，不能通过 `/confirm` 绕过。

### 消费样本与异常规则

**模拟消费分析样本，不参与当前账户余额核算。** 不将银行转账自动归类为餐饮/购物，不混合来源生成总消费。统计使用完整匹配记录，分页仅影响明细，金额由Python确定性计算。

账户1001的固定样本核对值（不是全部真实消费）：

| 口径 | 笔数/金额 |
| --- | --- |
| 2026年9月 | 8笔，2140.49元 |
| 2026年10月1—20日 | 10笔，2737.01元 |
| 两月前20天同期比较 | 2016.49元 → 2737.01元；增加720.52元，35.73% |

时间使用UTC存储，按北京时间解释；范围内部左闭右开。10月样本覆盖截至20日，未覆盖日期不能当作零消费，不能生成“10月完整消费”结论。

- `LARGE_PURCHASE_V1`：单条消费≥1000元，提示核对用途；与转账授权阈值是不同规则。
- `SIMILAR_PAYMENT_V1`：同商户、同金额的相邻消费间隔≤300秒，提示核对。
- `oct20-a`、`oct20-b`各88.00元、相隔2分钟，可信映射关联模拟生活卡A、尾号0101；卡片当前状态必须查询，不能硬编码。
- **规则命中不代表已确认盗刷或重复扣款。** 退款/撤销等类型暂不支持，商户、分类和映射来自固定数据，不由LLM补造。

### 调查规划、证据与恢复

调查只允许 `analyze_spending`、`get_spending_record`、`get_card_for_spending_record`、`get_card`。记录ID来自本次真实异常，卡片ID来自已核验映射，模型只能选择服务端提供的就绪step_id，不能提交SQL、任意ID、风险或写操作。最多6条原始记录、20次业务只读工具尝试；模型选择另有20次预算。

| 字段 | 含义 |
| --- | --- |
| `planner_mode` | `model_planned`：真实合法模型选择；`deterministic_fallback`：失败后确定性降级；`deterministic_no_choice`：没有需要模型选择的分支 |
| `planning.events` | 模型计划选择记录，接受计划不代表步骤成功 |
| `steps` | 实际执行的依赖、工具、状态、证据和审计 |
| `execution_status` | 执行状态，与数据是否完整独立 |
| `coverage_status` | 数据覆盖状态；可以执行completed而覆盖incomplete |
| `investigation_id` | 已保存报告标识，不是金融操作授权 |

聊天页“历史调查”读取当前账户最近20份报告。打开带调查ID的历史链接后刷新，只查询已保存快照，不调用模型或重新调查；明确的新调查请求才生成新ID。旧卡片状态是带读取时间的历史快照，“查询最新卡片状态”独立查询并单独显示。调查不会自动冻结或创建待确认任务，历史恢复也不恢复许可。

## 6. 主要API

所有业务结果以结构化状态和真实工具结果为准，不能把HTTP 200、普通对话completed或模型文案当作金融成功。

| 方法与路径 | 用途 |
| --- | --- |
| `GET /health` | 服务及LLM是否配置，不调用模型 |
| `POST /chat/secure` | 统一自然语言、受限调查及安全任务入口 |
| `POST /confirm`、`POST /cancel` | 处理已有任务，确认/取消不调用LLM |
| `GET /accounts/{account_id}/balance`、`/bills` | 余额、最近1—100条账单 |
| `GET /accounts/{account_id}/bill-analysis` | 消费摘要及同期比较 |
| `GET /accounts/{account_id}/analysis-records` | 原始明细分页，或按record_id查询 |
| `GET /accounts/{account_id}/cards`、`/cards/{card_id}` | 模拟卡片列表、详情 |
| `GET /accounts/{account_id}/analysis-records/{record_id}/card` | 可信消费关联卡片 |
| `GET /accounts/{account_id}/card-operations/{action_id}` | 只读核对卡片操作持久化凭据 |
| `GET /accounts/{account_id}/investigations` | 已保存调查列表，不重跑 |
| `GET /accounts/{account_id}/investigations/{investigation_id}` | 已保存调查详情，不重新读取业务证据 |
| `GET /audit/{account_id}` | 按request_id筛选审计，支持limit/offset |
| `POST /chat` | 无会话的旧只读/普通对话兼容入口，禁止业务写入 |

除了自然语言聊天外，上述GET及确认/取消不要求模型调用；无样本时对应数据接口明确失败，不自动填充数据。参数范围见Swagger及各阶段文档。`audit_id`是请求审计链编号，单条审计事件另有id；确认流程还通过action_id关联。

`needs_input`用于真实澄清，不等于一切“模型未调用工具”。`execution_unknown`或写请求通信中断时，不自动重发，应核对账单、卡片操作凭据及审计；`audit_warning`不能推翻已提交结果。`SESSION_NOT_FOUND`须在安全处理原任务后开始新对话，不能自动重试原业务。

## 7. 测试与验证记录

以下为 **v0.4.1于2026-10-25的开发仓库实测**；源码包解压验证单独记录于[发布说明](docs/RELEASE_NOTES_v0.4.1.md)，不混用两个环境的结果：

| 检查 | 已记录结果 |
| --- | --- |
| 后端完整unittest | 539通过，0失败、0错误、0跳过；60.220秒 |
| 前端完整测试 | 93通过，0失败、0取消、0跳过；851.4951毫秒 |
| TypeScript `tsc --noEmit` | 通过 |
| Vite生产构建 | 通过 |
| `git diff --check` | 本次文档及提交前检查通过 |

后端使用独立临时SQLite和模拟模型/SDK，前端使用模拟fetch、React静态渲染与共享状态控制器。它们不等同于真实模型或浏览器端到端验收。用户另行完成真实DeepSeek规划（4次PLAN_ACCEPTED）、异常证据、卡片关联、覆盖不足、历史恢复及无自动处置的浏览器验收。**此前真实模型验收属于历史证据，本次不重复调用真实DeepSeek。2026-10-23存档测试是历史记录，不能替代本次发布或解压包测试。功能回归及静态扫描不是第三方安全审计。**

完整后端回归：在单独终端执行，临时变量只作用于进程并在finally恢复。新建独立测试目录；不使用演示库测试写操作。

```powershell
Set-Location '<项目根目录>'
$bankTestRoot = Join-Path (Get-Location).Path ('.cache\readme-regression-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $bankTestRoot -ErrorAction Stop | Out-Null
$bankTestPrevious = @{}
foreach ($name in @('TEMP', 'TMP', 'TMPDIR', 'BANK_DB_PATH', 'DEEPSEEK_API_KEY')) {
    $bankTestPrevious[$name] = [Environment]::GetEnvironmentVariable($name, 'Process')
}
try {
    $env:TEMP = $bankTestRoot
    $env:TMP = $bankTestRoot
    $env:TMPDIR = $bankTestRoot
    $env:BANK_DB_PATH = Join-Path $bankTestRoot 'fallback-test.db'
    $env:DEEPSEEK_API_KEY = 'offline-test-placeholder'
    .\.venv\Scripts\python.exe -B -m unittest discover -s tests -v
    if ($LASTEXITCODE -ne 0) { throw '后端回归未通过，请保留错误信息。' }
} finally {
    foreach ($name in $bankTestPrevious.Keys) {
        [Environment]::SetEnvironmentVariable($name, $bankTestPrevious[$name], 'Process')
    }
}
```

前端回归及生产构建：

```powershell
Set-Location '<项目根目录>\frontend'
npm.cmd test
if ($LASTEXITCODE -ne 0) { throw '前端测试未通过。' }
.\node_modules\.bin\tsc.cmd --noEmit
if ($LASTEXITCODE -ne 0) { throw 'TypeScript检查未通过。' }
npm.cmd run build
if ($LASTEXITCODE -ne 0) { throw '生产构建未通过。' }
```

`npm.cmd run build`本身也执行TypeScript检查。测试日志、临时目录和构建产物不进入Git，不删除失败现场或削弱测试断言。

## 8. 常见启动问题

| 现象 | 检查与处理 |
| --- | --- |
| 找不到 `main`、`requirements.txt` 或.venv | 后端必须位于项目根目录；前端命令位于frontend目录，按上文先Set-Location |
| PowerShell阻止 `npm.ps1` 或激活脚本 | 使用 `npm.cmd` 和 `.\.venv\Scripts\python.exe`，不需要激活虚拟环境或修改系统执行策略 |
| node/npm不存在或不支持strip-types | 检查 `node --version`、`npm.cmd --version` 与PATH；复用满足要求的环境，不用改依赖锁文件解决 |
| 8000或5173被占用 | 在旧服务终端Ctrl+C停止已确认的本项目服务；可用 `Get-NetTCPConnection -State Listen -LocalPort 8000,5173` 查询，不能任意结束未知进程 |
| 前端提示无法连接、`/api` 502或返回非JSON | 确认后端8000和前端5173都在运行，检查两个终端日志与Vite代理；不要将API base改成未经配置CORS的跨域地址 |
| 自然语言503或模型502/504 | 先看 `/health` 和错误码；检查服务端Key/模型/基础地址及网络，重启加载配置；不打印Key、不反复重发写请求 |
| 普通库没有消费、卡片或数据集 | 普通启动只建基础库。核对启动日志中的BANK_DB_PATH，使用正确的独立演示库，不向普通库自动导入 |
| SQLite无法打开、权限或锁错误 | 确认显式路径、父目录权限、磁盘及旧进程；演示路径必须存在，停止并保留错误，不管理员绕过、不删库、不改余额 |
| 测试临时目录权限失败 | 用上文独立.cache测试目录及进程级TEMP/TMP/TMPDIR，确认可写；不能用业务库代替临时库 |
| 新虚拟环境在ensurepip阶段失败 | 本次解压验证曾遇到此问题；部署准备使用上文进程级独立临时目录。只对本次新建的未完成虚拟环境可在同样临时目录下执行`.\.venv\Scripts\python.exe -B -m ensurepip --upgrade`，失败则停止，不覆盖开发虚拟环境或改系统权限 |
| 会话丢失、任务过期或卡片版本冲突 | 核对服务是否重启、空闲是否超过20分钟、卡片任务是否超过5分钟；查询状态后重新发起，不自动确认或重发 |
| 10月整月显示覆盖不足 | 正常样本边界：仅有1—20日；不能补造数据或将21—31日当零消费 |
| 历史报告不能恢复 | 检查是否选中同一数据库和账户；快照保存失败或库被换掉时不能保证恢复，不把GET改为隐式重新调查 |

## 9. 已知限制

- 模拟账户选择与会话隔离不是登录/持卡人认证；无短信、人脸、U盾等强验证放行，正式挂失/解挂不执行。
- 自然语言、上下文解释和文本安全规则仍可能误判；不是通用聊天机器人。实际金融数据、风险和授权必须由服务端工具与状态提供。
- 卡片冻结只改变模拟状态，不阻断真实支付；异常命中仅是调查线索，不是欺诈结论。
- 消费仅覆盖明确模拟样本；退款、撤销、理财、订阅/代扣管理尚未实现。
- 会话最多128个、空闲20分钟、最近6轮；会话和待确认任务在进程内存中，重启丢失。页面切换保留当前任务，刷新不会恢复其授权。
- 调查快照可持久化恢复，但没有后台断点续跑、多worker/多实例恢复、分布式任务租约或生产级保留策略；中断不伪装完成。
- 不支持自动金融处置、任意自由工具编排、SSE/WebSocket或生产级身份/部署体系。调查历史与恢复不解除未知写结果限制。

## 10. 进一步阅读

- [正式项目存档](docs/PROJECT_ARCHIVE_2026-10-23.md)：四阶段成果、实际测试与人工验收。
- [账单分析](docs/BILL_ANALYSIS.md)：独立样本、统计口径、API与覆盖边界。
- [银行卡管理](docs/CARD_MANAGEMENT.md)、[银行卡前端验收](docs/CARD_FRONTEND_ACCEPTANCE.md)：状态、许可、确认及操作凭据。
- [调查核心](docs/INVESTIGATION_CORE.md)、[受限模型规划与API](docs/INVESTIGATION_AGENT.md)、[调查演示与恢复](docs/INVESTIGATION_DEMO.md)。
- [开发规则](AGENTS.md)：文件安全与Git约束；其中早期V0.2分支说明是历史约定，当前分支以实际Git及用户任务要求为准。

阶段文档可能保留开发时“待验收”“未提交”描述，应结合存档和当前代码阅读，不能把历史测试记录当作当前新增验证。
