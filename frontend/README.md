# Banking Agent AI 比赛演示前端

React + TypeScript + Vite，使用 HashRouter、Context + useReducer、原生 fetch 和普通 CSS。正式业务请求全部连接现有 FastAPI；没有 mock 或浏览器端密钥。

## 本地运行

在项目根目录启动现有后端（单 worker；无需修改源码或 `.env`）：

```powershell
.\.venv\Scripts\python.exe -m uvicorn main:app --host 127.0.0.1 --port 8000
```

另一个终端：

```powershell
cd frontend
npm install
npm run build
npm run dev
```

访问 http://127.0.0.1:5173/#/home 。其余页面为 `#/chat`、`#/risk`、`#/audit`。

若 PowerShell 执行策略阻止 npm.ps1，使用 `npm.cmd`。若终端找不到本机 npm，可在当前终端临时设置：

```powershell
$env:Path = 'C:\Users\Irsoon\nodejs;' + $env:Path
npm.cmd --version
```

默认 API base 为 `/api`。Vite dev/preview 将 `/api/*` 代理到 `http://127.0.0.1:8000/*`，剥离 `/api` 前缀。可以在启动/构建终端设置 `$env:VITE_API_BASE_URL = '/api'`，不必创建 `.env`。直接指定跨域后端地址时，需要部署环境提供 CORS。`npm run preview` 用于检查本地构建；生产部署仍需 Web 服务器为 `/api` 提供同等反向代理。

## 操作及保护

- 账户固定为演示账户 `1001`；余额、户名、账单均从后端读取，不预设初始余额。
- 示例中的金额仅是用户指令模板；结果、风险与转账凭据只使用后端结构化数据。
- 待确认任务在 Provider 内存中保存，切换页面保留。刷新浏览器不恢复；后端重启也会丢失任务。
- 有待确认任务时仍可聊天和查询；服务端会阻止同一会话的新转账，前端单独保存原任务的决策、参数与审计编号，不能用新消息覆盖。“我确认了”不会执行任务。
- 普通回复通过 `response_kind` 展示为蓝色对话/能力/澄清/不支持卡，不生成交易凭据。`conversation_id` 由服务端返回并在后续消息中传递，最多保留最近 6 次交流、空闲 20 分钟过期；同一后端进程最多 128 个会话，重启丢失。
- 会话过期或后端重启后不自动重发；没有待确认任务及未知结果锁定时，可以点击“新对话”。服务端会话不保存工具金融结果，账户事实仍需重新查询。
- 确认/取消只发送账号与 action_id；参数只读。同步锁防止同一轮连续点击，按钮在请求期间禁用。
- 红色操作不提供确认入口。没有伪造的验证通过功能，也没有模拟 Agent 思考或实时执行事件。
- HTTP 错误、业务 failed、partial、needs_input、execution_unknown 分别展示。未知结果及 POST 通信异常锁定后续资金操作，不自动重试；先核对首页账单及审计。锁定持续到本页会话结束，刷新不能作为重新转账的依据。
- `audit_warning` 保留已返回的执行结果；账户刷新失败明确提示旧数据，不能把刷新失败当成交易失败。确认/取消后的刷新不会复用交易前的在途读取，旧结果不能覆盖新数据。
- `audit_id` 是 request_id 别名。审计页支持筛选和后端分页；每页按真实事件 id 正序展示。全部历史默认是最近 100 条，使用“更早的记录”翻页。
- 首页余额/账单 GET 也会由后端写审计事件；审计 GET 不生成额外审计。页面不会轮询或自动重发业务请求。

## 检查

`npm run build` 执行严格 TypeScript 检查并构建；`npm test` 验证状态在路由切换之外的生命周期、取消与未知结果语义及真实审计排序。真实端到端验收通过浏览器连接实际后端进行，必须单独记录，不以这些测试代替。
