# 前端 V0.1 验收记录

日期：2026-10-18（Asia/Shanghai）。分支：`feature/frontend-v0.1`。

环境：Windows 本机 `C:\Users\Irsoon\nodejs\node.exe` v24.22.0、npm 11.19.1。没有安装或升级系统 Node/npm。

前端通过 Vite `/api` 代理真实连接 `http://127.0.0.1:8000`。后端使用项目现有虚拟环境运行，健康检查返回 `ok`、`llm_configured: true`。没有 mock 正式 API。后端源码、`.env` 和 Key 未修改；余额/账单/审计仅通过真实演示 API 自然更新，没有手工写库或重置演示数据。

## 20 项验收

| # | 验收项 | 结果与证据 |
| --- | --- | --- |
| 1 | 首页读取账户 1001 | 通过；浏览器展示账户 1001、张三 |
| 2 | 首页读取余额 | 通过；初次为后端实际 9500.00 CNY，并非硬编码 |
| 3 | 首页读取账单 | 通过；展示历史转出和初始入账，转账后新增真实账单 |
| 4 | AI 查询余额成功 | 通过；真实 secure API 返回 completed |
| 5 | 查询显示 green | 通过；风险卡显示绿色、allow、自动执行 |
| 6 | 展示 get_balance 工具结果 | 通过；显示实际工具名、工具原始结果与余额 |
| 7 | 500 元转账进入 yellow | 通过；真实响应 pending、pending_confirmation |
| 8 | 黄色任务展示 pending_action | 通过；500.00 元、1001 → 1002、action_id、创建时间均为后端快照 |
| 9 | 确认调用 /confirm | 通过；浏览器双击确认，后端仅收到一次确认请求 |
| 10 | 显示真实 transfer_id | 通过；458f23ea3c5c453ebf62b2b7e123b3ac |
| 11 | 确认后余额刷新 | 通过；凭据、账户卡及代理 API 均为 9000.00 CNY，账单新增对应交易 |
| 12 | 取消调用 /cancel | 通过；浏览器双击取消，后端仅收到一次取消请求 |
| 13 | 取消显示未执行 | 通过；显示“操作已取消，未执行”，风险页没有交易凭据；审计无 tool_executed |
| 14 | 5000 元进入 red | 通过；require_verification、大额转账需要强验证 |
| 15 | 红色没有确认按钮 | 通过；风险页确认按钮数为 0，后端无 pending_created 或 tool_executed |
| 16 | 对应 audit_id 可查询 | 通过；成功、取消、阻断三条真实链均取得对应事件 |
| 17 | 真实审计正序展示 | 通过；成功链 DOM 事件 ID 为 125、126、127、128、129、155、156、157 |
| 18 | 切页保留待确认任务 | 通过；黄色任务切换首页再进入风险页后仍为原 action_id |
| 19 | 重复点击保护 | 通过；发送、确认、取消均执行双击测试；对应聊天/确认/取消入站请求各一次，转账工具只执行一次 |
| 20 | npm run build | 通过；严格 TypeScript 检查与 Vite 生产构建完成 |

`npm install` 成功（75 个依赖包，审计报告 0 vulnerabilities）；默认缓存写入受限后，仅将本次命令缓存改至项目已忽略的 `.cache/npm`。`npm run dev` 在 127.0.0.1:5173 成功启动。最终构建重新验证通过，`npm test` 5/5 通过。浏览器最终查询无 error/warn 控制台记录。另检查桌面及移动宽度布局，页面无横向溢出。

## 真实审计证据

- 绿色查询：`42fe19712d394b1ab97c37c86b1e9048`。最终代码再次查询：`ee599ffa26ca40548111b1e030f2a597`，余额 9000.00。
- 黄色确认：audit_id `0b302ca1d6644dc7b2b16ddbd4b7a813`，action_id `640bbb8470b2400586d7bbb4f9fa3f99`。
- 黄色取消：audit_id `7e8d5baf32e64b33a2602a396ed2c85e`，action_id `b8b8c1e2669e4852bed72211d1aabf7a`。
- 红色阻断：audit_id `79ab001e30184c8f9fccd1a71c42fa6c`。

确认链真实事件：request_received → risk_evaluated → permission_decided → pending_created → request_returned → confirmation_confirmed → tool_execution_started → tool_executed。

取消链真实事件：request_received → risk_evaluated → permission_decided → pending_created → request_returned → confirmation_cancelled。执行事件数为 0。

红色链真实事件：request_received → risk_evaluated → permission_decided → request_returned。待确认和执行事件数均为 0。

最终演示账户余额为 9000.00 CNY：本次验收实际新增一笔 500.00 元模拟转账，取消和红色操作没有再次扣款。

## 验证边界

没有为了制造异常去修改后端、替换 LLM 或 mock 正式 API。failed、needs_input、partial、execution_unknown、审计写入故障、真实网络中断没有全部进行端到端故障注入；相关展示和保护已实现，未知结果持久锁定、取消无凭据、任务失效等状态语义由 5 项离线 reducer 测试覆盖。20 项通过不代表这些额外异常场景已实测。

浏览器刷新恢复、后端任务持久化、多 worker、登录和红色验证放行均超出本次范围。
