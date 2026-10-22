import { useEffect } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useDemo } from "../state/DemoProvider";
import { CARD_NOTICE, cardActionLabel } from "../state/cardFlow";
import { CardList } from "../components/CardView";
import { SpendingCardLink } from "../components/SpendingCardLink";
import { PageTitle, Alert, AuditLink } from "../components/ui";
import { RiskPanel } from "../components/RiskPanel";

export function CardsPage() {
  const { state, refreshCards, sendMessage } = useDemo();
  const [params] = useSearchParams();
  const selected = params.get("card_id");
  const record = params.get("record_id");
  useEffect(() => { void refreshCards(); }, [refreshCards, state.account_id]);
  const data = state.cardsData?.account_id === state.account_id ? state.cardsData : null;
  const blocked = !!state.pendingAction || state.uncertain || state.loading.chat || state.loading.action || state.loading.cards;
  return <>
    <PageTitle eyebrow="SIMULATED CARD MANAGEMENT" title="银行卡管理">每一次状态变化，都经过服务端确认并留下凭据。</PageTitle>
    <Alert tone="blue">{CARD_NOTICE}</Alert>
    <section className="panel row"><div>当前模拟账户 {state.account_id} · 不具备生产级持卡人身份认证</div><button className="button secondary" disabled={state.loading.cards} onClick={() => void refreshCards()}>刷新卡片状态</button></section>
    {state.loading.cards && <p role="status">正在读取服务端银行卡状态…</p>}
    {state.cardsError && <Alert tone="red">{state.cardsError} 请检查后端及独立演示数据库，页面不会生成卡片。</Alert>}
    {state.cardsErrorAudit && <AuditLink id={state.cardsErrorAudit}>查看失败查询审计</AuditLink>}
    {record && <section className="panel"><h3>来自原始消费记录 <code>{record}</code></h3><p>仅查询明确映射，不会因异常提示自动冻结或挂失。</p><SpendingCardLink account={state.account_id} recordId={record} /></section>}
    {data && <>
      {data.audit_warning && <Alert>{data.audit_warning}</Alert>}
      {selected && !data.cards?.some(c => c.card_id === selected) && <Alert tone="red">指定卡片不存在或不属于当前账户，请重新查询。</Alert>}
      <CardList cards={data.cards ?? []} disabled={blocked} selected={selected} onPrepare={(card, action) => void sendMessage(`${cardActionLabel(action)}${card.card_id}`)} />
      <p className="muted">读取时间 {data.read_at} · <AuditLink id={data.audit_id}>查看列表查询审计</AuditLink></p>
    </>}
    {state.pendingAction && <p className="alert">已有待确认任务，请先确认或取消。切换页面不会丢失任务，新的卡片操作不会替换它。</p>}
    <div className="cards-risk"><RiskPanel actionable /></div>
    {state.recoveryStatus === "completed" && <Alert tone="green">已查到原操作的持久化成功凭据，已重新查询卡片状态；未重发写操作。请同时留意状态读取是否成功。</Alert>}
    <p><Link to="/chat">前往AI对话查询或提出明确卡片请求</Link></p>
  </>;
}
