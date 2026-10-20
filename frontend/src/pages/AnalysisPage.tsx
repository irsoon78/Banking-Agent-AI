import { useEffect, useRef, useState } from "react";
import type { FormEvent } from "react";
import { banking } from "../api/banking";
import { RequestError } from "../api/client";
import { Link } from "react-router-dom";
import type { AnalysisResponse } from "../api/types";
import { AnalysisView } from "../components/AnalysisView";
import { PageTitle } from "../components/ui";
import { useDemo } from "../state/DemoProvider";
import { analysisQuery, SAMPLE_NOTICE } from "../state/analysisQuery";
import { LatestRead } from "../state/latestRead";

export function AnalysisPage() {
  const { state } = useDemo();
  const [preset, setPreset] = useState("this_month");
  const [month, setMonth] = useState("");
  const [start, setStart] = useState("");
  const [end, setEnd] = useState("");
  const [compare, setCompare] = useState(false);
  const [through, setThrough] = useState("");
  const [query, setQuery] = useState(() => analysisQuery("this_month").toString());
  const [data, setData] = useState<AnalysisResponse | null>(null);
  const [error, setError] = useState("");
  const [errorAudit, setErrorAudit] = useState<string | undefined>();
  const [loading, setLoading] = useState(false);
  const reads = useRef(new LatestRead());
  const [reload, setReload] = useState(0);
  useEffect(() => {
    let active = true;
    void reads.current.run(async latest => {
      setData(null); setError(""); setErrorAudit(undefined); setLoading(true);
      try {
        const result = await banking.analysis(state.account_id, new URLSearchParams(query));
        if (active && latest()) setData(result);
      } catch (e) {
        if (active && latest()) { setError(e instanceof Error ? e.message : "分析请求失败。"); setErrorAudit(e instanceof RequestError ? e.auditId : undefined); }
      } finally {
        if (active && latest()) setLoading(false);
      }
    }, true);
    return () => { active = false; };
  }, [state.account_id, query, reload]);

  const submit = (event: FormEvent) => {
    event.preventDefault();
    try {
      const period = preset === "month" ? month : preset === "range" ? `${start}/${end}` : preset;
      setQuery(analysisQuery(period, compare, through).toString());
      setReload(n => n + 1);
    } catch (e) {
      void reads.current.run(async () => {}, true);
      setData(null); setLoading(false);
      setError(e instanceof Error ? e.message : "筛选条件无效。"); setErrorAudit(undefined);
    }
  };
  return <>
    <PageTitle eyebrow="BILL ANALYSIS" title="让每一笔样本消费，都能追溯。">按分类、商户与日期查看确定性统计；规则提示只用于核对。</PageTitle>
    <p className="alert tone-blue">{SAMPLE_NOTICE}</p>
    <form className="panel analysis-filters" onSubmit={submit}>
      <label>分析期间<select value={preset} onChange={e => setPreset(e.target.value)}><option value="this_month">本月（服务器北京时间）</option><option value="last_month">上月</option><option value="recent">最近30天</option><option value="month">指定月份</option><option value="range">日期范围</option></select></label>
      {preset === "month" && <label>月份<input type="month" required value={month} onChange={e => setMonth(e.target.value)} /></label>}
      {preset === "range" && <><label>开始日（含）<input type="date" required value={start} onChange={e => setStart(e.target.value)} /></label><label>结束日（不含）<input type="date" required value={end} onChange={e => setEnd(e.target.value)} /></label></>}
      <label className="analysis-checkbox"><input type="checkbox" checked={compare} onChange={e => { setCompare(e.target.checked); if (!e.target.checked) setThrough(""); }} />与相邻上月比较</label>
      {compare && <label>两月均取前N天（留空比较整月）<input type="number" min="1" max="31" value={through} onChange={e => setThrough(e.target.value)} /></label>}
      <button className="button primary" type="submit">{loading ? "正在分析，可切换条件" : "分析消费样本"}</button>
    </form>
    {loading && <p role="status">正在读取分析工具结果…</p>}
    {error && <p className="alert tone-red" role="alert">{error}</p>}
    {errorAudit && <Link className="button secondary" to={`/audit?request_id=${encodeURIComponent(errorAudit)}`}>查看失败查询审计</Link>}
    {data && <section className="panel">{data.audit_warning && <p className="alert" role="alert">{data.audit_warning}</p>}<AnalysisView key={`${state.account_id}-${query}-${reload}`} result={data} auditId={data.audit_id} /></section>}
  </>;
}
