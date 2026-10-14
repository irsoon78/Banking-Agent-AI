# Banking-Agent-AI 开发与 Git 规则

## 稳定基线

- 当前稳定版本为 V0.1.2，状态为“首次 Agent 闭环成功”。
- `main` 和标签 `v0.1.2` 保留当前稳定源码，作为 V0.2 的回归基线。
- 不在 `main` 上继续提交，不重写其历史；不移动或删除稳定标签。
- 不执行针对稳定基线的 amend、rebase、reset 或强制推送。

## V0.2 工作分支

- 所有 V0.2 源码、配置、测试及文档修改必须在 `feature/security-v0.2` 上完成。
- 编辑前执行 `git status --short --branch`、`git diff` 和 `git diff --cached`。
- 若当前分支不是 `feature/security-v0.2`，先确认工作区状态，再切换到已有开发分支。
- 不覆盖、丢弃或代为提交用户与本次功能无关的修改。

## 提交

- 每完成一个明确功能，验证并单独提交；不要把多个无关功能合成一个提交。
- 提交消息使用 `feat: 描述`、`fix: 描述` 或 `refactor: 描述`，例如：
  - `feat: 添加风险判断模块`
  - `fix: 修复DeepSeek工具调用问题`
  - `refactor: 重构权限流程`
- 只暂存明确审阅的文件；提交前检查暂存 diff、执行 `git diff --cached --check`。
- 禁止提交 `.env` 及其实际配置变体、`.venv`、数据库、API Key、私钥和缓存文件。
- `.env.example` 可以提交，但其中必须只包含空值或明显的占位符，不能包含真实密钥。
- `.gitignore` 不能阻止源码中的硬编码密钥；每次提交仍须检查暂存内容，发现疑似密钥时停止提交，不打印密钥值。

## 回归与停止条件

- Windows 回归命令：`.\.venv\Scripts\python.exe -m unittest discover -s tests -v`。
- macOS/Linux 回归命令：`.venv/bin/python -m unittest discover -s tests -v`。
- 测试使用临时数据库和模拟 LLM；不得改动用户的运行数据库或使用真实 Key 执行测试转账。
- 如修改导致已有 V0.1 的余额、账单、模拟转账或 Agent 闭环测试失败，立即停止后续 V0.2 开发并报告失败用例、相关修改和错误原因。
- 不自动回滚或覆盖用户的工作，不把功能失败报告为完成。
- 每次完成后报告当前分支、commit 记录、修改文件和测试结果。
