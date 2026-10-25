# v0.4.1 交付修订发布说明

日期：2026-10-25（Asia/Shanghai）。本次是比赛Demo交付修订，不新增业务功能，不调整风险、权限、确认或资金/卡片执行规则。

## 基线与版本含义

- 原稳定标签 `v0.4.0-cross-scenario-mvp` 保留，指向 `6f36a32cb10ef0a1381f8d385ff36678590cce2d`。
- 发布准备起点为 `f4349a8010a30e2baafa54b825e41fd156263fac`，它仅在原稳定版本上更新README。
- 本次整体发布版本/新标签名为 `v0.4.1`。FastAPI组件版本0.3.0、前端package及锁文件0.1.0、页眉V0.3.0保留，不用文档发布号覆盖组件含义。
- 无业务源码、测试断言、requirements或package-lock变更。标签与提交最终绑定关系用Git查询，不在提交自身文档硬编码自身SHA。

## 本次真实变化

1. 根README移除开发者绝对路径，部署命令改用明确的项目根目录占位符，说明Python依赖要求、组件版本和本次验证口径。
2. docs中的本机路径通用化，旧验收/未提交状态增加历史记录标识；保留原始验收事实，不将旧数字当作当前结果。
3. 修正账单分析页地址为Hash路由 `#/analysis`。
4. 前端README纠正账户切换、卡片会话确认和调查历史说明，使用锁文件安装及现有只读/安全API。
5. 独立源码包按“受跟踪文件＋本发布说明”白名单构造；不是对开发目录直接压缩。保留.env.example及frontend/package-lock.json。
6. 没有重置或初始化已有演示库，没有真实模型调用和真实金融业务测试。

所有 `<项目根目录>` 均需用户替换为实际克隆/解压目录；不依赖某位开发者的盘符或用户名。

## 本次开发仓库验证

环境：Windows、Python 3.14.7、Node.js 24.22.0、npm 11.19.1。直接Python依赖按requirements.txt核对；锁文件解析到Vite 7.3.7、TypeScript 5.9.3。直接依赖整体要求Python>=3.10；Vite要求Node ^20.19.0或>=22.12.0，测试另外使用Node strip-types，其他版本组合未逐一实测。

| 实际命令（对应目录） | 结果 | 耗时 |
| --- | --- | --- |
| `.\.venv\Scripts\python.exe -B -m unittest discover -s tests -v`（根目录） | 539通过，0失败、0错误、0跳过 | unittest 60.220秒；命令62.866秒 |
| `npm.cmd ci --no-audit --no-fund`（frontend） | 成功，按原锁文件安装75包，锁文件不变 | 14.695秒 |
| `npm.cmd test`（frontend） | 93通过，0失败、0取消、0跳过 | Node 851.4951毫秒；命令1.228秒 |
| `.\node_modules\.bin\tsc.cmd --noEmit`（frontend） | 通过，退出码0 | 4.685秒 |
| `npm.cmd run build`（frontend） | 通过，含TypeScript检查；63模块 | 命令4.673秒；Vite 805毫秒 |

外置验证脚本曾在前端测试通过后打印Unicode字符时发生GBK编码异常；该脚本在仓库外修复为UTF-8，然后继续执行原先未开始的类型检查及构建。没有修改业务代码、测试或断言，也没有把脚本故障记作测试通过。

npm ci保留默认安装脚本策略，提示esbuild postinstall未获allowScripts批准；没有自行批准安装脚本或升级npm。实际前端测试和构建均成功。关闭npm联网audit/fund不是安全审计结论；本次没有第三方安全审计。

后端使用模拟模型/SDK、MockTransport和独立临时SQLite，TEMP/TMP/TMPDIR及BANK_DB_PATH只传给测试进程；用无效测试Key占位避免载入真实Key。测试与打包未启动已有业务库。前端使用模拟fetch、React静态渲染及共享状态控制器；不等于真实浏览器端到端验收。

## 解压候选包独立验证

已从候选1压缩包解压到独立目录，未复制开发虚拟环境或node_modules；源代码、配置、锁文件、测试逐文件SHA-256与候选清单一致。

| 解压目录实际命令 | 结果 | 耗时 |
| --- | --- | --- |
| `python -m venv .venv`；`.\.venv\Scripts\python.exe -B -m ensurepip --upgrade` | 初次ensurepip失败；切换进程级独立TEMP/TMP/TMPDIR后修复新环境，开发环境未变 | 保留失败事实，不计为测试通过 |
| `.\.venv\Scripts\python.exe -m pip install --retries 0 --timeout 15 -r requirements.txt` | 全新虚拟环境安装成功 | 25.728秒 |
| `.\.venv\Scripts\python.exe -B -m unittest discover -s tests -v` | 539通过，0失败、0错误、0跳过 | unittest 52.393秒；命令54.435秒 |
| `npm.cmd ci --no-audit --no-fund` | 成功，75包，原锁文件不变 | 6.816秒 |
| `npm.cmd test` | 93通过，0失败、0取消、0跳过 | Node 861.9445毫秒；命令1.207秒 |
| `.\node_modules\.bin\tsc.cmd --noEmit` | 通过 | 1.471秒 |
| `npm.cmd run build` | 通过；63模块 | 命令2.631秒；Vite 738毫秒 |

初次虚拟环境自举失败后，没有删除或覆盖其他环境；仅在本次新环境中完成ensurepip。pip首次尝试因自举未完成而报No module named pip，后续独立安装成功，不隐藏该环境问题。

最终源码包在候选1之后仅补入文档修订、真实验证记录及有效PowerShell示例；所有可执行源码、测试、配置和锁文件保持相同哈希。最终包再次解压、扫描并核对清单；终包确认回归的实际输出单独保存在包外交付记录，不把不同环境耗时合并。Python仅锁定直接依赖，传递依赖由安装器解析，未宣称存在完整Python锁文件；frontend/package-lock.json完整保留。

## 源码包规则与完整性

- 压缩包名称 `Banking-Agent-AI-v0.4.1.zip`，顶层目录 `Banking-Agent-AI/`。
- 仅源文件、配置模板、锁文件、测试和必要文档进入包；白名单之外的缓存/运行数据不复制。
- 排除.env及其实际配置、数据库及wal/shm、.venv/venv、node_modules、dist、.cache、__pycache__、pyc、日志、.git、嵌套压缩包及临时文件。
- .env.example的DEEPSEEK_API_KEY为空。禁入路径、疑似密钥及个人绝对路径扫描必须在最终包再次通过。
- 包大小、文件数量、逐文件清单及最终SHA-256写在包外校验记录和交付报告，避免在包内嵌入自身哈希造成循环。
- 本包不包含演示数据库。首次离线创建与已有演示库一致备份采用根README及调查演示说明，绝不通过覆盖库来恢复“冻结版本3”等历史状态。

## GitHub发布状态

本次Git查询origin时，连接github.com:443失败（约21秒）。已停止远程发布尝试；本地准备/验证不代表分支、标签或附件已经发布到GitHub。当前环境未发现可用的GitHub CLI，未创建GitHub Release。最终本地提交/标签以交付报告及Git为准，远端恢复后另行核对并普通推送，禁止强推或覆盖旧标签。

## 已知限制与未验证事项

- 模拟账号、卡片、消费不是实际银行服务；没有生产级登录/持卡人认证、强验证放行或真实支付拦截。
- 转账按北京时间业务日累计<=1000元黄色，超过1000元红色阻断；卡片临时状态黄色确认，挂失/解挂红色阻断，规则不变。
- 10月消费样本仅覆盖1—20日；相似/大额规则只是核对线索，不能认定盗刷或重复扣款。
- 普通自然语言仍有识别与上下文限制；调查只允许受控只读步骤，没有自动处置、后台断点续跑或多实例恢复。
- 没有新增真实DeepSeek、浏览器、生产部署、其他OS/版本兼容或第三方渗透测试；2026-10-23真实模型人工验收作为历史证据保留。
