import { Link } from "react-router-dom";
import type { Card, CardOperation, ToolData } from "../api/types";
import { CARD_NOTICE, validCardOperation } from "../state/cardFlow";

export const cardStatus = (card: Card) => card.loss_status === "reported" ? "正式挂失" : card.temporary_frozen ? "临时冻结" : "正常";

export function CardSummary({ card }: { card: Card }) {
  return <section className="card-summary" aria-label={`模拟卡片 ${card.card_id}`}>
    <div className="row"><h3>{card.display_name}</h3><span className={`badge tone-${card.loss_status === "reported" ? "red" : card.temporary_frozen ? "yellow" : "green"}`}>{cardStatus(card)}</span></div>
    <p>展示尾号 <strong>{card.last_four}</strong> · 所属账户 {card.account_id}</p>
    <dl><div><dt>card_id</dt><dd><code>{card.card_id}</code></dd></div><div><dt>临时冻结</dt><dd>{card.temporary_frozen ? "是" : "否"}</dd></div><div><dt>正式挂失</dt><dd>{card.loss_status === "reported" ? "已挂失，临时解冻不能解挂" : "未挂失"}</dd></div><div><dt>服务端状态版本</dt><dd>{card.version}</dd></div><div><dt>更新时间</dt><dd>{new Date(card.updated_at).toLocaleString("zh-CN", { timeZone: "Asia/Shanghai", hour12: false })}（北京时间）</dd></div></dl>
  </section>;
}

export function CardOperationView({ operation }: { operation: CardOperation }) {
  if (!validCardOperation(operation)) return <p role="alert">未取得有效的卡片操作凭据，请核对服务端。</p>;
  return <section className="receipt card-receipt" aria-label="卡片操作凭据"><h3>模拟卡片操作凭据 · 已提交成功</h3>
    <p>{operation.operation === "temporary_freeze" ? "临时冻结" : "解除临时冻结"} · <code>{operation.card_id}</code> · 账户{operation.account_id}</p>
    <dl><div><dt>临时冻结状态</dt><dd>{operation.before.temporary_frozen ? "冻结" : "未冻结"} → {operation.after.temporary_frozen ? "冻结" : "未冻结"}</dd></div><div><dt>状态版本</dt><dd>{operation.before.version} → {operation.after.version}</dd></div><div><dt>正式挂失状态</dt><dd>{operation.after.loss_status === "reported" ? "已挂失" : "未挂失"}</dd></div><div><dt>operation_id</dt><dd><code>{operation.operation_id}</code></dd></div><div><dt>action_id</dt><dd><code>{operation.action_id}</code></dd></div><div><dt>执行时间</dt><dd>{operation.executed_at}</dd></div></dl>
    {operation.audit_id && <Link className="text-link" to={`/audit?request_id=${encodeURIComponent(operation.audit_id)}`}>查看操作审计</Link>}
    <p className="muted">{CARD_NOTICE}</p></section>;
}

export function CardDataView({ result }: { result: ToolData }) {
  if (result.ok !== true) return null;
  if (result.card_operation) return <CardOperationView operation={result.card_operation} />;
  if (result.linked === false) return <p className="empty">原始记录 <code>{result.record_id}</code>：未关联模拟银行卡，不会猜测卡片。</p>;
  const cards = result.cards ?? (result.card ? [result.card] : null);
  if (!cards) return null;
  return <div className="card-results"><p className="muted">模拟银行卡查询结果；仅表示服务端读取时的模拟状态。</p>{result.record_id && <p>追溯原始消费记录 <code>{result.record_id}</code> · 数据集 <code>{result.dataset_id}</code></p>}
    {cards.length ? cards.map(card => <CardSummary key={card.card_id} card={card} />) : <p className="empty">当前账户没有模拟银行卡。</p>}</div>;
}

export function CardList({ cards, disabled, selected, onPrepare }: { cards: Card[]; disabled: boolean; selected?: string | null; onPrepare: (card: Card, action: "temporarily_freeze_card" | "remove_temporary_freeze" | "report_card_loss" | "cancel_card_loss_report") => void }) {
  if (!cards.length) return <p className="empty">当前账户没有模拟银行卡。</p>;
  return <div className="cards-grid">{cards.map(card => <article key={card.card_id} className={`panel ${selected === card.card_id ? "card-selected" : ""}`}>
    <CardSummary card={card} /><div className="action-buttons">
      {card.loss_status === "normal" && <button className="button primary" disabled={disabled} onClick={() => onPrepare(card, card.temporary_frozen ? "remove_temporary_freeze" : "temporarily_freeze_card")}>申请{card.temporary_frozen ? "解除临时冻结" : "临时冻结"}</button>}
      <button className="button secondary" disabled={disabled} onClick={() => onPrepare(card, card.loss_status === "reported" ? "cancel_card_loss_report" : "report_card_loss")}>{card.loss_status === "reported" ? "解挂" : "正式挂失"}（强验证）</button>
    </div></article>)}</div>;
}
