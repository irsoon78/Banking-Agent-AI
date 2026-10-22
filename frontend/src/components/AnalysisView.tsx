import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { banking } from "../api/banking";
import { RequestError } from "../api/client";
import type { AnalysisResponse, SpendingRecord, ToolData } from "../api/types";
import { barPercent, displayAmount, recordsQuery, SAMPLE_NOTICE } from "../state/analysisQuery";
import { SpendingCardLink } from "./SpendingCardLink";

function RecordsTable({ rows }: { rows: SpendingRecord[] }) {
  return <div className="table-scroll"><table><thead><tr><th>北京时间 / 记录ID</th><th>商户 / 分类</th><th>金额（元）</th><th>支付工具引用</th></tr></thead>
    <tbody>{rows.map(r => <tr key={r.record_id}><td>{r.business_date}<small>{new Date(r.occurred_at).toLocaleString("zh-CN", { timeZone: "Asia/Shanghai", hour12: false })}</small><code>{r.record_id}</code></td><td>{r.merchant_name}<small>{r.category}</small></td><td>{displayAmount(r.amount)}</td><td><code>{r.payment_instrument_ref || "—"}</code><SpendingCardLink account={r.account_id} recordId={r.record_id} /></td></tr>)}</tbody></table></div>;
}

export function RecordsResult({ data, offset, loading, onPage }: {
  data: AnalysisResponse; offset: number; loading: boolean; onPage: (offset: number) => void;
}) {
  const rows = data.spending_records?.records ?? (data.spending_record ? [data.spending_record.record] : []);
  return <><p>样本版本 {data.spending_records?.dataset.version ?? data.spending_record?.dataset.version} · <Link to={`/audit?request_id=${encodeURIComponent(data.audit_id)}`}>查看本次明细审计</Link></p>
    {data.audit_warning && <p className="alert" role="alert">{data.audit_warning}</p>}
    {rows.length ? <RecordsTable rows={rows} /> : <p className="empty">没有匹配的已记录消费。</p>}
    {data.spending_records && <div className="analysis-pagination"><button disabled={loading || offset === 0} onClick={() => onPage(Math.max(0, offset - 10))}>上一页</button><span>匹配{data.spending_records.total_count}条 · 第{Math.floor(offset / 10) + 1}页</span><button disabled={loading || offset + 10 >= data.spending_records.total_count} onClick={() => onPage(offset + 10)}>下一页</button></div>}</>;
}

export function AnalysisRecords({ account, period, category, merchant, recordId }: {
  account: string; period: string; category?: string; merchant?: string; recordId?: string;
}) {
  const [offset, setOffset] = useState(0);
  const [data, setData] = useState<AnalysisResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [errorAudit, setErrorAudit] = useState<string | undefined>();
  useEffect(() => {
    let active = true;
    setData(null); setError(""); setErrorAudit(undefined); setLoading(true);
    void banking.analysisRecords(account, recordsQuery(period, offset, category, merchant, recordId)).then(result => {
      if (active) setData(result);
    }).catch(e => { if (active) { setError(e instanceof Error ? e.message : "读取明细失败。"); setErrorAudit(e instanceof RequestError ? e.auditId : undefined); } })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [account, period, offset, category, merchant, recordId]);
  return <section className="analysis-records" aria-label="原始消费明细">
    <h3>原始消费明细</h3><p className="muted">{SAMPLE_NOTICE}</p>
    {loading && <p role="status">正在读取明细…</p>}
    {error && <p className="alert tone-red" role="alert">{error}</p>}
    {errorAudit && <Link to={`/audit?request_id=${encodeURIComponent(errorAudit)}`}>查看失败明细查询审计</Link>}
    {data && <RecordsResult data={data} loading={loading} offset={offset} onPage={setOffset} />}
  </section>;
}

export function AnalysisView({ result, auditId }: { result: ToolData; auditId?: string }) {
  const report = result.analysis ?? result.comparison?.current;
  const comparison = result.comparison;
  const [drill, setDrill] = useState<{ category?: string; merchant?: string; recordId?: string } | null>(null);
  if (!report) {
    if (result.spending_record) return <section className="analysis-view"><p className="alert tone-blue">{SAMPLE_NOTICE}</p><p>样本版本 {result.spending_record.dataset.version} · 北京时间</p><RecordsTable rows={[result.spending_record.record]} /></section>;
    if (result.spending_records) return <section className="analysis-view"><p className="alert tone-blue">{SAMPLE_NOTICE}</p><p>{result.spending_records.period.start_date} 至 {result.spending_records.period.end_date_exclusive}（结束日不含） · 匹配{result.spending_records.total_count}条</p><RecordsTable rows={result.spending_records.records} /></section>;
    return null;
  }
  const period = `${report.period.start_date}/${report.period.end_date_exclusive}`;
  return <section className="analysis-view" aria-label="消费分析结果">
    <p className="alert tone-blue">{SAMPLE_NOTICE}</p>
    <p className="muted">北京时间 · {report.period.start_date} 至 {report.period.end_date_exclusive}（结束日不含）<br />来源：独立模拟消费样本 · 版本：{report.dataset.version} · 数据集：<code>{report.dataset.dataset_id}</code></p>
    {!report.coverage.complete && <p className="alert tone-yellow" role="alert">覆盖不足：{report.coverage.note}</p>}
    {comparison && <section className="analysis-comparison"><h3>相邻月份比较</h3><p>{comparison.through_day ? `两个月均取前${comparison.through_day}天` : "比较两个完整月份"}；仅比较同一来源。</p><p>前期 {comparison.previous.period.start_date}：{displayAmount(comparison.previous.summary.amount)}元（已记录）；本期：{displayAmount(report.summary.amount)}元（已记录）。</p>
      {comparison.comparable ? <p>变化 <strong>{displayAmount(comparison.change_amount)}元</strong> · 变化率 {comparison.change_percent == null ? "不适用（前期为零）" : `${comparison.change_percent}%`}</p> : <p className="alert tone-yellow" role="alert">两期覆盖不足，不能据此生成整月增幅。前期覆盖{comparison.previous.coverage.covered_days}/{comparison.previous.coverage.requested_days}天，本期覆盖{report.coverage.covered_days}/{report.coverage.requested_days}天。</p>}</section>}
    <div className="analysis-summary"><div><span>已记录消费金额（CNY）</span><strong>{displayAmount(report.summary.amount)}</strong></div><div><span>已记录笔数</span><strong>{report.summary.count}</strong></div><div><span>样本覆盖天数</span><strong>{report.coverage.covered_days}/{report.coverage.requested_days}</strong></div></div>
    {!report.summary.count && <p className="empty">此期间没有已记录消费。未覆盖日期不代表零消费。</p>}
    <div className="analysis-columns"><section><h3>消费分类</h3>{report.categories.map(g => <button className="analysis-group" key={g.category} onClick={() => setDrill({ category: g.category })}><span>{g.label} · {g.count}笔</span><strong>{displayAmount(g.amount)}元</strong><i style={{ width: `${barPercent(g.share_percent)}%` }} /><small>{g.share_percent ?? "—"}% · 查看明细</small></button>)}</section>
      <section><h3>商户排行</h3>{report.merchants.map((g, i) => <button className="analysis-group" key={g.merchant_id} onClick={() => setDrill({ merchant: g.merchant_id })}><span>{i + 1}. {g.merchant_name}</span><strong>{displayAmount(g.amount)}元</strong><small>{g.count}笔 · 查看明细</small></button>)}</section></div>
    <h3>每日消费趋势</h3><p className="muted">柱高表示服务端返回的期间金额占比；斜纹为未覆盖日期。</p>
    <div className="analysis-trend" aria-label="每日消费趋势">{report.daily_trend.map(d => <div className={`analysis-day ${d.covered ? "" : "uncovered"}`} key={d.date} title={`${d.date}：${d.covered ? `${displayAmount(d.amount)}元 · ${d.count}笔` : "未覆盖，金额未知"}`}><div className="analysis-bar-track"><i style={{ height: `${d.covered ? Math.max(d.amount === "0.00" ? 0 : 2, barPercent(d.share_percent)) : 100}%` }} /></div><small>{d.date.slice(5)}</small></div>)}</div>
    <h3>需要关注的记录</h3><p className="muted">{report.anomaly_disclaimer}</p>
    {report.anomalies.length ? report.anomalies.map((f, i) => <div className="analysis-finding" key={`${f.rule_id}-${i}`}><code>{f.rule_id}</code><p>{f.reason}</p>{f.record_ids.map(id => <button className="button secondary" key={id} onClick={() => setDrill({ recordId: id })}>查看记录 {id}</button>)}</div>) : <p>未命中已实现的提示规则，不代表不存在其他风险。</p>}
    <div className="message-links"><button className="button secondary" onClick={() => setDrill({})}>查看全部原始明细</button>{auditId && <Link className="button secondary" to={`/audit?request_id=${encodeURIComponent(auditId)}`}>查看分析审计</Link>}</div>
    {drill && <AnalysisRecords key={`${report.account_id}-${period}-${JSON.stringify(drill)}`} account={report.account_id} period={period} {...drill} />}
  </section>;
}
