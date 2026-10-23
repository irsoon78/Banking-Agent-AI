# 第四阶段第一批：可信只读调查核心

本批只有确定性Python调查核心、内存证据、步骤契约和测试。没有接入模型、HTTP、前端或持久化调度。`orchestration=deterministic`，不能将本批称为已实现模型自主规划。

调查只读取业务表；必要审计会写入当前配置数据库的既有`audit_logs`。不创建消费样本、银行卡、黄色任务或操作凭据，不修改余额、账本、卡片、消费记录及原风险策略。不要把调用本核心当成完全不写任何文件的源码检查。

## 服务端调用

```python
from investigation import InvestigationService, InvestigationRequest

# 在可信服务端已配置明确独立的BANK_DB_PATH之后调用。
# account_id来自现有可信Demo上下文，不采用模型参数；不是生产身份认证。
request = InvestigationRequest(
    start_date="2026-10-01",
    end_date_exclusive="2026-10-21",
    focus="similar",  # similar / large / all
    max_records=6,
    max_tool_calls=20,
)
report = InvestigationService(account_id="1001").run(request)
```

日期按Asia/Shanghai业务日期解释，结束日不含，复用原日期校验及UTC边界，最大366天。必须明确日期；本批不解释“十月份”“最近”等自然语言。所有输入严格禁止额外字段，包括account_id、SQL、路径、风险、记录ID、卡片ID、许可及工具原始响应。

数据库位置沿用既有`BANK_DB_PATH`配置；缺失数据库明确拒绝，不建目录、表或样本。新增模块不在应用启动时注册或调用。适配器在开始调查时惰性复用`main.audited_execute_tool`函数，不启动FastAPI lifespan、不实例化真实模型。该函数沿用原只读兼容审计标记，并不表示调查调用了旧`/chat` HTTP接口。

## 查询及依赖

1. `analyze_spending`，固定`view=anomalies`，获取真实报告、覆盖范围和异常规则结果。
2. 按focus选择已有`SIMILAR_PAYMENT_V1`、`LARGE_PURCHASE_V1`命中。不复制规则算法，不将未知规则当成无异常。
3. 从命中结果提取并去重记录ID，最多选择6条；每条生成不透明证据引用。
4. `get_spending_record`依赖分析成功，核验账户、数据集、版本、记录ID、CNY原始字段、金额表示及业务日期。
5. `get_card_for_spending_record`依赖对应原始记录核验成功，同时核对账户、数据集及原始支付工具引用。无映射是明确结果，不是猜测目标的理由。
6. 已验证关联产生卡片证据引用，按card_id去重，执行`get_card`查询当前状态及read_at。关联响应中的卡片旧状态不作为最终当前状态。
7. 汇总真实结果和步骤，不能认定盗刷、欺诈或重复扣款已经成立。

确定性执行器通常先核验本批全部原始记录，再逐条关联，最后读取去重卡片。每步包含`execution_index`；返回steps按照真实尝试顺序排列，未执行节点排在后面。`depends_on`表示真实先决步骤。外部选择其他合法就绪顺序时，同样受依赖约束，不能直接指定ID跳过分析或关联。

记录会在每次查询前后通过现有`AnalysisService.current_dataset()`核对数据集元数据，固定账户、ID、版本、来源及覆盖信息；查询结果中的数据集也必须一致。发现版本/来源漂移停止继续查询，单条无效记录或关联失败只停止该分支。内部元数据校验不属于额外银行工具，预算保守统计工具查询尝试。

这不是跨整个任务的数据库原子快照。每次只读连接结束后才写审计，不跨调用持有SQLite事务。消费样本没有本批运行时写入口；卡片可能发生变化，所以报告保留读取时间，未来处置仍必须重新核对状态和版本。

## 结果契约 investigation.v1

| 字段 | 含义 |
| --- | --- |
| investigation_id / schema_version / orchestration | 服务端UUID、结果版本、deterministic |
| account_id / period / focus | 可信账户、明确业务日期和时区、选择的调查重点 |
| status | running、completed、partial或failed；running仅用于内存快照 |
| dataset / coverage / analysis_summary | 原工具实际数据集、覆盖及已记录消费汇总；不是调查记录金额之和 |
| discovered_record_ids | 本次所选真实规则发现的去重ID，不接受外部补入 |
| findings | 原规则编号、原因及阈值等原始提示；源步骤、证据引用及核验状态 |
| evidence.records | 发现的记录、源分析步骤及明细步骤；discovered/verified/failed，只有verified才有record |
| evidence.links | 每条记录独立的关联证据；not_executed/verified/failed；linked=false时不生成card_ref |
| evidence.cards | 只由已核验关联发现；discovered/verified/failed，只有verified才有当前卡片快照与read_at |
| steps | step_id、四项枚举工具、evidence_ref、depends_on、状态、execution_index、时间、真实权限决策、request_id、audit_event_ids、错误码 |
| budget | 去重发现数、选择数、原始记录核验数、整条证据链完成数、未完成数、因6条限制未选择数、查询尝试数及上限 |
| limits / errors / audit | 截断原因、步骤错误、审计完整性和真实事件关联 |
| safety / disclaimer | 只读业务边界、没有新建确认任务、模拟身份限制、命中不代表欺诈等免责声明 |

步骤状态：planned、running、succeeded、failed、skipped（依赖未成功）、not_executed（预算或主动结束/停止）。没有执行的节点没有执行序号或开始时间，不能显示为成功。

相似消费的`observed_interval_milliseconds`和`observed_interval_minutes`仅从已核验的UTC原始记录计算。金额沿用工具返回的整数分字符串和十进制元字符串；不使用浮点累计，不让模型产生数字。卡片状态完全采用实际只读服务结果。

`unfinished_records`表示已发现但尚未完成明细→关联→卡片状态整条证据链的数量，包含失败、预算未执行及记录限额遗漏，不能仅凭原始明细已读取就称调查全部完成。

## 第二批可复用的合法计划接缝

```python
run = InvestigationService(account_id="1001").start(request)
safe_context = run.planning_view()
commands = run.ready_steps()
run.execute_step(commands[0])
snapshot = run.snapshot()
# 第一批也可 run.run() 确定性完成；主动提前结束用run.finish()，不会伪装成全部成功。
```

`StepCommand`仅有step_id、四工具枚举和evidence_ref。命令必须与当前调查的私有步骤注册表完全匹配，依赖已成功且尚未消费。任意真实ID、其他调查的引用、错误工具、风险字段或伪造依赖均无法提交。本批没有加载外部快照或恢复已结束任务的接口。

`planning_view()`只输出焦点、日期、规则编号、证据引用、状态和就绪命令。不含商户、卡名、规则原因、工具原始响应或可执行文字。商户名称在报告里仍是普通数据，不可作为指令、工具名或参数。不得在第二批把完整snapshot替代白名单规划上下文发送给模型。

snapshot和命令是独立副本；修改返回JSON不能修改私有可信状态。单次调查使用内存锁串行领取步骤，避免并发重复消费和突破预算。这是应用内边界，不是对持有任意Python执行权或数据库写权限程序的安全沙箱。

## 失败、预算与审计

- 6条原始记录和20次查询尝试是硬上限，调用方只能调低。命中更多记录返回partial并给出遗漏数，不能宣称已调查全部异常。
- 原始记录失败不执行其关联；其他独立分支可继续。失败查询没有自动重试。未知规则、初始分析失败或无法建立可信数据集返回failed。
- 日期覆盖不足返回partial，即使已记录的相关消费均已核验。无命中只能说已覆盖样本内未命中现有规则，不代表未覆盖日期无异常或不存在其他风险。
- 审计在查询前失败则不查询；事后审计失败保留已获得证据，标记incomplete并停止后续查询，不重新执行。
- 复用现有Risk Engine与Permission Controller。只有真实green/allow结果可采用；调查没有PendingActionManager或确认许可发放能力。
- `investigation_started/finished`关联根request_id和investigation_id。各步有独立request_id，既有风险、权限、工具事件及新增step_started/finished事件。事件详情只添加步骤、依赖及证据引用；不记录模型原文或凭据。
- 没有新增数据库表或任务持久化。进程结束后内存调查丢失，已写入的审计保留；返回结果可供第二批设计持久化。

## 测试与回归

`tests/test_investigation.py`使用每例新建的临时SQLite数据库。版本3冻结状态、缺失映射、额外记录和恶意文本均只构造在测试库；不重置既有演示数据库。

已实际通过新增50项同步测试：正常调查、金额/时间、关联去重、实时冻结版本、无异常、覆盖不足、依赖/引用隔离、跨账户和版本不一致、记录与工具预算、查询及审计失败、注入、并发领取、零写工具/零待确认任务、原业务表不变、冷导入不初始化、无真实模型。

本轮实际执行：新增50/50与原有可同步运行回归303/303通过，共353项；完整后端收集470项（原420项保留），没有把收集成功当成完整回归通过。原有源码、测试与前端文件未修改，前端55项是既有验收基线，本批未重跑前端。

在项目根目录运行完整本机回归；仅临时改变当前PowerShell进程中的临时目录变量，退出后恢复，不安装依赖：

```powershell
Set-Location 'E:\GPT\2026-10-14\python-ai-agent-banking-agent-llm-2\outputs\Banking-Agent-AI'
$invTestTemp = Join-Path (Get-Location) '.cache\investigation-tests'
New-Item -ItemType Directory -Force -Path $invTestTemp | Out-Null
$invOldTemp = $env:TEMP
$invOldTmp = $env:TMP
$invOldTmpdir = $env:TMPDIR
try {
    $env:TEMP = $invTestTemp
    $env:TMP = $invTestTemp
    $env:TMPDIR = $invTestTemp
    .\.venv\Scripts\python.exe -B -m unittest discover -s tests -v
} finally {
    $env:TEMP = $invOldTemp
    $env:TMP = $invOldTmp
    $env:TMPDIR = $invOldTmpdir
}
```

本批未进行真实DeepSeek或浏览器验收。当前Codex环境存在既知asyncio限制，不以修改原异步业务代码的方式适配；完整回归结果以本机实际运行为准。
