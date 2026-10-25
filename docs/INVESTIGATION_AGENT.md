# 第四阶段第二批：受限规划、Agent接入与持久化查询

> 历史记录说明：本文保留阶段开发时的测试数量、待验收及未提交状态，不代表v0.4.1的发布结果。当前部署见[根README](../README.md)，本次实测见[发布说明](RELEASE_NOTES_v0.4.1.md)。命令中的`<项目根目录>`须替换为实际解压/克隆目录。

本批在第一批确定性核心上增加独立调查模式。没有前端调查页面、后台续跑或自动处置，没有修改资金及卡片执行权限。模型仅选择合法步骤；金额、规则、原记录、关联卡片及状态仍由原Python工具返回。当前账户是Demo上下文，不是生产级持卡人身份认证。

## 调用入口与范围

`POST /chat/secure`沿用`message / account_id / conversation_id`请求。服务端SessionStore校验账户、会话并注入conversation_id；调查路由优先于单项分析和卡片意图。旧`/chat`不进入本模式，普通Agent工具集合未增加调查执行器或快照存储工具。

可用指令：

- 帮我调查2026年10月有没有可疑消费，特别是重复扣款。
- 检查2026年10月的大额消费，并看看关联的是哪些银行卡。
- 帮我调查2026年10月的异常消费。
- 调查2026-10-01至2026-10-20的相似消费。

服务端支持明确年月、ISO日期范围（用户结束日包含，内部转为不含结束日），或按Asia/Shanghai解释的本月、上月、最近30天。没有年份的“10月”要求澄清。暂不解释任意中文日区间或分类、商户过滤；不能可靠解释的范围要求澄清，不能擅自扩大为整月。模型不能改变日期或用户重点；similar / large / all由本轮用户意图确定。

Python入口：

```python
from investigation.planner import run_planned_investigation
from investigation.contracts import InvestigationRequest

# 仅在已绑定可信Demo账户、会话及独立BANK_DB_PATH的服务端调用。
report = await run_planned_investigation(
    client=existing_async_client, model=configured_model,
    account_id=trusted_account, conversation_id=trusted_conversation,
    options=InvestigationRequest(start_date="2026-10-01",
        end_date_exclusive="2026-11-01", focus="similar"),
)
```

## 模型实际规划协议

1. 开始调查，保存running快照。执行唯一就绪的初始`analyze_spending`。
2. 每次读取核心`planning_view()`及`ready_steps()`。没有就绪步骤则finish；只有一个就绪步骤时直接执行，不伪造模型选择。
3. 有多个合法分支时，向现有模型发送**白名单规划视图**，只提供一个`select_investigation_step`函数。模型不能看到原始用户文本、商户名称、卡片名称、规则解释或完整工具响应。视图只含时间范围、固定重点、规则枚举及服务端生成的不透明证据/步骤引用。
4. 函数参数：`step_id / tool / evidence_ref / focus / reason_code`。`tool`仅四种只读枚举，`reason_code`仅有限枚举；其余字段严格禁止。focus是固定范围回显，不是扩大权限的参数。
5. 拒绝未知函数、多函数、空/普通文本响应、不正常结束、超长/无效JSON、伪造步骤或证据、改变focus及额外的SQL/账户/日期/record_id/card_id/金额/风险字段。等待模型之后重新获取就绪集合；执行前核心再次检查本调查私有步骤注册表、依赖、证据及预算。
6. 合法选择会影响**真实执行顺序**。测试使用相反就绪分支选择，核验两条原始记录实际查询次序不同，最终可信证据一致。没有固定`investigate_all`模型工具，也没有让模型标记步骤成功。

`planner_mode`：

- `model_planned`：至少一次合法模型选择，后续没有规划降级。
- `deterministic_fallback`：模型调用失败、非法计划或规划预算耗尽；`planning.fallback_reason`记录服务端代码，已接受选择仍保留。不重复调用失败的模型，不重试已经执行/失败的工具。
- `deterministic_no_choice`：没有出现需要模型选择的独立分支，例如无异常或只有唯一依赖路径。不宣称模型自主规划。

每次模型调用设置15秒SDK超时；现有SDK关闭重试。最多20次模型选择、20次业务只读工具尝试、6条原始记录。没有跨网络请求持有SQLite事务。执行器仍只有`analyze_spending / get_spending_record / get_card_for_spending_record / get_card`四种业务工具；快照查询是模式外的独立只读元数据API。没有进入PendingActionManager、资金执行器或卡片许可发放路径。

## 响应与第三批前端契约

SecureChatResponse新增以下可选字段（原转账、卡片字段不变）：

|字段|含义|
|---|---|
|investigation_id|服务端生成的32位十六进制调查ID|
|investigation|真实报告，不使用模型自由文本生成金融结论|
|execution_status|running / completed / partial / failed / interrupted|
|coverage_status|complete / incomplete / unknown|
|planner_mode|上述三种模式|

报告包括：`period / focus / dataset / coverage / findings / steps / evidence / budget / audit / planning / persistence_status`。每个实际步骤有tool、depends_on、evidence_ref、status、execution_index、started_at、finished_at、request_id、audit_event_ids、真实decisions和失败code。`planning.events`是模型选择的接受/拒绝记录，含服务端诊断及audit_event_id，**不能作为已执行步骤展示**。未执行节点保留明确状态；未核验证据不能展示为已确认事实。

`evidence.records / links / cards`保留原始记录、确定性关联、卡片版本和读取时间。相似消费时间差来自核验后的原记录；规则命中不能宣称已确定盗刷或重复扣款。示例两笔88.00元、2分钟间隔由服务端计算，不写死在回复或模型提示中。

`tool_results`保持为空；本模式的实际工具调用完整保存在`investigation.steps`，不伪造单个聚合工具调用。顶层`decisions`由实际步骤决策汇总。顶层`request_id/audit_id`沿用原聊天审计，调查audit及每步request_id可查询各自真实事件。

旧`status`兼容第一批语义，覆盖不足也可能为partial；**新页面必须分开读取execution_status和coverage_status**。10月请求即使步骤全部成功，仍为execution_status=completed、coverage_status=incomplete，coverage明确只覆盖1—20日。无命中仅针对已覆盖样本。

## 持久化与恢复

`database.db.init_db()`在既有事务中仅增加`investigation_reports`表和账户/时间索引，不重置账本或导入任何样本。本次开发没有启动或迁移已有演示库；以后仅在用户明确指定的隔离环境启动时使用现有初始化机制建立元数据表。

表列：investigation_id主键、account_id账户外键、conversation_id、owner_id进程标识、execution_status、coverage_status、planner_mode、created_at、updated_at、finished_at、revision、snapshot_json。日期、数据集版本、步骤依赖、证据、审计等存储在有界JSON中。

每次模型等待前、规划接受/拒绝后、实际步骤完成后及finish后保存。独立短BEGIN IMMEDIATE事务，不在持有写锁时调用模型、业务工具或另一连接写审计。禁止变更快照账户/会话/进程归属和覆盖已终结报告；终结报告的完全相同保存可幂等返回。

单快照最大256KiB，最多32步骤、64规则提示、128个已发现记录、每类6条证据、20条规划记录。根字段白名单及嵌套敏感字段拒绝；不保存API Key、认证上下文、许可、模型原始响应或历史消息。没有自动删除或保留期管理；Demo报告总量需人工管理，不能宣称已具备生产存储治理。

新增接口：

- `GET /accounts/{account_id}/investigations?limit=20&offset=0`：返回items、total、分页及请求审计；limit 1—20，offset 0—1000。
- `GET /accounts/{account_id}/investigations/{investigation_id}`：返回investigation及请求审计。其他账户ID或不存在ID均404。

以上接口经过独立绿色Risk Engine与Permission Controller及审计。存储连接mode=ro且query_only，不重新查询消费或实时卡片、不执行模型、不更新快照、不创建任务。审计仍写audit_logs；“只读”指业务数据只读，而非不写任何元数据。

原报告保存的是**当时读取状态**，不会冒充实时卡片状态。`recovery.source=stored_snapshot / revision / updated_at / rerun=false`说明来源。服务重启后已完成报告仍能查询；原进程running报告投影为interrupted，已完成节点保留、未完成节点标为未执行，不伪造结束时间。请求取消尝试保存中断快照；极端进程崩溃只能恢复上次成功保存点。

查询恢复不会断点续跑。明确再次发送调查请求会生成新investigation_id、读取时间及审计。新报告不能覆盖旧报告。存储失败立即停止且persistence_status=failed，不能宣称报告可靠保存；审计失败标记incomplete及非完整执行。没有任何自动转账、冻结或挂失重试。

本MVP恢复检测采用单服务进程假设。多个worker或多实例共同读取同一库尚不支持：其他活跃进程的running报告可能被投影为中断。演示应使用单worker；没有实现分布式租约、后台调度或自动接管。

## 测试与本机验收

`tests/test_investigation_planning.py`：临时独立SQLite、模拟规划器，直接驱动立即完成协程并内联to_thread，验证选择差异、范围/证据约束、降级、持久化、恢复、读API及业务不变。

`tests/test_investigation_http.py`：真实事件循环、to_thread和FastAPI响应序列化；独立测试lifespan与模拟模型，不使用实际API Key。Codex已知asyncio限制时不运行这部分，将其交给本机完整回归。

本机完整回归（仅改变当前PowerShell进程临时目录，结束恢复；不安装依赖）：

```powershell
Set-Location '<项目根目录>'
$invTemp = Join-Path (Get-Location) '.cache\investigation-tests'
New-Item -ItemType Directory -Force -Path $invTemp | Out-Null
$oldTemp, $oldTmp, $oldTmpdir = $env:TEMP, $env:TMP, $env:TMPDIR
try {
    $env:TEMP = $invTemp
    $env:TMP = $invTemp
    $env:TMPDIR = $invTemp
    .\.venv\Scripts\python.exe -B -m unittest discover -s tests -v
} finally {
    $env:TEMP = $oldTemp
    $env:TMP = $oldTmp
    $env:TMPDIR = $oldTmpdir
}
```

本批不消耗真实DeepSeek额度，强制函数选择兼容性、真实模型合法选择率、超时后的网络表现及浏览器展示尚待人工验收。离线测试不能替代这些结论。第三批应展示过程、证据、覆盖和规划/降级来源；不要将旧报告中的卡片状态直接当作当前处置授权。真正处置仍须重新明确提出，走原黄色确认或红色强验证阻断。

## 本批实际交付记录

- 新增69项后端测试：66项同步/立即完成模拟协程测试已实际通过；3项真实异步HTTP测试已编写但未在Codex运行。
- 最终实际同步回归419/419通过（既有可运行回归303、第一批50、本批66）。完整收集539项，收集错误0；不等同于539项完整回归通过。完整异步/HTTP回归请执行上方PowerShell命令。
- 新Python子进程读取已保存报告的用例已实际通过。服务进程重建后的running投影、账户隔离、无业务重跑亦有测试。
- 前端未修改；55项是既有验收基线，本批未重跑。没有进行真实模型或浏览器验收。

本批新增：`agent/investigation_intents.py`、`investigation/planner.py`、`investigation/storage.py`、`tests/test_investigation_planning.py`、`tests/test_investigation_http.py`及本文档。

本批扩展：`agent/llm_agent.py`、`main.py`、`database/db.py`、`security/risk_engine.py`以及第一批的`investigation/__init__.py`、`investigation/contracts.py`、`investigation/service.py`、`investigation/tools.py`。风险策略只增加两个快照查询名称，未更改转账/卡片风险策略、确认管理器、许可或资金执行代码。

第一批`docs/INVESTIGATION_CORE.md`和`tests/test_investigation.py`保持原内容；前者是第一批历史说明，当前模型接入和持久化以本文档为准。第一批全部6个文件保留，整个工作区仍未提交。
