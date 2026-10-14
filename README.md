# Banking Agent AI

一个基于 **Python + FastAPI + SQLite + LLM Tool Calling** 的银行智能体 Demo。
LLM 使用 **DeepSeek 官方兼容接口**，Python 调用仍使用 **OpenAI SDK**。
支持自然语言查询余额、查询最近账单和模拟转账。没有自定义前端、登录权限系统、MCP 或真实银行接入。

## 1. 项目结构与文件职责

```text
Banking-Agent-AI/
├── main.py                    # FastAPI、配置加载、启动初始化、HTTP 接口
├── deepseek_client.py         # DeepSeek HTTP 协议兼容配置，保留本地校验
├── agent/
│   ├── __init__.py             # Python 包标记
│   ├── llm_agent.py            # 工具描述、参数校验、调用循环、工具结果回复
│   └── prompt.py               # 系统提示词：功能范围与工具使用规则
├── tools/
│   ├── __init__.py             # Python 包标记
│   ├── account.py              # get_balance：独立查询账户余额
│   ├── transfer.py             # transfer_money：事务内模拟转账及双边记账
│   └── bill.py                 # get_bills：独立查询最近账单
├── database/
│   ├── __init__.py             # Python 包标记
│   ├── db.py                   # 连接管理、建表、演示数据、金额格式化
│   └── banking.db              # 首次启动自动生成，不包含在源码压缩包中
├── security/
│   ├── __init__.py             # 导出风险、权限和确认管理器
│   ├── risk_engine.py          # 独立于 LLM 的确定性风险分类
│   ├── permission.py           # 将风险等级映射为执行权限策略
│   └── confirmation.py         # 待确认任务保存、确认、取消与权限调度
├── tests/
│   ├── __init__.py             # Python 包标记
│   ├── test_banking.py         # 临时数据库、事务与模拟 LLM/SDK 测试
│   ├── test_risk_engine.py     # 风险等级、阈值、无效输入与扩展操作测试
│   ├── test_permission.py      # 权限策略及 Risk Engine 组合测试
│   └── test_confirmation.py    # 确认流程、并发和 HTTP/SDK/SQLite 测试
├── .env.example               # 环境变量示例
├── .gitignore                 # 忽略密钥、虚拟环境、缓存和数据库
├── AGENTS.md                  # 稳定基线、V0.2 分支与提交规则
├── requirements.txt           # 运行及测试依赖；SQLite 使用 Python 标准库
└── README.md                  # 本文档
```

## 2. 运行环境与安装

需要 Python **3.10 或更高版本**。在终端进入 `Banking-Agent-AI` 目录。直接依赖固定为本次验证的版本。

Windows PowerShell：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
```

macOS / Linux：

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
cp .env.example .env
```

在 [DeepSeek 官方平台](https://platform.deepseek.com/) 创建 API Key，然后编辑 `.env`：

```dotenv
DEEPSEEK_API_KEY=你的DeepSeekAPIKey
DEEPSEEK_BASE_URL=https://api.deepseek.com/beta
DEEPSEEK_MODEL=deepseek-flash
BANK_DB_PATH=database/banking.db
```

仍使用 `from openai import AsyncOpenAI` 和 `client.chat.completions.create(...)`，通过 `base_url` 连接 DeepSeek 官方 OpenAI 兼容接口，不需要安装 DeepSeek SDK。

| 环境变量 | 用途 | 默认值 |
| --- | --- | --- |
| `DEEPSEEK_API_KEY` | DeepSeek 官方 API Key，不能使用 OpenAI Key | 空；自然语言接口不可用 |
| `DEEPSEEK_BASE_URL` | SDK 连接地址，填写基础地址而非完整 `/chat/completions` URL | `https://api.deepseek.com/beta` |
| `DEEPSEEK_MODEL` | 支持工具调用的 DeepSeek 模型 | `deepseek-flash` |
| `BANK_DB_PATH` | SQLite 路径，相对路径基于项目目录 | `database/banking.db` |

默认模型名采用当前官方文档中的 `deepseek-flash`。DeepSeek 标准兼容地址为 `https://api.deepseek.com`；本项目原有工具使用 `strict=True`，因此配置为官方要求的 **`https://api.deepseek.com/beta`**。请保留这个 Beta 地址，模型是否可用取决于你的 DeepSeek 账户。

连接层使用非思考模式，并在发送给 DeepSeek 的 schema 副本中移除其不支持的字符串长度关键字；原有 Python 长度校验保持生效。DeepSeek 请求不发送官方接口未定义的 `parallel_tool_calls` 参数，Agent 原有的单工具调用检查仍执行。

原有 `.env` 若使用 `OPENAI_API_KEY` / `OPENAI_MODEL`，请将其改为上述 DeepSeek 变量；项目不再从这两个旧变量读取 Key 或模型。
已存在的系统环境变量优先于 `.env`。修改配置后需要重启服务。

Windows 启动：

```powershell
.\.venv\Scripts\python.exe -m uvicorn main:app --reload --host 127.0.0.1 --port 8000
```

macOS / Linux 启动：

```bash
.venv/bin/python -m uvicorn main:app --reload --host 127.0.0.1 --port 8000
```

访问 [Swagger 接口文档](http://127.0.0.1:8000/docs)，可以直接通过 `POST /chat` 的 **Try it out** 测试。
不填写 Key 也可以启动服务并调用健康检查、直连余额与账单接口；自然语言 `/chat` 会返回 503 配置提示。

## 3. 演示账户

首次初始化数据库生成以下数据，均为本地模拟数据：

| 账户编号 | 户名 | 初始余额 |
| --- | --- | --- |
| 1001 | 张三 | 10000.00 元 |
| 1002 | 李四 | 5000.00 元 |

每个账户有一条对应的演示初始入账记录。重启不会重置余额或重复添加初始记录。
所有金额单位为人民币，内部存储为整数“分”，JSON 展示金额为两位小数字符串。
账单时间为 UTC，格式中的 `Z` 表示 UTC。

## 4. 自然语言调用

`POST /chat` 请求：

```json
{"account_id": "1001", "message": "查询我的余额"}
```

`account_id` 可省略，默认 `1001`。它由 API 传给工具，模型无法通过工具参数更换当前账户。
本 Demo 没有登录，调用者可以在请求中选择演示账户。

其他可直接使用的完整请求：

```json
{"account_id": "1001", "message": "查看我最近5条账单"}
```

```json
{"account_id": "1001", "message": "请给账户1002模拟转账100.50元"}
```

PowerShell 示例（确保服务已经启动）：

```powershell
$body = @{ account_id = '1001'; message = '查询我的余额' } | ConvertTo-Json
Invoke-RestMethod -Uri 'http://127.0.0.1:8000/chat' -Method Post -ContentType 'application/json; charset=utf-8' -Body ([System.Text.Encoding]::UTF8.GetBytes($body))
```

余额查询成功时返回以下结构；金额以当前数据库为准，此处展示首次启动的数据：

```json
{
  "status": "completed",
  "reply": "账户 1001（张三）余额：10000.00 元。",
  "tool_results": [
    {
      "tool": "get_balance",
      "result": {
        "ok": true,
        "account_id": "1001",
        "owner": "张三",
        "balance": "10000.00",
        "balance_cents": 1000000,
        "currency": "CNY"
      }
    }
  ]
}
```

模拟转账成功后，两边余额及两条关联同一 `transfer_id` 的账单同时提交。
工具返回 `simulated: true`、交易编号、金额、账户编号及转出账户余额。
若从初始数据执行上面的 `100.50` 元转账，余额将变为 `9899.50` 元和 `5100.50` 元。

每次 `/chat` 独立处理，不保存多轮聊天上下文。缺少收款账户编号或金额时，请重新发送完整指令。
每条请求支持一笔转账；转账完成后立即返回，不继续执行该请求中的后续查询。需要查看最新账单时另发请求。
同一转账指令通过新的 HTTP 请求再次发送，会产生另一笔模拟转账。本 Demo 未实现跨请求幂等。

## 5. 接口一览与错误

| 接口 | 用途 | 需要 LLM Key |
| --- | --- | --- |
| `GET /health` | 服务状态和 LLM 是否已配置 | 否 |
| `POST /chat` | 自然语言工具调用入口 | 是 |
| `POST /chat/secure` | 自然语言风险、权限及黄色确认入口 | 是 |
| `POST /confirm` | 确认指定账户的待执行任务 | 否 |
| `POST /cancel` | 取消指定账户的待执行任务 | 否 |
| `GET /accounts/1001/balance` | 直接调用余额工具，便于验证 | 否 |
| `GET /accounts/1001/bills?limit=10` | 直接调用账单工具，支持 1–100 条 | 否 |

`/chat` 返回的 `status`：

- `completed`：请求的工具执行结束。
- `needs_input`：模型没有选择工具；返回固定的信息补全提示，不采用模型自行生成的金融描述。
- `failed`：工具参数、账务校验或数据库操作失败，具体错误见 `tool_results` 或 `reply`。
- `partial`：已有查询结果，但后续 LLM 失败或调用达到上限，返回已经完成的工具结果。

HTTP 422 表示请求参数不合法；缺少 Key 返回 503；LLM 连接/API 错误返回 502，超时返回 504。
业务工具失败通过 HTTP 200 中的 `status: failed` 和 `result.ok: false` 表达，不能仅根据 HTTP 200 判断转账成功。
直接查询不存在的账户返回 404，数据库错误返回 503。

转账工具检查金额格式、正数、账户存在、同账户转账、余额不足和收款余额上限。
它使用参数化 SQL 和 `BEGIN IMMEDIATE`；扣款、收款或账单写入失败会回滚整个事务。
模型每次只允许一个工具调用；参数仍由 Python 校验，未知工具不会执行。

## 6. Agent 的实现路径

```text
自然语言请求 → LLM 根据工具描述返回函数名和 JSON 参数
            → Python 校验参数并调用独立工具
            → SQLite 读取或事务写入
            → 查询结果回传给 LLM（可继续选择查询工具）
            → 后端依据工具结果生成 reply 和 tool_results
```

查询最多进行 6 轮模型调用。金融回复使用 Python 模板，模型返回的自由文本不会成为 API 的金融事实。
转账是终止工具：提交成功后直接返回工具交易凭据，避免提交后还依赖一次模型调用，也避免本轮重复扣款。
系统提示词指导模型如何理解指令；是否正确理解各种复杂语句仍需要实际模型验证。本 Demo 适合上述明确指令。
没有规则匹配器替代 LLM；测试中的模拟模型仅用于离线验证，不参与正常运行。

兼容接口和模型配置参考 [DeepSeek 官方首次调用说明](https://api-docs.deepseek.com/)；strict 模式地址及 schema 限制参考 [DeepSeek 官方 Tool Calls 文档](https://api-docs.deepseek.com/zh-cn/guides/tool_calls/)。
本次适配没有修改 `agent/`、`tools/` 或 `database/` 中的实现。

## 7. 测试

Windows：

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

macOS / Linux：

```bash
.venv/bin/python -m unittest discover -s tests -v
```

测试使用独立临时数据库，不修改演示数据库。覆盖余额和账单、双边转账、金额精度、非法输入、余额不足、并发扣款、事务回滚、启动重复初始化、API 参数与缺少配置、SDK 工具回传、丢弃模型编造文本、调用上限和网络错误。
测试使用 `httpx.MockTransport` 模拟 LLM API，不访问网络或消耗额度；真实自然语言理解需配置 Key 后通过 `/chat` 验证。

测试还验证 DeepSeek 环境变量与默认配置、官方地址和认证头、strict schema 兼容处理，以及 HTTP `/chat` → OpenAI SDK → DeepSeek 模拟响应 → 工具 → SQLite 的查询和转账流程。
DeepSeek 适配阶段在 Windows / Python 3.12.14 上运行 **19 个测试，全部通过**。未使用真实 DeepSeek API Key 验证远程服务或模型理解。
另已实际启动 Uvicorn，验证健康检查、余额、账单、OpenAPI 文档和缺少 Key 时的 HTTP 响应，并在验证后停止服务。

## 8. 数据库初始化与重新演示

服务启动时自动建表。也可以独立执行：

```powershell
.\.venv\Scripts\python.exe -m database.db
```

要重新从初始数据演示，停止服务后将 `.env` 中 `BANK_DB_PATH` 改为新文件，例如 `database/demo2.db`，再启动服务。
旧数据库的余额和账单会保留。

## 9. 稳定版本与 Git 开发流程

当前稳定版本为 **V0.1.2**，状态为“首次 Agent 闭环成功”。项目原先没有 Git 仓库，现已将当前源码保存为 `main` 初始提交，并创建标签 `v0.1.2`。

`main` 和 `v0.1.2` 保留稳定基线；后续所有 V0.2 修改均在 **`feature/security-v0.2`** 分支完成。不要在 `main` 上继续提交或重写历史，也不要移动稳定标签。

修改前先检查：

```bash
git status --short --branch
git diff
git diff --cached
```

每完成一个明确功能，检查暂存文件与 diff、排除密钥和运行文件、运行相关测试，然后单独 commit。提交格式为 `feat: 描述`、`fix: 描述`、`refactor: 描述`。

`.env`、`.venv`、数据库和缓存已配置为忽略；`.env.example` 仅允许空值或占位符。忽略规则不能防止源码中出现 API Key，因此每次提交仍须检查暂存内容。

如果修改导致已有 V0.1 功能失败，立即停止后续开发并报告问题。完整规则见 [AGENTS.md](AGENTS.md)。Git 基线建立阶段仅设置版本与开发流程，未添加 V0.2 业务功能。

## 10. V0.2.1 Risk Engine 基础模块

Risk Engine 只负责根据操作和人民币金额返回 `risk_level`、`reason`，不依赖 LLM、FastAPI 或 SQLite，不执行资金操作。

```python
from security import RiskEngine

engine = RiskEngine()
result = engine.evaluate(operation="transfer", amount=500)
# {"risk_level": "yellow", "reason": "小额转账需要用户确认"}
```

| 操作 | 风险等级 | 后续执行策略 |
| --- | --- | --- |
| `get_balance`、`get_bills`、`query` 等明确支持的查询 | `green` | 自动执行 |
| `transfer` / `transfer_money`，金额为正数且 ≤ 1000 元 | `yellow` | 用户确认 |
| 转账金额 > 1000 元 | `red` | 强验证 |
| `high_risk`，以及预留的理财、卡片管理、订阅管理操作 | `red` | 强验证 |
| 未知操作、转账金额缺失或无效 | `red` | 保守分类，不能因此跳过业务校验 |

支持整数、浮点数、十进制字符串和 `Decimal` 金额。使用 `Decimal` 比较阈值，不进行舍入；拒绝布尔值、非数值、非有限数和非正数。风险分类不替代转账工具现有的金额精度、账户和余额校验。
未来新增操作时，可扩展操作集合和专用评估方法；未知操作默认不会按低风险执行。

未来接入时的流程为：用户请求 → Agent 识别工具和参数 → Risk Engine 根据实际待执行操作判断风险 → 执行策略处理自动执行、确认或强验证 → 工具执行。风险等级由 Python 规则确定，不能由模型自行选择。

**V0.2.1 尚未将 Risk Engine 接入 Agent 或工具执行入口，也未实现用户确认和强验证。现有 `/chat` 的模拟转账行为保持不变。** 本模块已在 `feature/security-v0.2` 提交，commit 为 `78c9f7d`。

仅运行风险模块测试：

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_risk_engine -v
```

V0.2.1 基础模块已在 Windows / Python 3.14.7 下验证：新增 13 个风险测试与已有 19 个银行功能测试，共 **32 个测试全部通过**。既有业务代码未修改。

## 11. V0.2.2 Permission System

`PermissionController.decide(operation, risk_level)` 消费服务端 Risk Engine 的风险结果，返回执行策略。它不依赖 LLM、FastAPI 或 SQLite，不重新判断金额，也不执行银行工具。

| 风险等级 | action | require_confirmation | require_verification |
| --- | --- | --- | --- |
| `green` | `allow` | `false` | `false` |
| `yellow` | `pending_confirmation` | `true` | `false` |
| `red` | `require_verification` | `false` | `true` |

空白或非法操作参数、未知或非法风险等级保守返回 `require_verification`。输入无效不会因后续验证而自动成为有效业务请求，调用方仍须校验操作和参数。

组合调用示例：

```python
from security import PermissionController, RiskEngine

operation = "transfer"
risk = RiskEngine().evaluate(operation=operation, amount=500)
decision = PermissionController().decide(operation, risk["risk_level"])
# {"action": "pending_confirmation",
#  "require_confirmation": True, "require_verification": False}
```

调用方必须使用 Risk Engine 根据实际操作计算出的风险等级，不能接受 LLM 或用户自行声明的 `risk_level`。当前 Permission Controller 校验 operation 是否为非空字符串，已知操作识别及未知操作的保守分类由 Risk Engine 负责。

安全模块的组合流程是：Agent 识别意图和工具参数 → Risk Engine 判断风险 → Permission Controller 返回执行策略 → 后续执行层处理策略。执行层只有收到 `allow` 才能直接调用工具；黄色和红色策略均不能直接执行。

本阶段只实现权限决策模块及组合测试，**未接入现有 Agent 执行入口，`/chat` 的行为保持 V0.1.2 原样**。尚未实现确认后的恢复执行或真实身份验证。后续可由执行层接入人脸、短信验证码、U 盾等验证方式，并扩展理财申购、卡片挂失等操作策略。

仅运行权限模块测试：

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_permission -v
```

V0.2.2 已在 `feature/security-v0.2` 提交，commit 为 `85e11b8`。

V0.2.2 已在 Windows / Python 3.14.7 下验证：新增 13 个权限测试、已有 13 个风险测试及 19 个银行功能测试，共 **45 个测试全部通过**。既有 Agent、工具、DeepSeek 连接和数据库实现未修改。

## 12. V0.2.3 Confirmation Flow

新增 `/chat/secure` 接入 Risk Engine、Permission Controller 和 `PendingActionManager`，提供黄色操作的确认流程。原有 `/chat` 保留 V0.1.2 直接执行行为；测试确认流程时请使用 **`/chat/secure`**。

```text
POST /chat/secure → 原 Agent 识别工具和参数 → 参数校验 → Risk Engine → Permission Controller
  green  → 调用原工具
  yellow → 保存待确认任务，返回 pending 和 action_id（此时不扣款）
  red    → 返回 require_verification（不执行，也不创建可确认任务）

POST /confirm → 校验账户及 action_id → 原子领取任务 → 调用原工具 → 返回工具结果
POST /cancel  → 校验账户及 action_id → 删除任务，不执行工具
```

### 创建待确认转账

```http
POST /chat/secure
Content-Type: application/json
```

```json
{"account_id":"1001","message":"给账户1002模拟转账500元"}
```

返回的核心字段如下；`action_id` 和 `created_at` 在运行时生成：

```json
{
  "status": "pending",
  "message": "转账 500 元，需要用户确认，尚未执行。",
  "risk_level": "yellow",
  "action_id": "运行时生成的任务编号",
  "pending_action": {
    "action_id": "同一个任务编号",
    "account_id": "1001",
    "action": "transfer_money",
    "params": {"to_account_id": "1002", "amount": "500"},
    "risk_level": "yellow",
    "created_at": "运行时生成的UTC时间"
  }
}
```

响应同时包含 Agent 的 `reply` 和已完成查询的 `tool_results`。转账参数采用原工具的账户编号和金额字符串；只有收款人姓名时仍需补充编号，不猜测账户。

### 确认或取消

将收到的真实任务编号填入请求。确认无需再次调用 LLM：

```http
POST /confirm
Content-Type: application/json
```

```json
{"account_id":"1001","action_id":"收到的真实任务编号","confirmed":true}
```

确认后原转账工具重新执行参数、账户和余额校验；成功返回 `status: completed`、`result` 和根据工具结果生成的 `reply`。工具拒绝交易时返回 `status: failed` 和原工具错误。

取消请求：

```http
POST /cancel
Content-Type: application/json
```

```json
{"account_id":"1001","action_id":"收到的真实任务编号"}
```

也可以向 `/confirm` 发送 `confirmed: false` 取消。取消返回 `status: cancelled`，不会执行工具。未知、已消费或其他账户的任务返回 HTTP 404；非法请求参数返回 422。确认与取消接口不依赖 LLM Key。

### 管理器与阶段范围

`PendingActionManager(executor)` 接收服务端固定注册的执行器，提供 `prepare_action`、`create_pending_action`、`get_pending_action`、`confirm_action`、`cancel_action`。管理器独立于 LLM、HTTP 和 SQLite；保存账户、操作、参数快照、风险等级、UTC 创建时间及唯一任务编号。

任务在执行前原子领取并移出待确认队列，同一个编号最多调用一次执行器。重复或并发确认不会重复扣款；领取后的操作不能再取消。执行器返回业务失败时任务也被消费；意外异常返回 `execution_unknown`，不能假定资金未变动，应先查询账单，管理器不自动重试。

本阶段待确认数据只保存在**当前服务进程内存**，重启会丢失，服务请保持一个 worker。多个任务编号及固定执行器接口为后续多步骤任务、人工接管和独立红色验证流程留出扩展位置；未实现持久化、过期策略或真实身份验证。

账户归属检查基于 Demo 请求中的 `account_id`，不代表已经实现登录认证。红色操作仍需后续强验证模块；不能通过 `/confirm` 放行。LLM 的工具列表没有确认或取消工具，模型自行生成的确认字段也不会被接受。

V0.2.3 改动仅保留在 `feature/security-v0.2`，人工审查前不暂存、不提交 commit。

已在 Windows / Python 3.14.7 下运行完整测试：新增确认流程测试 24 个、既有测试 45 个，共 **69 个全部通过**。测试使用模拟 LLM 和临时数据库，不访问真实 DeepSeek 或修改用户数据库。
