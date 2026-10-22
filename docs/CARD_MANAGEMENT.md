# 模拟银行卡：数据与安全执行链（第一、二批）

第一批建立独立模型、离线样本、只读服务与纯状态核心；第二批已接入 Agent、只读 HTTP 查询、原有确认管理器及事务审计。没有开发新前端页面。卡片状态只表示模拟数据，不控制真实银行卡，也不拦截既有转账或重算消费。当前账户隔离不是生产持卡人身份认证。

## 数据模型

三个表只由显式离线初始化创建，服务启动和查询不会建表或补数据。

| 表 | 字段及约束 |
| --- | --- |
| `cards` | `card_id` 内部不透明主键；`account_id` 外键；`display_name`；四位 `last_four`；独立的 `temporary_frozen` 与 `loss_status`（`normal` / `reported`）；整数 `version`；UTC 毫秒 `created_at` / `updated_at`；固定来源 `synthetic_card_demo_v1`；唯一 `(account_id, card_id)` |
| `card_instrument_links` | 主键 `(account_id, payment_instrument_ref)`；`(account_id, card_id)` 复合外键；同一支付工具只属于该账户的一张卡；触发器拒绝更新和删除，避免迁移历史关联 |
| `card_operations` | `operation_id` 主键、全局唯一 `action_id`；账户和卡复合外键；操作类型；操作前后临时状态、挂失状态、版本；UTC 执行时间；仅允许已提交操作的 `completed` 凭据；同卡结果版本唯一；触发器禁止改写或删除凭据 |

应用校验拒绝完整卡号形态的连续长数字，只接受四位展示尾号和受限的模拟支付工具引用。模型没有 PAN、CVV、PIN 或认证凭据字段。SQL 数据均使用参数绑定；SQL 结构是程序常量。

UTC 时间存储不涉及金额累计。原 `accounts`、`bills`、`audit_logs`、`analysis_datasets`、`spending_records` 的结构与语义均不改变。

## 只读服务与消费关联

```python
from card_management.service import CardService

# account_id 由未来服务端可信上下文注入，不能照搬模型或客户端声称的身份。
cards = CardService(database_path, account_id="1001")
cards.list_cards()
cards.get_card("card-7a4e2d91")
cards.resolve_card_target("模拟生活卡A", "0101")  # 同账户完整名称及四位尾号，必须唯一匹配
cards.card_for_consumption("oct20-a")
cards.get_operation(action_id)
```

数据库以 `mode=ro` 和 `query_only=ON` 打开，查询不创建缺失数据库或表。不属于当前账户的卡返回与不存在相同的 `CARD_NOT_FOUND`。这个账户范围机制不是生产身份认证。

关联先调用原 `AnalysisService` 校验数据集及原始消费记录的账户归属，读取记录中的支付工具引用，再用账户和引用精确匹配映射；连接卡片时再次匹配账户。不按名称、尾号或模型输出猜测。没有引用返回 `NO_PAYMENT_INSTRUMENT`，没有映射返回 `NO_MAPPING`；记录不存在或跨账户访问仍是错误，不伪装成未关联。

记录和不可变映射分两次只读连接查询；卡片返回查询时的当前状态，不宣称这是消费发生时的历史状态。后续临时操作不改变记录、内容校验值或映射。出现多个数据集时，未显式指定且无法唯一选择会明确失败。

## 状态规则

有效展示状态优先级：正式挂失 > 临时冻结 > 正常。`usable_in_demo` 只是这两个状态字段的综合结果。

| 当前状态 / 请求 | 结果 |
| --- | --- |
| 正常、未临时冻结 → 临时冻结 | 临时字段变为真，版本加一 |
| 正常、已临时冻结 → 解除临时冻结 | 临时字段变为假，版本加一 |
| 再次冻结 / 再次解除 | `ALREADY_FROZEN` / `NOT_TEMPORARILY_FROZEN`，不产生新凭据 |
| 已正式挂失 → 任一临时操作 | `CARD_REPORTED_LOST`，不能恢复使用 |
| 版本不同 / 版本达到整数上限 | `VERSION_CONFLICT` / `VERSION_LIMIT` |
| 跨账户或卡不存在 | `CARD_NOT_FOUND` |
| 正式挂失或解挂 | `VERIFICATION_REQUIRED`，本批永不执行 |

`plan_transition(card, command)` 是无副作用的纯规则函数，不表示权限已经通过。数据库更新触发器还禁止变更身份、来源和挂失字段，要求临时字段确实改变且版本恰好加一。

## 默认关闭的事务内核

`apply_temporary_action()` 不直接注册为工具；普通调用在打开数据库前返回 `AUTHORIZATION_REQUIRED`。只有原确认管理器原子领取已确认的卡片任务后，才进入专用许可作用域。正式挂失和解挂在风险、权限以及核心校验层阻断，不能通过构造临时许可执行。

许可检查绑定数据库绝对路径、账户、卡、操作、预期版本和 action_id，具有短期有效期与加锁的一次性消费检查；等待 SQLite 写锁后再次检查过期。该接缝不是对持有任意 Python 执行权或数据库写权限的本机程序的安全沙箱，不能把测试许可视为已实现风险、身份或用户确认机制。

持有有效专用许可时，事务流程为：

1. 以 `mode=rw` 打开已有库，`BEGIN IMMEDIATE` 获得写锁。
2. 检查 action_id 原凭据：完全相同的命令返回原结果，不同绑定拒绝。
3. 读取账户所属卡片，在锁内检查版本、状态、时间，重新运行 Risk Engine 与 Permission Controller。
4. 带旧状态和版本条件更新卡片，插入同一事务中的唯一操作凭据及必要风险、权限、提交审计，再提交。审计使用同一连接；审计失败回滚。
5. 提交前可确认的失败回滚卡片和凭据；提交开始后异常返回 `EXECUTION_UNKNOWN`，要求核对状态及凭据，禁止自动重试。

并发命令由 SQLite 写事务串行化，并经版本条件防止旧状态覆盖。一次性许可失败后也不恢复；重新发起必须经安全链重新授权。重复执行保护以持久化凭据为准，数据库重启后仍保留。

事务中的 `risk_evaluated`、`permission_decided` 标记 `stage=card_confirmation`。`card_operation_committed` 关联 action_id、operation_id、卡片、前后版本与状态。事务外请求/确认及最终工具事件沿用现有审计；事后审计失败仅附警告，不覆盖已提交结果或触发重试。最新策略升级红色时，提交真实阻断决策审计，卡片与凭据保持不变。

## 第二批 Agent、API 与确认协议

只读工具：`list_cards`、`get_card`、`get_card_for_spending_record`、`get_card_operation`，均显式列入绿色只读白名单。

黄色工具：`temporarily_freeze_card`、`remove_temporary_freeze`。红色工具：`report_card_loss`、`cancel_card_loss_report`。模型只能提供 card_id；账户、版本、风险、许可及结果全部由服务端产生。`execute_tool`、默认执行器和旧 `/chat` 均不能直接执行卡片写操作；只有 `/chat/secure` 准备任务，原 `/confirm` 确认后执行黄色操作。

示例输入：`查询我的银行卡`、`查询卡片card-7a4e2d91状态`、`查看原始消费记录 oct20-a 对应的银行卡`、`临时冻结card-7a4e2d91`、`解除临时冻结card-7a4e2d91`、`正式挂失card-7a4e2d91`、`解挂card-3e902ad6`。挂失/解挂返回 `status=require_verification`；审计为 `verification_required/not_executed`，没有黄色任务或成功凭据。

识别采用有限明确语法及模型工具参数的交叉检查；模型未调用工具且正常返回非空文本时可使用同一受限解析。异常、空回复、未知或无效调用不兜底重试。咨询、否定、假设、引用、多操作、模糊的“冻结/停掉/这张卡”等只澄清。不能声称覆盖任意自然语言；不自动从消费异常发起处置。

明确目标还支持完整卡名与四位展示尾号同时出现，例如 `请帮我临时冻结模拟生活卡A，银行卡尾号0101。`，或 `解除临时冻结模拟生活卡A，银行卡尾号0101。`。当前有限语法要求名称和尾号之间用中文或英文逗号分隔；不支持只凭名称、尾号或“这张卡”定位。`CardService.resolve_card_target` 使用当前服务端账户的只读列表，同时对名称与尾号做精确相等比较，不做相似、大小写或全半角归一匹配；零匹配返回 `CARD_TARGET_NOT_FOUND`，多匹配返回 `CARD_TARGET_AMBIGUOUS`，均不创建任务，也不向其他账户搜索。

Agent 只将服务端唯一匹配到的内部 card_id 与枚举工具名放入本轮可信上下文，不把卡名内容提升为系统指令；模型参数必须与此 ID 一致。工具 schema 仍只接收 card_id，不接收名称、尾号、版本、风险或授权字段。原 `prepare_card_params` 再按账户查询实际卡片、验证操作条件并读取当前版本，再进入 Risk Engine → Permission Controller → 原待确认任务。定位本身不是身份认证或用户确认，不能改变卡片状态；确认时仍复核版本与状态，挂失/解挂仍红色阻断。

| 只读 API | 用途 |
| --- | --- |
| `GET /accounts/{account_id}/cards` | 卡列表 |
| `GET /accounts/{account_id}/cards/{card_id}` | 卡详情 |
| `GET /accounts/{account_id}/analysis-records/{record_id}/card` | 记录关联卡片 |
| `GET /accounts/{account_id}/card-operations/{action_id}` | 查询持久化真实结果，结果未知时用于核对，不自动重试 |

查询返回结构化结果、request_id、audit_id、tool、decisions。操作结果查询的 `card_operation` 内含原执行 request_id/audit_id；外层标识本次查询请求。

`POST /chat/secure` 沿用原请求。卡片黄色响应包含 `pending_action.action`、`params.card_id`、`params.expected_version`、`conversation_id`、action_id 和黄色权限决策。任务仅存内存，卡片任务5分钟过期；同会话已有转账或卡片任务不能被新任务替换，查询不消费任务。

`POST /confirm` 和 `/cancel` 的卡片任务请求必须带 `account_id`、`action_id`、原 `conversation_id`。API 在原 SessionStore 检查账户、会话存在且未过期，并锁定会话；管理器检查绑定及任务时效、原子领取。转账确认的既有请求格式继续兼容。聊天中的“好的/确认一下”不触发该接口。

许可有效30秒，在取得数据库写锁后复核时效。成功响应为 `status=completed`，`result.card_operation`（同 `execution_result.card_operation`）包含专用 operation_id、原 action_id、card_id、前后状态/版本、executed_at 和 completed 结果，没有 transfer_id。版本冲突返回 `VERSION_CONFLICT`，任务不恢复；用户需重新查询并明确发起。`execution_unknown` 时应查询操作结果；无凭据不能作为允许自动重试的依据。

第三批前端必须先按 pending_action.action 分支展示卡片与转账任务，卡片确认/取消发送原 conversation_id，并单独渲染 card_operation，不能读取 amount 或包装成转账凭据。当前旧前端尚未完成这些适配，不能认为已有完整浏览器卡片闭环。

## 明确离线初始化

在项目根目录手动执行，使用全新且已忽略的独立目录：

```powershell
.\.venv\Scripts\python.exe -B -m card_management.sample --database .cache/card-management-demo/banking.db
```

复用现有分析演示库的保护逻辑，拒绝默认正式数据库、进程配置目标和 `.env` 配置目标；已有目标必须具有受支持的演示标记并通过消费样本校验。不要指定实际业务数据库，也不要在启动钩子调用初始化器。

首次创建独立账户/消费库后，再在单个事务中增补卡片与映射；这两个步骤不是一个跨模块原子事务。若后一步失败，新库可能保留已初始化的消费部分；初始化器不会删除或重置数据库。再次导入只接受相同身份/映射，保留已有临时状态、版本和操作凭据；冲突明确拒绝。

| 账户 | 卡片ID / 初始状态 | 映射 |
| --- | --- | --- |
| 1001 | `card-7a4e2d91` / 正常，尾号0101 | `demo-instrument-wallet-a` |
| 1001 | `card-8c6f104b` / 临时冻结，尾号0202 | 无 |
| 1001 | `card-3e902ad6` / 正式挂失，尾号0303 | 无 |
| 1002 | `card-5b71c8f0` / 正常，尾号0404 | `demo-instrument-wallet-b` |

已挂失卡仅是离线初始样本，不代表执行挂失或通过了强验证。不会修改原消费记录，也不会扣减账户余额。

## 测试与本机回归

新增同步测试使用临时 SQLite 库和测试许可，覆盖查询隔离、显式关联、状态规则、重复/并发/版本、许可绑定/时效/一次性、操作凭据、回滚/提交结果未知、样本幂等及原账本、日累计与消费分析不变。没有使用真实银行或模型服务。

PowerShell 完整回归命令（只为该 Python 子进程临时设置环境，退出后恢复）：

```powershell
Set-Location 'E:\GPT\2026-10-14\python-ai-agent-banking-agent-llm-2\outputs\Banking-Agent-AI'
$cardTestTemp = Join-Path (Get-Location) '.cache\card-management-test-temp'
New-Item -ItemType Directory -Force -Path $cardTestTemp | Out-Null
$cardOldTemp = $env:TEMP
$cardOldTmp = $env:TMP
$cardOldTmpdir = $env:TMPDIR
try {
    $env:TEMP = $cardTestTemp
    $env:TMP = $cardTestTemp
    $env:TMPDIR = $cardTestTemp
    .\.venv\Scripts\python.exe -B -m unittest discover -s tests -v
} finally {
    $env:TEMP = $cardOldTemp
    $env:TMP = $cardOldTmp
    $env:TMPDIR = $cardOldTmpdir
}
```

Codex 环境已知的 asyncio 初始化问题不能通过修改业务代码规避；完整异步回归应在本机执行。前端本批没有改动。

第二批本地验证记录（2026-10-21）：新增40项测试，其中37项同步安全链/模拟Agent测试已实际通过，3项真实HTTP测试尚待本机执行。连同既有同步回归，253项实际通过；完整测试集可收集397项，不能据此宣称397项已全部通过。原357项用例均保留，仅将旧“卡片尚未实现”的一项测试中的卡片子用例改为缺参澄清预期，仍断言不创建任务、不返回业务工具结果。独立进程冷导入与响应模型校验通过；没有真实模型联网或浏览器验收。

卡名与尾号定位修复记录（2026-10-22）：先新增测试复现原请求返回 needs_input，再实现确定性唯一匹配。新增17项测试通过，覆盖匹配、名称/尾号冲突、相似名称、同账户重复、跨账户、取消/确认、状态版本、挂失阻断、伪造模型参数、数据库查询失败及待确认保护。连同现有可同步运行的回归，共276项实际通过；前端55项测试通过。完整后端可收集420项（原403项保留），完整异步/HTTP回归须在本机运行上述命令；没有进行真实DeepSeek或浏览器验收。

浏览器复测使用已有独立卡片演示库，不重新初始化。先查询当前卡片状态；正常未冻结的“模拟生活卡A、尾号0101”应通过上述自然语言请求进入黄色待确认，确认前状态和版本不变。取消后状态仍不变；再次发起并点击正式确认后，仅目标卡临时状态及版本改变，生成 card_operation 与审计。确认会写入独立演示库。若原卡已冻结，应明确提出解除临时冻结并经过相同确认链。名称/尾号冲突应澄清，正式挂失或解挂应红色阻断；不根据模糊指代或异常消费自动处置。

## 第二批涉及的接入位置（已实现）

- `security/risk_engine.py`：显式卡片操作策略，查询绿色、临时操作黄色待确认、挂失/解挂红色强验证阻断。
- `security/confirmation.py`：扩展有类型的待确认命令，绑定卡片、版本和账户；保留现有转账保护及一次确认语义。
- `card_management/authorization.py`：仅供可信安全执行链调用的许可发放器，不接受模型或 HTTP 自报授权。
- `card_management/transitions.py`：执行事务内的风险/权限复核和审计衔接；失败与未知结果不自动重试。
- `agent/llm_agent.py`、`agent/prompt.py`、`agent/dialogue.py`：严格 schema、只读白名单及受保护写命令调度，防止自然文本成为授权。
- `main.py`、`api/responses.py`：账户上下文、只读查询与操作准备/确认协议、结构化卡片凭据和错误状态；不能将卡片凭据包装成转账成功凭据。
- `card_management/tools.py`、`agent/card_intents.py` 及 `tests/test_card_integration.py`；前端接入另批实施。`api/responses.py` 已支持通用字典结果，本批直接复用而未修改。

权限控制器的现有规则尽量复用，只有确有必要才扩展；任何卡片接入都不得恢复旧 `/chat` 写权限或削弱转账日累计事务。
