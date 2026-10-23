"""调查专用适配：闭合的四工具集合，复用现有带审计的只读调度。"""

from copy import deepcopy
import json

from api.responses import decision_scope
from bill_analysis.service import AnalysisService
from bill_analysis.storage import AnalysisError
from database.db import database_path
from investigation.contracts import InvestigationError, READ_TOOLS


class _ReadOnlyTools:
    def __init__(self, account_id, logger):
        # 惰性复用已有受保护入口；不启动FastAPI lifespan，不初始化数据库或模型。
        from main import ARGUMENT_MODELS, audited_execute_tool
        self._models = {name: ARGUMENT_MODELS[name] for name in READ_TOOLS}
        self._dispatch = audited_execute_tool
        self._account, self._logger = account_id, logger
        self._path = database_path().resolve()
        if not self._path.is_file():
            raise InvestigationError("DATABASE_UNAVAILABLE", "调查数据库不存在，未创建新数据库。")

    def _dataset(self):
        if database_path().resolve() != self._path:
            raise InvestigationError("DATABASE_CONTEXT_CHANGED", "调查数据库上下文变化，已停止。")
        try:
            return AnalysisService(self._path, account_id=self._account).current_dataset()
        except AnalysisError as exc:
            raise InvestigationError(exc.code, str(exc)) from None

    def call(self, name, args, expected_dataset):
        if name not in READ_TOOLS:
            raise InvestigationError("TOOL_NOT_ALLOWED", "调查执行器只允许四项明确的只读查询。")
        # 参数仍经过原工具的strict模型；这里不接受风险、账户、许可或SQL。
        params = self._models[name].model_validate(args).model_dump()
        before = self._dataset()
        if expected_dataset is not None and before != expected_dataset:
            raise InvestigationError("DATASET_CHANGED", "调查数据集、版本或覆盖信息变化，未混合证据。")
        with decision_scope() as decisions:
            try:
                result = self._dispatch(name, json.dumps(params, ensure_ascii=False), self._account, self._logger)
                after = self._dataset()
                if before != after:
                    raise InvestigationError("DATASET_CHANGED", "查询期间数据集信息变化，结果未作为调查证据。")
                if not isinstance(result, dict) or type(result.get("ok")) is not bool:
                    raise InvestigationError("INVALID_TOOL_RESULT", "查询未返回合法结构化结果。")
                if result["ok"] and (len(decisions) != 1 or decisions[0]["operation"] != name or
                                     decisions[0]["risk_level"] != "green" or decisions[0]["permission_action"] != "allow"):
                    raise InvestigationError("READ_PERMISSION_REQUIRED", "未取得实际绿色只读权限决策，结果未采用。")
            except Exception as exc:
                # 数据集漂移/事后审计失败不能抹去已经发生的真实安全决策。
                exc.investigation_decisions = deepcopy(decisions)
                raise
        return {"result": deepcopy(result), "decisions": deepcopy(decisions), "dataset": after}
