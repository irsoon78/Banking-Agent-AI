import { Link } from "react-router-dom";
import { useDemo } from "../state/DemoProvider";
import type { DemoState } from "../state/demoReducer";
import { canResolve, cardActionLabel, isCardPending } from "../state/cardFlow";
import { CardSummary } from "./CardView";
import {
  Alert,
  AuditLink,
  Empty,
  Icon,
  Receipt,
  dateTime,
  money,
  statusLabels,
} from "./ui";

export function RiskPanel({ actionable = false }: { actionable?: boolean }) {
  const { state, resolveAction, recoverCardOperation } = useDemo();
  return <RiskPanelView state={state} actionable={actionable} resolveAction={resolveAction} recoverCardOperation={recoverCardOperation} />;
}

export function RiskPanelView({ state, actionable = false, resolveAction, recoverCardOperation }: {
  state: DemoState; actionable?: boolean; resolveAction: (confirm: boolean) => Promise<void>; recoverCardOperation: () => Promise<void>;
}) {
  const decision = state.pendingAction
    ? state.pendingDecision
    : state.currentDecision;
  if (!decision)
    return (
      <section className="panel risk-panel">
        <h2>
          <Icon name="shield" /> 风险与权限
        </h2>
        <Empty>发送一条指令，查看后端返回的风险判断。</Empty>
      </section>
    );
  const matchingResult =
    state.actionResponse?.action_id === decision.action_id
      ? state.actionResponse
      : null;
  const status = matchingResult?.status || decision.status;
  const pending = state.pendingAction;
  const canConfirm = canResolve(pending, decision, state.account_id, state.conversation_id);
  const labels = {
    green: "绿色 · 自动执行",
    yellow: "黄色 · 用户确认",
    red: "红色 · 强验证",
  };
  return (
    <section
      className={`panel risk-panel risk-${decision.risk_level || "none"}`}
      aria-label="风险与权限"
    >
      <div className="row">
        <h2>
          <Icon name="shield" /> 风险与权限
        </h2>
        <span className="live-label">
          {pending ? "原待确认任务" : "最近业务决策"}
        </span>
      </div>
      <div className={`risk-heading tone-${decision.risk_level || "neutral"}`}>
        <strong>
          {decision.risk_level ? labels[decision.risk_level] : "尚无风险决策"}
        </strong>
        <span>{statusLabels[status]}</span>
      </div>
      <p className="risk-reason">
        {decision.risk_reason || "未选择工具，尚未产生风险决策。"}
      </p>
      <dl className="decision-fields">
        <div>
          <dt>操作</dt>
          <dd>
            <code>{decision.operation || "—"}</code>
          </dd>
        </div>
        <div>
          <dt>权限策略</dt>
          <dd>
            <code>{decision.permission_action || "—"}</code>
          </dd>
        </div>
      </dl>
      {decision.status === "require_verification" && (
        <Alert tone="red">
          <strong>当前操作尚未执行。</strong>
          <p>需要强验证；当前后端尚未提供完成强验证的能力。</p>
          <code>verification_required · not_executed</code>
        </Alert>
      )}
      {pending && (
        <div className="pending-details">
          <h3>
            {isCardPending(pending) ? `待确认卡片操作：${cardActionLabel(pending.action)}` : "待确认转账"} <span className="badge tone-yellow">参数只读</span>
          </h3>
          {!isCardPending(pending) && <div className="pending-amount">
            {money(pending.params.amount)} <small>元</small>
          </div>}
          {isCardPending(pending) && <><p>确认前卡片状态不会改变。任务5分钟有效；服务端最终复核版本。</p><dl><div><dt>card_id</dt><dd><code>{pending.params.card_id}</code></dd></div><div><dt>准备时版本快照</dt><dd>{pending.params.expected_version}</dd></div><div><dt>原conversation_id</dt><dd><code>{pending.conversation_id}</code></dd></div></dl>
            {state.cardsData?.cards?.filter(c => c.card_id === pending.params.card_id && c.account_id === pending.account_id).map(c => <div key={c.card_id}><p className="muted">以下信息来自最近一次服务端查询，确认仍以版本快照复核。</p><CardSummary card={c} /></div>)}</>}
          <dl>
            <div>
              <dt>所属账户</dt>
              <dd>{pending.account_id}</dd>
            </div>
            {!isCardPending(pending) && <div>
              <dt>收款账户</dt>
              <dd>{pending.params.to_account_id}</dd>
            </div>}
            <div>
              <dt>创建时间</dt>
              <dd>{dateTime(pending.created_at)}</dd>
            </div>
            <div>
              <dt>action_id</dt>
              <dd>
                <code>{pending.action_id}</code>
              </dd>
            </div>
          </dl>
          <details>
            <summary>查看 pending_action</summary>
            <pre>{JSON.stringify(pending, null, 2)}</pre>
          </details>
        </div>
      )}
      {state.uncertain && (
        <Alert tone="red">
          结果待核对。已锁定新的资金操作；请核对账单与审计，不要重新发起交易。
          {state.unknownCardAction && <><p>待核对卡片action_id：<code>{state.unknownCardAction.action_id}</code></p><button className="button secondary" disabled={state.loading.recovery} onClick={() => void recoverCardOperation()}>{state.loading.recovery ? "正在查询真实结果…" : "只读查询卡片操作结果"}</button><p>{state.recoveryStatus === "not_found" ? "未查到结果，状态仍未知；不能重新执行。" : "状态仍未知；这里只查询凭据，不会自动重试写操作。"}</p></>}
        </Alert>
      )}
      {canConfirm &&
        !state.uncertain &&
        (actionable ? (
          <div className="action-buttons">
            <button
              className="button primary"
              disabled={state.loading.action || state.loading.chat}
              onClick={() => void resolveAction(true)}
            >
              {state.loading.action ? "正在处理，请勿重复操作…" : "确认执行"}
            </button>
            <button
              className="button secondary"
              disabled={state.loading.action || state.loading.chat}
              onClick={() => void resolveAction(false)}
            >
              取消操作
            </button>
          </div>
        ) : (
          <Link className="button primary full" to="/risk">
            进入风险确认 <Icon name="arrow" />
          </Link>
        ))}
      {matchingResult && <Receipt response={matchingResult} />}
      {matchingResult?.audit_warning && (
        <Alert>{matchingResult.audit_warning}</Alert>
      )}
      <div className="message-links">
        <AuditLink id={decision.audit_id} />
        {actionable && (
          <Link className="text-link" to="/chat">
            返回 AI 对话
          </Link>
        )}
      </div>
      {decision.decisions.length > 0 && (
        <details>
          <summary>实际决策明细（{decision.decisions.length}）</summary>
          <pre>{JSON.stringify(decision.decisions, null, 2)}</pre>
        </details>
      )}
    </section>
  );
}
