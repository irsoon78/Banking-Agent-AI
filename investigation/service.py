"""有界、确定性的消费调查；业务表只读，证据只来自受保护查询结果。"""

from copy import deepcopy
from dataclasses import fields
from decimal import Decimal
from functools import wraps
from threading import RLock
from uuid import uuid4

from pydantic import ValidationError

from bill_analysis.service import DISCLAIMER, date_range, public_record
from bill_analysis.storage import AnalysisError, Dataset, SpendingRecord, identifier, utc_boundary, utc_instant, validate_dataset, validate_record
from card_management.storage import Card, CardError, now_stamp, public_card
from database.db import database_path
from investigation.contracts import InvestigationError, InvestigationRequest, RULE_FOCUS, StepCommand, MAX_FINDINGS, MAX_DISCOVERED_RECORDS
from investigation.tools import _ReadOnlyTools
from security.audit import AuditError, AuditLogger


def _id(prefix):
    return prefix + uuid4().hex


def _require(condition, code="EVIDENCE_INVALID"):
    if not condition:
        raise InvestigationError(code, "查询证据不一致，已停止相关分支，未猜测内容或目标。")


def _serial(method):
    @wraps(method)
    def wrapped(self, *args, **kwargs):
        with self._lock:
            return method(self, *args, **kwargs)
    return wrapped


class InvestigationService:
    """account_id由调用方可信Demo上下文注入；本类不是身份认证系统。"""
    def __init__(self, *, account_id, audit_logger=None):
        self._account = identifier(account_id)
        self._logger = audit_logger if audit_logger is not None else AuditLogger()

    def start(self, options):
        """只创建内存调查及开始审计，不自动执行业务查询。"""
        return _InvestigationRun(self._account, InvestigationRequest.model_validate(options), self._logger)

    def run(self, options):
        return self.start(options).run()


class _InvestigationRun:
    def __init__(self, account, options, logger):
        self._lock = RLock()
        self._account, self._options, self._logger = account, options, logger
        self._tools = _ReadOnlyTools(account, logger)
        self._context = logger.context(account, "investigation")
        self._steps, self._records, self._links, self._cards = {}, {}, {}, {}
        self._record_refs, self._card_refs = {}, {}
        self._dataset = None
        self._used, self._stop, self._finished = 0, False, False
        self._result = {
            "schema_version": "investigation.v1", "orchestration": "deterministic", "investigation_id": uuid4().hex,
            "account_id": account, "focus": options.focus, "status": "running",
            "started_at": now_stamp(), "finished_at": None,
            "period": {"start_date": options.start_date, "end_date_exclusive": options.end_date_exclusive, "time_zone": "Asia/Shanghai"},
            "dataset": None, "coverage": None, "analysis_summary": None,
            "discovered_record_ids": [], "unselected_finding_count": 0,
            "findings": [], "errors": [], "limits": [],
            "audit": {"request_id": self._context["request_id"], "event_ids": [], "status": "complete"},
            "disclaimer": DISCLAIMER,
            "safety": {"mode": "read_only_investigation", "business_writes_allowed": False,
                       "note": "本次调查未修改银行卡、资金或消费数据，未创建待确认任务。审计元数据会写入；模拟账户上下文不代表生产级银行认证。"},
        }
        self._add_step("analyze_spending", None, [])
        try:
            event = self._log("investigation_started", self._context, details={"period": self._result["period"], "focus": options.focus})
            self._result["audit"]["event_ids"].append(event)
        except (AuditError, InvestigationError):
            self._audit_failure()

    def _log(self, event, context, *, details):
        # 不让配置漂移把审计写到另一数据库；不在读事务中写审计。
        if database_path().resolve() != self._tools._path:
            raise InvestigationError("DATABASE_CONTEXT_CHANGED", "数据库上下文变化，已停止。")
        return self._logger.log_event(event, context, details={"investigation_id": self._result["investigation_id"], **details})

    def _audit_failure(self):
        self._stop = True
        self._result["audit"]["status"] = "incomplete"
        self._error(None, "AUDIT_UNAVAILABLE", "调查审计未能完整保存，已停止后续查询。")

    def _error(self, step_id, code, message):
        self._result["errors"].append({"step_id": step_id, "code": code, "message": message})

    def _add_step(self, tool, ref, dependencies):
        step_id = _id("step-")
        self._steps[step_id] = {"step_id": step_id, "tool": tool, "evidence_ref": ref,
            "depends_on": list(dependencies), "status": "planned", "started_at": None, "finished_at": None,
            "execution_index": None, "request_id": None, "audit_event_ids": [], "decisions": [], "code": None}
        return step_id

    @_serial
    def ready_steps(self):
        """仅返回可选择的枚举命令和不透明引用；不包含商户原文或可自填参数。"""
        if self._stop or self._finished:
            return []
        return [StepCommand(**{key: s[key] for key in ("step_id", "tool", "evidence_ref")}).model_dump()
                for s in self._steps.values() if s["status"] == "planned" and
                all(self._steps[d]["status"] == "succeeded" for d in s["depends_on"])]

    @_serial
    def planning_view(self):
        """供第二批规划使用的字段白名单；不传商户、卡名、规则原文或工具原始响应。"""
        return deepcopy({"investigation_id": self._result["investigation_id"], "focus": self._options.focus,
            "period": self._result["period"], "ready_steps": self.ready_steps(),
            "findings": [{"evidence_ref": f["evidence_ref"], "rule_id": f["finding"]["rule_id"],
                          "record_refs": f["record_refs"]} for f in self._result["findings"]],
            "records": [{"evidence_ref": r["evidence_ref"], "status": r["status"]} for r in self._records.values()],
            "links": [{k: l[k] for k in ("evidence_ref", "record_ref", "status", "card_ref")} for l in self._links.values()],
            "cards": [{"evidence_ref": c["evidence_ref"], "status": c["status"]} for c in self._cards.values()]})

    @_serial
    def record_planner_event(self, event):
        """仅接收控制器生成的有限诊断，不接受模型原文或任意审计字段。"""
        allowed = {"index", "status", "code", "step_id", "reason_code"}
        _require(isinstance(event, dict) and set(event) <= allowed)
        try:
            event_id = self._log("investigation_plan_selection", self._context, details=event)
            return event_id
        except (AuditError, InvestigationError):
            self._audit_failure()
            return None

    @_serial
    def interrupt(self):
        self._stop = True
        self._result["interrupted"] = True
        self._error(None, "INVESTIGATION_INTERRUPTED", "调查已中断，未自动续跑或重试业务操作。")
        return self.finish()

    @_serial
    def execute_step(self, command):
        """只执行本调查生成的就绪步骤；没有接受raw record_id/card_id的接口。"""
        command = StepCommand.model_validate(command)
        s = self._steps.get(command.step_id)
        if self._finished or self._stop or s is None or s["tool"] != command.tool or s["evidence_ref"] != command.evidence_ref:
            raise InvestigationError("STEP_NOT_ALLOWED", "步骤或证据不属于本次可执行调查。")
        if s["status"] != "planned" or not all(self._steps[d]["status"] == "succeeded" for d in s["depends_on"]):
            raise InvestigationError("DEPENDENCY_NOT_READY", "步骤已消费或依赖尚未核验，未执行查询。")
        if self._used >= self._options.max_tool_calls:
            s.update(status="not_executed", code="TOOL_BUDGET_EXCEEDED")
            if "TOOL_BUDGET_EXCEEDED" not in self._result["limits"]:
                self._result["limits"].append("TOOL_BUDGET_EXCEEDED")
            return self.snapshot()
        params = self._params(s)
        # 每个步骤独立审计上下文，返回真实request_id及审计事件ID。
        with self._logger.request_context(self._account, "只读消费调查", s["tool"]) as context:
            s["request_id"] = context["request_id"]
            try:
                event = self._log("investigation_step_started", context,
                                  details={"step_id": s["step_id"], "depends_on": s["depends_on"], "evidence_ref": s["evidence_ref"]})
                s["audit_event_ids"].append(event)
            except (AuditError, InvestigationError):
                s.update(status="not_executed", code="AUDIT_UNAVAILABLE")
                self._audit_failure()
                return self.snapshot()
            s.update(status="running", started_at=now_stamp())
            self._used += 1  # 保守计入查询尝试；失败、参数或数据集检查不自动重试。
            s["execution_index"] = self._used
            try:
                try:
                    outcome = self._tools.call(s["tool"], params, self._dataset)
                except Exception as exc:
                    s["decisions"] = deepcopy(getattr(exc, "investigation_decisions", []))
                    raise
                s["decisions"] = outcome["decisions"]
                result = outcome["result"]
                if not result["ok"]:
                    raise InvestigationError(result.get("code", "TOOL_QUERY_FAILED"), result.get("error", "只读查询失败。"))
                self._accept(s, result, outcome["dataset"])
                s["status"] = "succeeded"
                if result.get("audit_warning"):
                    self._audit_failure()
            except InvestigationError as exc:
                self._fail(s, exc.code, str(exc))
                if exc.code in {"DATASET_CHANGED", "DATABASE_CONTEXT_CHANGED", "DATASET_AMBIGUOUS"}:
                    self._stop = True
            except AuditError:
                self._fail(s, "AUDIT_UNAVAILABLE", "只读查询的审计失败，已停止。")
                self._audit_failure()
            except (AnalysisError, CardError, ValidationError, KeyError, ValueError, TypeError, ArithmeticError):
                self._fail(s, "EVIDENCE_INVALID", "查询结果无法可靠核验，未采用为证据。")
            except Exception:
                self._fail(s, "TOOL_QUERY_FAILED", "只读查询异常，未自动重试或猜测结果。")
            s["finished_at"] = now_stamp()
            try:
                event = self._log("investigation_step_finished", context,
                    details={"step_id": s["step_id"], "status": s["status"], "code": s["code"], "evidence_ref": s["evidence_ref"]})
                s["audit_event_ids"].append(event)
            except (AuditError, InvestigationError):
                self._audit_failure()
        return self.snapshot()

    def _fail(self, step, code, message):
        step.update(status="failed", code=code)
        ref = step["evidence_ref"]
        bucket = {"get_spending_record": self._records, "get_card_for_spending_record": self._links, "get_card": self._cards}.get(step["tool"])
        if bucket is not None:
            bucket[ref]["status"] = "failed"
        self._error(step["step_id"], code, message)

    def _params(self, s):
        tool, ref = s["tool"], s["evidence_ref"]
        if tool == "analyze_spending":
            return {"period": f"{self._options.start_date}/{self._options.end_date_exclusive}", "view": "anomalies"}
        if tool == "get_spending_record":
            return {"record_id": self._records[ref]["record_id"]}
        if tool == "get_card_for_spending_record":
            return {"record_id": self._records[self._links[ref]["record_ref"]]["record_id"]}
        return {"card_id": self._cards[ref]["card_id"]}

    def _accept(self, step, result, dataset):
        if step["tool"] == "analyze_spending":
            self._accept_analysis(step, result, dataset)
        elif step["tool"] == "get_spending_record":
            value = result["spending_record"]
            ref = step["evidence_ref"]
            raw = value["record"]
            _require(value["account_id"] == self._account and raw["account_id"] == self._account, "ACCOUNT_MISMATCH")
            _require(value["dataset_id"] == self._dataset["dataset_id"] and value["dataset"] == self._dataset, "DATASET_CHANGED")
            _require(raw["record_id"] == self._records[ref]["record_id"] and value["time_zone"] == "Asia/Shanghai")
            _require(isinstance(raw["amount_cents"], str) and raw["amount_cents"].isascii() and raw["amount_cents"].isdigit())
            data = {f.name: raw[f.name] for f in fields(SpendingRecord)}
            data["amount_cents"] = int(raw["amount_cents"])
            record = SpendingRecord(**data)
            validate_record(record, Dataset(**self._dataset))
            _require(public_record(record) == raw)
            self._records[ref].update(status="verified", record=deepcopy(raw), read_at=now_stamp())
        elif step["tool"] == "get_card_for_spending_record":
            self._accept_link(step, result)
        else:
            evidence = self._cards[step["evidence_ref"]]
            _require(result["account_id"] == self._account, "ACCOUNT_MISMATCH")
            card = self._card(result["card"])
            _require(card["card_id"] == evidence["card_id"])
            utc_instant(result["read_at"])
            evidence.update(status="verified", card=card, read_at=result["read_at"])

    def _card(self, raw):
        _require(raw["account_id"] == self._account, "ACCOUNT_MISMATCH")
        card = Card(**{f.name: raw[f.name] for f in fields(Card)})
        value = public_card(card)
        _require(value == raw)
        return value

    def _accept_analysis(self, step, result, dataset):
        report = result["analysis"]
        _require(report["account_id"] == self._account and dataset["account_id"] == self._account, "ACCOUNT_MISMATCH")
        validate_dataset(Dataset(**dataset))
        _require(report["dataset"] == dataset, "DATASET_CHANGED")
        start, end = date_range(self._options.start_date, self._options.end_date_exclusive)
        _require(report["period"] == {"start_date": start.isoformat(), "end_date_exclusive": end.isoformat(),
                 "start_utc": utc_boundary(start), "end_utc_exclusive": utc_boundary(end)} and report["time_zone"] == "Asia/Shanghai")
        _require(type(report["coverage"]["complete"]) is bool and isinstance(report["anomalies"], list))
        _require(len(report["anomalies"]) <= MAX_FINDINGS, "EVIDENCE_LIMIT_EXCEEDED")
        findings = []
        for finding in report["anomalies"]:
            _require(finding["rule_id"] in RULE_FOCUS, "UNSUPPORTED_FINDING_RULE")
            ids = finding["record_ids"]
            _require(isinstance(ids, list) and len(ids) == (2 if finding["rule_id"] == "SIMILAR_PAYMENT_V1" else 1))
            for record_id in ids:
                identifier(record_id)
            _require(len(set(ids)) == len(ids))
            if self._options.focus != "all" and RULE_FOCUS[finding["rule_id"]] != self._options.focus:
                self._result["unselected_finding_count"] += 1
                continue
            if finding not in findings:
                findings.append(deepcopy(finding))
        ids = list(dict.fromkeys(record_id for f in findings for record_id in f["record_ids"]))
        _require(len(ids) <= MAX_DISCOVERED_RECORDS, "EVIDENCE_LIMIT_EXCEEDED")
        self._dataset = deepcopy(dataset)
        self._result.update(dataset=deepcopy(dataset), coverage=deepcopy(report["coverage"]), analysis_summary=deepcopy(report["summary"]))
        self._result["discovered_record_ids"] = ids
        if len(ids) > self._options.max_records:
            self._result["limits"].append("RECORD_BUDGET_EXCEEDED")
        for record_id in ids[:self._options.max_records]:
            ref, link_ref = _id("evidence-"), _id("evidence-")
            self._record_refs[record_id] = ref
            read = self._add_step("get_spending_record", ref, [step["step_id"]])
            link = self._add_step("get_card_for_spending_record", link_ref, [read])
            self._records[ref] = {"evidence_ref": ref, "record_id": record_id, "source_step_id": step["step_id"],
                                  "step_id": read, "status": "discovered", "record": None, "read_at": None, "link_ref": link_ref}
            self._links[link_ref] = {"evidence_ref": link_ref, "record_ref": ref, "step_id": link,
                                     "status": "not_executed", "linked": None, "card_ref": None}
        for finding in findings:
            self._result["findings"].append({"evidence_ref": _id("evidence-"), "source_step_id": step["step_id"],
                "finding": finding, "record_refs": [self._record_refs[i] for i in finding["record_ids"] if i in self._record_refs]})

    def _accept_link(self, step, result):
        evidence = self._links[step["evidence_ref"]]
        record = self._records[evidence["record_ref"]]["record"]
        _require(result["account_id"] == self._account, "ACCOUNT_MISMATCH")
        _require(result["dataset_id"] == self._dataset["dataset_id"], "DATASET_CHANGED")
        _require(result["record_id"] == record["record_id"] and result["payment_instrument_ref"] == record["payment_instrument_ref"])
        _require(type(result["linked"]) is bool)
        utc_instant(result["read_at"])
        if not result["linked"]:
            _require(result["card"] is None and result["reason_code"] in {"NO_MAPPING", "NO_PAYMENT_INSTRUMENT"})
            evidence.update(status="verified", linked=False, reason_code=result["reason_code"], read_at=result["read_at"])
            return
        _require(record["payment_instrument_ref"] is not None and result["reason_code"] == "LINKED")
        card = self._card(result["card"])
        card_id = card["card_id"]
        if card_id not in self._card_refs:
            ref = _id("evidence-")
            self._card_refs[card_id] = ref
            card_step = self._add_step("get_card", ref, [step["step_id"]])
            self._cards[ref] = {"evidence_ref": ref, "card_id": card_id, "source_link_refs": [],
                                "step_id": card_step, "status": "discovered", "card": None, "read_at": None}
        ref = self._card_refs[card_id]
        self._cards[ref]["source_link_refs"].append(evidence["evidence_ref"])
        card_step = self._steps[self._cards[ref]["step_id"]]
        if card_step["status"] == "planned" and step["step_id"] not in card_step["depends_on"]:
            card_step["depends_on"].append(step["step_id"])
        evidence.update(status="verified", linked=True, card_ref=ref, reason_code="LINKED", read_at=result["read_at"],
                        payment_instrument_ref=result["payment_instrument_ref"])

    def _record_complete(self, ref):
        record = self._records[ref]
        link = self._links[record["link_ref"]]
        return record["status"] == "verified" and link["status"] == "verified" and (
            link["linked"] is False or self._cards[link["card_ref"]]["status"] == "verified")

    @_serial
    def snapshot(self):
        """返回副本；调用方修改JSON不能改变后续步骤的可信状态。"""
        result = deepcopy(self._result)
        # 实际执行顺序与预先创建节点的顺序可能不同；未执行节点明确排在后面。
        result["steps"] = deepcopy(sorted(self._steps.values(), key=lambda s: (s["execution_index"] is None, s["execution_index"] or 0)))
        result["evidence"] = {"records": deepcopy(list(self._records.values())), "links": deepcopy(list(self._links.values())), "cards": deepcopy(list(self._cards.values()))}
        verified = sum(r["status"] == "verified" for r in self._records.values())
        completed = sum(self._record_complete(ref) for ref in self._records)
        result["budget"] = {"max_records": self._options.max_records, "max_tool_calls": self._options.max_tool_calls,
                            "tool_attempts": self._used, "discovered_records": len(result["discovered_record_ids"]),
                            "selected_records": len(self._records), "verified_records": verified, "completed_records": completed,
                            "unfinished_records": len(result["discovered_record_ids"]) - completed,
                            "record_limit_omitted": max(0, len(result["discovered_record_ids"]) - len(self._records))}
        for finding in result["findings"]:
            ids = finding["finding"]["record_ids"]
            ready = all(i in self._record_refs and self._record_complete(self._record_refs[i]) for i in ids)
            finding["status"] = "verified" if ready else "discovered"
            # 仅计算已核验记录的时间差，不重新实现异常规则，不将命中当成事实定性。
            if len(ids) == 2 and all(i in self._record_refs and self._records[self._record_refs[i]]["status"] == "verified" for i in ids):
                first, last = [self._records[self._record_refs[i]]["record"] for i in ids]
                delta = utc_instant(last["occurred_at"]) - utc_instant(first["occurred_at"])
                ms = delta.days * 86400000 + delta.seconds * 1000 + delta.microseconds // 1000
                finding["observed_interval_milliseconds"] = ms
                finding["observed_interval_minutes"] = str(Decimal(ms) / Decimal(60000))
        result["coverage_status"] = ("unknown" if result["coverage"] is None else
                                     "complete" if result["coverage"]["complete"] else "incomplete")
        if result.get("interrupted"):
            result["execution_status"] = "interrupted"
        elif result["status"] == "running":
            result["execution_status"] = "running"
        elif self._dataset is None:
            result["execution_status"] = "failed"
        else:
            result["execution_status"] = "partial" if result["errors"] or result["limits"] or result["budget"]["unfinished_records"] else "completed"
        return result

    @_serial
    def finish(self):
        if self._finished:
            return self.snapshot()
        for step in self._steps.values():
            if step["status"] == "planned":
                blocked = any(self._steps[d]["status"] != "succeeded" for d in step["depends_on"])
                step.update(status="skipped" if blocked else "not_executed",
                            code="DEPENDENCY_FAILED" if blocked else "INVESTIGATION_STOPPED")
        snapshot = self.snapshot()
        analyzed = self._dataset is not None
        partial = (snapshot["errors"] or snapshot["limits"] or snapshot["budget"]["unfinished_records"] or
                   (snapshot["coverage"] is not None and not snapshot["coverage"]["complete"]))
        self._result.update(status="failed" if not analyzed else "partial" if partial else "completed", finished_at=now_stamp())
        try:
            event = self._log("investigation_finished", self._context,
                              details={"status": self._result["status"], "tool_attempts": self._used, "unfinished_records": snapshot["budget"]["unfinished_records"]})
            self._result["audit"]["event_ids"].append(event)
        except (AuditError, InvestigationError):
            self._audit_failure()
            self._result["status"] = "partial" if analyzed else "failed"
        self._finished = True
        return self.snapshot()

    @_serial
    def run(self):
        while commands := self.ready_steps():
            for command in commands:
                if self._stop:
                    break
                self.execute_step(command)
        return self.finish()
