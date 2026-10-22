import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { banking } from "../api/banking";
import { RequestError } from "../api/client";
import type { CardResponse } from "../api/types";
import { CardDataView } from "./CardView";

export function SpendingCardLink({ account, recordId }: { account: string; recordId: string }) {
  const generation = useRef(0);
  const [view, setView] = useState<{ account: string; record: string; data?: CardResponse; error?: string; audit?: string } | null>(null);
  const [loading, setLoading] = useState(false);
  useEffect(() => { ++generation.current; setView(null); setLoading(false); return () => { ++generation.current; }; }, [account, recordId]);
  const read = async () => {
    const ticket = ++generation.current;
    setLoading(true); setView(null);
    try {
      const data = await banking.spendingCard(account, recordId);
      if (data.account_id !== account || data.record_id !== recordId || (data.card && data.card.account_id !== account)) throw new Error("关联结果归属不匹配，已拒绝展示。");
      if (ticket === generation.current) setView({ account, record: recordId, data });
    } catch (e) {
      if (ticket === generation.current) setView({ account, record: recordId, error: e instanceof Error ? e.message : "关联查询失败。", audit: e instanceof RequestError ? e.auditId : undefined });
    } finally { if (ticket === generation.current) setLoading(false); }
  };
  const current = view?.account === account && view.record === recordId ? view : null;
  return <div className="spending-card-link"><button className="button secondary" disabled={loading} onClick={() => void read()}>{loading ? "正在查询关联…" : "查看关联银行卡"}</button>
    {current?.error && <p className="alert tone-red" role="alert">{current.error}</p>}
    {current?.data && <><CardDataView result={current.data} />{current.data.card && <Link className="text-link" to={`/cards?card_id=${encodeURIComponent(current.data.card.card_id)}&record_id=${encodeURIComponent(recordId)}`}>前往银行卡管理（原始记录 {recordId}）</Link>}
      <Link className="text-link" to={`/audit?request_id=${encodeURIComponent(current.data.audit_id)}`}>查看关联查询审计</Link></>}
    {current?.audit && <Link to={`/audit?request_id=${encodeURIComponent(current.audit)}`}>查看失败审计</Link>}
  </div>;
}
