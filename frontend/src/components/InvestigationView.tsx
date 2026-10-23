import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { banking } from "../api/banking";
import type { CardResponse, InvestigationReport as Report } from "../api/types";
import { LatestRead } from "../state/latestRead";
import { displayAmount, SAMPLE_NOTICE } from "../state/analysisQuery";
import { coverageLabel, executionLabel, focusLabel, investigationCoveragePeriod, plannerLabel, readInvestigation, stepLabel, validateInvestigation, validateLatestCard } from "../state/investigationRead";
import type { InvestigationReadView } from "../state/investigationRead";
import { CardSummary } from "./CardView";

const auditPath = (id: string) => `/audit?request_id=${encodeURIComponent(id)}`;
const time = (value?: string | null) => value && Number.isFinite(Date.parse(value)) ? new Date(value).toLocaleString("zh-CN", { timeZone: "Asia/Shanghai", hour12: false }) + "（北京时间）" : "未提供";

export function LatestInvestigationCard({ account, cardId }: { account: string; cardId: string }) {
  const reader = useRef(new LatestRead());
  const scope = `${account}:${cardId}`;
  const [view, setView] = useState<InvestigationReadView<CardResponse> | null>(null);
  useEffect(() => { const current = reader.current; return () => current.invalidate(); }, [scope]);
  const current = view?.scope === scope ? view : null;
  return <div className="investigation-live-card">
    <button className="button secondary" disabled={current?.loading} onClick={() => void readInvestigation(reader.current, scope,
      () => banking.card(account, cardId), data => validateLatestCard(data, account, cardId), setView)}>
      {current?.loading ? "正在查询最新状态…" : "查询最新卡片状态"}
    </button>
    {current?.error && <p role="alert" className="text-red">{current.error} 历史快照不会被当成最新查询结果。</p>}
    {current?.data?.card && <div className="investigation-current"><strong>单独查询结果 · 仅代表此次读取时的状态</strong>
      <p>读取时间 {time(current.data.read_at)}</p><CardSummary card={current.data.card} />
      <Link className="text-link" to={auditPath(current.data.audit_id)}>查看最新状态查询审计</Link></div>}
  </div>;
}

export function InvestigationView({ report, account, historical = false }: { report: Report; account: string; historical?: boolean }) {
  try { validateInvestigation(report, account); }
  catch (error) { return <p className="alert tone-red" role="alert">{error instanceof Error ? error.message : "调查报告无法展示。"}</p>; }
  const records = report.evidence?.records ?? [];
  const links = report.evidence?.links ?? [];
  const cards = report.evidence?.cards ?? [];
  const steps = report.steps ?? [];
  const findings = report.findings ?? [];
  return <section className="investigation-report" aria-label="跨场景消费调查报告">
    <div className="row"><h3>{historical ? "历史调查报告" : "跨场景消费调查"}</h3><span className="badge tone-blue">只读调查</span></div>
    <p className="muted">调查ID <code>{report.investigation_id}</code> · 模拟账户 {report.account_id}</p>
    <div className="investigation-statuses">
      <span className={`badge tone-${report.execution_status === "completed" ? "green" : report.execution_status === "failed" ? "red" : "yellow"}`}>{executionLabel(report.execution_status)}</span>
      <span className={`badge tone-${report.coverage_status === "complete" ? "green" : "yellow"}`}>{coverageLabel(report.coverage_status)}</span>
      <span className="badge tone-blue">{plannerLabel(report.planner_mode)}</span>
    </div>
    <dl><div><dt>调查范围</dt><dd>{report.period ? `${report.period.start_date} 至 ${report.period.end_date_exclusive}（结束日不含）· ${report.period.time_zone}` : "时间范围未提供"}</dd></div>
      <div><dt>调查重点</dt><dd>{focusLabel(report.focus)}</dd></div>
      <div><dt>报告开始／结束</dt><dd>{time(report.started_at)} ／ {time(report.finished_at)}</dd></div>
      <div><dt>数据来源与版本</dt><dd>{report.dataset ? <>独立模拟消费样本 · <code>{report.dataset.dataset_id}</code> · 版本{report.dataset.version} · {report.dataset.source}</> : "尚未取得可信数据集"}</dd></div>
      {report.dataset && <div><dt>数据集覆盖区间</dt><dd>{report.dataset.coverage_start} 至 {report.dataset.coverage_end_exclusive}（结束日不含）</dd></div>}
      <div><dt>本次调查的样本覆盖</dt><dd>{investigationCoveragePeriod(report)}</dd></div>
    </dl>
    <p className="alert tone-blue">{SAMPLE_NOTICE} 银行卡仅为模拟数据，不代表已阻断真实支付；调查ID不是操作授权凭据。</p>
    {report.coverage && <p className={`alert tone-${report.coverage_status === "complete" ? "neutral" : "yellow"}`}>{report.coverage.note} 请求{report.coverage.requested_days}天，样本覆盖{report.coverage.covered_days}天。</p>}
    {report.coverage_status !== "complete" && <p className="muted">执行状态与覆盖状态独立；步骤完成不代表整段数据完整，未覆盖日期不能视为没有异常。</p>}
    {report.planner_mode === "deterministic_fallback" && <p className="alert tone-yellow">实际采用确定性降级，不代表AI自主规划成功。降级原因：<code>{report.planning?.fallback_reason ?? "未提供"}</code>。</p>}
    {report.planner_mode === "deterministic_no_choice" && <p className="muted">没有需要模型选择的独立就绪分支；未宣称模型自主规划。</p>}
    {historical && <p className="alert tone-blue">从已保存快照恢复，未调用模型、未重新调查。快照更新时间 {time(report.recovery?.updated_at)}；修订号 {report.recovery?.revision ?? "未提供"}。</p>}
    {report.persistence_status === "failed" && <p role="alert" className="alert tone-red">报告保存失败，不能保证通过历史查询恢复。</p>}
    {report.audit?.status === "incomplete" && <p role="alert" className="alert tone-red">调查审计未完整保存，不能宣称完整成功。</p>}
    {!!report.errors?.length && <ul className="investigation-errors">{report.errors.map((error, i) => <li key={i}><code>{error.code}</code>：{error.message}{error.step_id && <> · 步骤{error.step_id}</>}</li>)}</ul>}
    {!!report.limits?.length && <p className="alert tone-yellow">达到调查限制：{report.limits.join("、")}。未处理记录 {report.budget?.unfinished_records ?? "未提供"} 条。</p>}

    <section className="investigation-section"><h4>异常规则与核对线索</h4>
      <p>命中相似消费规则，不代表已经证实重复扣款或盗刷。{report.disclaimer}</p>
      {!findings.length && <p className="empty">{report.execution_status === "completed" ? "已覆盖样本内未命中本次规则，不代表未覆盖日期没有异常。" : "尚未取得可展示的异常规则证据，不能据此断言没有异常。"}</p>}
      {findings.map((item, i) => <article className="investigation-evidence" key={item.evidence_ref ?? i}>
        <div className="row"><strong>{item.finding?.rule_id ?? "规则未提供"}</strong><span className="badge tone-blue">{item.status === "verified" ? "证据链已核验" : "已发现线索 · 尚未全部核验"}</span></div>
        <p>{item.finding?.reason ?? "原因未提供"}</p><p>原始记录：{item.finding?.record_ids?.join("、") || "未提供"}</p>
        {item.observed_interval_minutes != null && <p>服务端核验的两笔时间差：{item.observed_interval_minutes}分钟</p>}
        <small>证据引用 <code>{item.evidence_ref}</code> · 来源步骤 <code>{item.source_step_id}</code></small>
      </article>)}
    </section>

    <details className="investigation-section" open><summary>原始消费证据（{records.length}条候选）</summary>
      {!records.length && <p className="empty">没有原始消费证据。</p>}
      {records.map((e, i) => {
        const record = e.status === "verified" ? e.record : null;
        const link = links.find(link => link.record_ref === e.evidence_ref);
        const card = link?.status === "verified" && link.linked ? cards.find(card => card.evidence_ref === link.card_ref) : null;
        const step = steps.find(step => step.step_id === e.step_id);
        return <article className="investigation-evidence" key={e.evidence_ref ?? i}><h4>原始记录 <code>{e.record_id}</code></h4>
          {!record ? <p className="text-red">原始内容尚未核验，不猜测金额、商户或关联卡片。</p> : <>
            <dl><div><dt>商户</dt><dd>{record.merchant_name} · {record.merchant_id}</dd></div><div><dt>金额</dt><dd><strong>{displayAmount(record.amount)} {record.currency}</strong></dd></div>
              <div><dt>消费时间</dt><dd>{time(record.occurred_at)} · 业务日期 {record.business_date}</dd></div><div><dt>支付工具引用</dt><dd><code>{record.payment_instrument_ref ?? "未提供"}</code></dd></div>
              <div><dt>核验读取时间</dt><dd>{time(e.read_at)}</dd></div></dl>
            <p>命中规则：{findings.filter(f => f.finding?.record_ids?.includes(record.record_id)).map(f => f.finding.rule_id).join("、") || "未提供"}</p>
            {link?.status === "verified" && link.linked === false ? <p>未关联模拟银行卡 · {link.reason_code}，不会猜测。</p> :
              card ? <p>服务端明确关联：<code>{card.card_id}</code> · <Link className="text-link" to={`/cards?card_id=${encodeURIComponent(card.card_id)}&record_id=${encodeURIComponent(record.record_id)}`}>查看关联银行卡（保留原始记录）</Link></p> : <p>关联尚未核验或查询失败，未猜测卡片。</p>}
          </>}
          <small>证据引用 <code>{e.evidence_ref}</code> · 来源步骤 <code>{e.step_id ?? "未提供"}</code></small>
          {step?.request_id && <p><Link className="text-link" to={auditPath(step.request_id)}>查看原始记录核验审计</Link></p>}
        </article>;
      })}
    </details>

    <section className="investigation-section"><h4>关联模拟银行卡 · 调查快照</h4><p className="muted">以下状态属于报告读取时的历史快照，不一定代表现在的状态。调查不会自动冻结、解冻或挂失。</p>
      {!cards.length && <p className="empty">没有已关联的银行卡证据，未猜测卡片。</p>}
      {cards.map((e, i) => <article className="investigation-evidence" key={e.evidence_ref ?? i}>
        {e.status === "verified" && e.card ? <><p><strong>历史状态快照</strong> · 读取时间 {time(e.read_at)}</p><CardSummary card={e.card} />
          {e.card.temporary_frozen && <p>读取时已临时冻结；本次调查未重复申请冻结。</p>}
          <LatestInvestigationCard key={`${report.investigation_id}:${e.card_id}`} account={account} cardId={e.card_id} /></> : <p>卡片 <code>{e.card_id}</code> 状态尚未核验，不使用关联查询中的旧状态冒充最新结果。</p>}
        <Link className="text-link" to={`/cards?card_id=${encodeURIComponent(e.card_id)}`}>前往现有银行卡管理</Link>
        <small>证据引用 <code>{e.evidence_ref}</code> · 来源关联 {(e.source_link_refs ?? []).join("、") || "未提供"}</small>
      </article>)}
    </section>

    <details className="investigation-section"><summary>实际工具步骤（{steps.length}个节点）</summary>
      <p>以下状态来自服务端执行记录；等待、跳过和未执行均不表示成功。</p>
      {!steps.length && <p>没有执行步骤记录。</p>}
      <ol className="investigation-steps">{steps.map((step, i) => <li key={step.step_id ?? i}>
        <div className="row"><code>{step.tool}</code><span className={`badge tone-${step.status === "succeeded" ? "green" : step.status === "failed" ? "red" : "yellow"}`}>{stepLabel(step.status)}</span></div>
        <p>实际执行序号 {step.execution_index ?? "未执行"} · <code>{step.step_id}</code></p>
        <p>依赖：{step.depends_on?.join("、") || "无"} · 证据引用：{step.evidence_ref ?? "无"}</p>
        <p>开始 {time(step.started_at)} · 结束 {time(step.finished_at)}{step.code && <> · <code>{step.code}</code></>}</p>
        {(step.decisions ?? []).map((decision, index) => <p key={index}>真实安全决策：{decision.risk_level} · {decision.permission_action} · {decision.risk_reason}</p>)}
        <small>审计事件ID：{step.audit_event_ids?.join("、") || "未提供"}</small>
        {step.request_id && <p><Link className="text-link" to={auditPath(step.request_id)}>查看此步骤审计</Link></p>}
      </li>)}</ol>
    </details>
    <details className="investigation-section"><summary>模型计划选择记录（与实际执行分开）</summary>
      <p>选择被接受不等于步骤已经成功；请对照上方实际执行状态。模型调用次数 {report.planning?.model_calls ?? "未提供"}。</p>
      {!report.planning?.events?.length && <p>没有模型选择记录，不能宣称AI自主规划成功。</p>}
      <ul>{(report.planning?.events ?? []).map((event, i) => <li key={i}>选择{event.index} · {event.status === "accepted" ? "计划被接受" : event.status === "rejected" ? "计划被拒绝" : "计划状态未提供"} · <code>{event.code}</code>
        {event.step_id && <> · 候选步骤 <code>{event.step_id}</code></>} {event.reason_code && <> · 理由代码 {event.reason_code}</>}
        {event.audit_event_id != null && <> · 计划审计事件ID {event.audit_event_id}</>}</li>)}</ul>
    </details>
    <p className="muted">本次调查不创建处置任务。正式挂失和解挂仍需强验证，当前Demo不能放行。模拟账户不等于生产级持卡人身份认证。</p>
    {report.audit?.request_id && <Link className="text-link" to={auditPath(report.audit.request_id)}>查看调查主审计</Link>}
  </section>;
}
