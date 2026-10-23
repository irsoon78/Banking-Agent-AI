import { useEffect, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { banking } from "../api/banking";
import type { InvestigationResponse, InvestigationsResponse } from "../api/types";
import { LatestRead } from "../state/latestRead";
import { coverageLabel, executionLabel, focusLabel, investigationLink, plannerLabel, readInvestigation, validInvestigationId, validateInvestigation, validateInvestigationList } from "../state/investigationRead";
import type { InvestigationReadView } from "../state/investigationRead";
import { InvestigationView } from "./InvestigationView";

export function InvestigationHistoryList({ data, account }: { data: InvestigationsResponse; account: string }) {
  try { validateInvestigationList(data, account); }
  catch { return <p role="alert">历史调查归属或结构无效，未展示。</p>; }
  return <><p className="muted">当前账户共{data.total}份报告，展示最近{data.items.length}份。查看历史只读取快照，不发起新调查。</p>
    {!data.items.length && <p className="empty">当前账户没有已保存的调查报告。</p>}
    <ul className="investigation-history-list">{data.items.map(item => <li key={item.investigation_id}>
      <Link className="text-link" to={investigationLink(account, item.investigation_id)}>查看报告 <code>{item.investigation_id}</code></Link>
      <p>{focusLabel(item.focus)} · {item.period?.start_date ?? "未提供"} 至 {item.period?.end_date_exclusive ?? "未提供"}（结束日不含）</p>
      <p>{executionLabel(item.execution_status)} · {coverageLabel(item.coverage_status)} · {plannerLabel(item.planner_mode)}</p>
      <small>报告开始 {item.started_at ?? "未提供"} · 快照更新时间 {item.recovery?.updated_at ?? "未提供"}</small>
    </li>)}</ul></>;
}

export function InvestigationHistory({ account, latestId }: { account: string; latestId?: string | null }) {
  const [params, setParams] = useSearchParams();
  const id = params.get("investigation_id") ?? "";
  const linkAccount = params.get("investigation_account");
  const selected = !linkAccount || linkAccount === account ? id : "";
  const listReader = useRef(new LatestRead());
  const detailReader = useRef(new LatestRead());
  const previousAccount = useRef(account);
  const [list, setList] = useState<InvestigationReadView<InvestigationsResponse> | null>(null);
  const [detail, setDetail] = useState<InvestigationReadView<InvestigationResponse> | null>(null);
  const loadList = () => readInvestigation(listReader.current, account,
    () => banking.investigations(account), data => validateInvestigationList(data, account), setList);
  const loadDetail = () => readInvestigation(detailReader.current, `${account}:${selected}`, () => banking.investigation(account, selected), data => {
    if (data.account_id !== account) throw new Error("调查查询账户不匹配。");
    validateInvestigation(data.investigation, account, selected);
  }, setDetail);

  useEffect(() => {
    const reader = listReader.current;
    if (previousAccount.current !== account) {
      previousAccount.current = account;
      setParams(previous => { const next = new URLSearchParams(previous); next.delete("investigation_id"); next.delete("investigation_account"); return next; }, { replace: true });
    }
    void readInvestigation(reader, account, () => banking.investigations(account), data => validateInvestigationList(data, account), setList);
    return () => reader.invalidate();
  }, [account, latestId]);

  useEffect(() => {
    const reader = detailReader.current;
    reader.invalidate();
    if (validInvestigationId(selected)) {
      void loadDetail();
    }
    return () => reader.invalidate();
  }, [account, selected]);
  const currentList = list?.scope === account ? list : null;
  const currentDetail = detail?.scope === `${account}:${selected}` ? detail : null;
  return <details className="investigation-history" open={!!selected}>
    <summary>历史调查 · 当前账户 {account}</summary>
    <p>刷新页面后可恢复已保存报告；链接只保存报告ID，不保存消费内容、许可或操作授权。</p>
    <button className="button secondary" disabled={currentList?.loading} onClick={() => void loadList()}>{currentList?.loading ? "正在读取历史…" : "刷新历史列表（只读）"}</button>
    {linkAccount && linkAccount !== account && <p className="muted">链接属于其他模拟账户，未展示其调查内容。</p>}
    {currentList?.error && <p role="alert" className="text-red">{currentList.error} 历史查询失败，不会自动重新调查。</p>}
    {currentList?.data && <><InvestigationHistoryList data={currentList.data} account={account} />
      <Link className="text-link" to={`/audit?request_id=${encodeURIComponent(currentList.data.audit_id)}`}>查看历史列表查询审计</Link></>}
    {selected && <section className="investigation-history-detail">
      <button className="button secondary" onClick={() => setParams(previous => { const next = new URLSearchParams(previous); next.delete("investigation_id"); next.delete("investigation_account"); return next; })}>关闭历史报告</button>
      {validInvestigationId(selected) && <button className="button secondary" disabled={currentDetail?.loading} onClick={() => void loadDetail()}>重新读取此报告（只读）</button>}
      {!validInvestigationId(selected) && <p role="alert">调查ID格式无效，未查询。</p>}
      {currentDetail?.loading && <p role="status">正在读取已保存的报告…</p>}
      {currentDetail?.error && <p role="alert" className="text-red">{currentDetail.error} 未重新执行调查；可重新打开报告进行只读查询。</p>}
      {currentDetail?.data && <><InvestigationView report={currentDetail.data.investigation} account={account} historical />
        <Link className="text-link" to={`/audit?request_id=${encodeURIComponent(currentDetail.data.audit_id)}`}>查看本次历史读取审计</Link></>}
    </section>}
  </details>;
}
