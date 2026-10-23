"""服务端只读调查核心。模型选择协议、快照读取与业务写入权限隔离。"""

from investigation.contracts import InvestigationError, InvestigationRequest, StepCommand
from investigation.service import InvestigationService

__all__ = ["InvestigationService", "InvestigationRequest", "StepCommand", "InvestigationError"]
