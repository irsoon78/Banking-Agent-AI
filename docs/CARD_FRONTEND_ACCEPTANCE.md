# 银行卡前端接入与人工验收

第三批只增加前端接入、测试及本文档。后端代码和现有数据库没有改动。页面展示模拟银行卡，临时状态不控制真实支付，不影响模拟转账余额或历史消费。账户选择不是生产登录认证。

## 页面与接口

使用既有 HashRouter：`/#/cards`。导航、聊天、账单分析、风险确认和审计页面继续共用 DemoProvider；在页面之间导航不会清除待确认任务。

- 卡片列表：`GET /accounts/{account_id}/cards`，展示名称、尾号、两个独立状态、版本及北京时间更新时间。
- 详情契约：`GET /accounts/{account_id}/cards/{card_id}`，API适配器已提供；本批页面直接展示列表已包含的详情字段。
- 消费关联：`GET /accounts/{account_id}/analysis-records/{record_id}/card`，从原始明细点击“查看关联银行卡”。异常提示先追溯原始记录，再查询明确映射，不能自动处置。
- 操作结果：`GET /accounts/{account_id}/card-operations/{action_id}`，只读查询持久化凭据。
- 操作准备：`POST /chat/secure`，卡片按钮将已选卡片的明确操作和card_id发送给现有安全Agent。
- 正式确认/取消：`POST /confirm`、`POST /cancel`，卡片任务使用原account_id、action_id和conversation_id；转账请求格式保持兼容。

PendingAction采用区分联合：转账参数只包含收款账户和金额；卡片参数包含card_id及服务端expected_version。卡片成功展示card_operation，转账成功展示transfer_id，两类凭据不能互换。

前端同步门闩防止重复点击，但不承担授权。后端仍负责归属、会话、任务时效、一次性许可、版本及事务审计。红色操作展示require_verification、verification_required、not_executed，不提供放行按钮。

## 状态与失败处理

- 页面按钮只准备请求；黄色确认前不修改卡片。所有卡状态均来自服务端查询，不设置本地“已冻结”假状态。
- 确认、取消返回后重新读取卡片；确认成功必须存在与原任务账户、卡片、action_id、操作及版本匹配的真实凭据。
- 版本冲突、过期或状态不允许时显示服务端原因和错误代码，要求重新查询，不自动重试。
- execution_unknown或确认响应丢失时保留未知操作绑定，锁定新操作，只允许读取原action_id结果。匹配的成功凭据才能解除该卡片操作的未知锁，并重新读取状态。
- 未查到凭据、读取失败或凭据不匹配都不能断言未执行，锁保持。没有任何自动重发写请求的机制。
- 任务准备响应丢失而未取得action_id时，无法自动查询该任务；保留不确定状态，需人工核对审计。取消响应丢失且没有成功操作凭据时，也不能用“未查到”推断取消已完成。
- 顶栏可选择模拟账户1001/1002。有待确认任务、正在处理操作或结果未知时禁止切换，避免遗失原任务。正常切换清空卡片、消息、审计和账户缓存；页面局部查询卸载，迟到结果不得覆盖新账户。
- 只保证页面导航保留任务；整页刷新不恢复内存中的前端任务。不要在确认期间刷新浏览器。

## 启动已有独立演示环境

不要重新初始化现有演示库。后端PowerShell窗口：

```powershell
Set-Location 'E:\GPT\2026-10-14\python-ai-agent-banking-agent-llm-2\outputs\Banking-Agent-AI'
$cardDemoDb = Join-Path (Get-Location) '.cache\card-management-demo\banking.db'
if (-not (Test-Path -LiteralPath $cardDemoDb -PathType Leaf)) {
    throw '独立银行卡演示库不存在；请停止并核对，不能改用正式数据库。'
}
$cardOldDb = $env:BANK_DB_PATH
try {
    $env:BANK_DB_PATH = $cardDemoDb
    .\.venv\Scripts\python.exe -B -m uvicorn main:app --host 127.0.0.1 --port 8000
} finally {
    $env:BANK_DB_PATH = $cardOldDb
}
```

前端另一个PowerShell窗口（复用现有依赖，不运行安装）：

```powershell
Set-Location 'E:\GPT\2026-10-14\python-ai-agent-banking-agent-llm-2\outputs\Banking-Agent-AI\frontend'
npm.cmd run dev
```

打开 `http://127.0.0.1:5173/#/cards`。卡片按钮仍需后端已配置模型；本批未进行真实DeepSeek联网验证。如果端口已被占用，请先确认现有服务是否使用上述独立库，不要直接继续操作未知数据库。

## 人工验收清单

下面是待人工执行的验收步骤，本轮自动化没有执行这些网页业务操作。确认冻结和解除会修改独立卡片演示库中的模拟状态、凭据及审计；正式库不得作为验收目标。

| 顺序 | 单项操作 | 预期 |
| --- | --- | --- |
| 1 | 打开银行卡管理 | 显示当前账户卡片、尾号、临时/挂失独立状态、版本、更新时间及模拟说明；记录初始状态 |
| 2 | 在账单分析选择2026年10月并打开异常消费原始记录 | 能核对原记录ID、商户、时间、金额、数据集；不会自动冻结 |
| 3 | 在原始记录点击“查看关联银行卡” | 返回服务端映射、卡名、尾号、当前状态和查询审计；可进入管理页面，保留record_id |
| 4 | 选择一张正常、未临时冻结的本人卡，点击“申请临时冻结” | 黄色待确认，显示目标、版本快照和原会话；卡状态不变 |
| 5 | 切换AI对话和风险确认页面，再取消原任务 | 原任务仍存在；取消后无状态变化、无成功操作凭据 |
| 6 | 再次发起同卡临时冻结 | 产生新的服务端action_id，仍不提前改变状态 |
| 7 | 点击一次“确认执行” | 处理中禁用按钮；成功后显示card_operation及operation_id，列表刷新为临时冻结、版本增加；没有transfer_id |
| 8 | 打开操作审计并刷新卡片 | 同action_id可追溯风险、权限、确认和提交；卡片状态与凭据一致 |
| 9 | 申请解除临时冻结，再正式确认 | 先黄色等待，确认后才解除，版本增加；正式挂失字段不改变 |
| 10 | 对明确card_id提出正式挂失，再对已挂失样本提出解挂 | 均红色require_verification，展示verification_required/not_executed，无放行按钮、无成功凭据 |
| 11 | 发送“不要冻结银行卡”“只是问问怎么冻结”“把我的卡冻结” | 澄清或安全拒绝，没有待执行任务或状态改变；普通“好的”不能确认任务 |
| 12 | 在无任务时切换账户1001/1002并查看关联记录 | 卡片、消息、关联结果不串户；异步旧结果不能重新出现 |
| 13 | 回归原转账黄色准备/取消及5000元红色阻断 | 转账继续使用金额及收款账户；按真实日累计判断，不因卡片操作改变余额或账本 |
| 14 | 回归账单分析及相邻月份同期比较 | 样本、覆盖说明、分类、商户和明细分页正常；仅比较同源数据 |

故障验收仅在隔离测试/可控环境进行：任务过期、另一个会话先改变版本、网络中断、未知结果查询及未查到凭据。不得通过重复点击确认或重发写请求来恢复。生产构建和SSR测试不能代替这一浏览器验收。

## 自动化命令

```powershell
Set-Location 'E:\GPT\2026-10-14\python-ai-agent-banking-agent-llm-2\outputs\Banking-Agent-AI\frontend'
npm.cmd test
.\node_modules\.bin\tsc.cmd --noEmit
npm.cmd run build
```

测试使用Node内置测试、React服务端渲染及模拟fetch，没有新增测试依赖，也不连接业务数据库。原23项保留；原analysisQuery测试仅将转账action字面量收窄以适配区分联合，未修改断言。测试覆盖状态处理、组件内容和API契约，没有模拟完整浏览器事件及真实网络。

本批实际验证（2026-10-21）：前端55/55项通过，其中新增32项；单独TypeScript检查及包含TypeScript检查的Vite生产构建均通过。尚未进行真实DeepSeek联网、浏览器交互或人工冻结验收。

后端397项已由用户在上一批本机验证通过，本批没有修改后端，也没有在已知受限环境重复执行异步回归。如需完整复验：

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
