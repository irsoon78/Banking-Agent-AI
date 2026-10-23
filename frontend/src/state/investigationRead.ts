import type { InvestigationReport, InvestigationsResponse, CardResponse } from "../api/types.ts";
import type { LatestRead } from "./latestRead.ts";

export const executionLabel = (value?: string) => ({ running: "调查执行中", completed: "调查步骤已完成", partial: "调查部分完成", failed: "调查执行失败", interrupted: "调查已中断" }[value ?? ""] ?? "执行状态未提供");
export const coverageLabel = (value?: string) => ({ complete: "样本覆盖完整", incomplete: "数据覆盖不足", unknown: "覆盖状态无法确定" }[value ?? ""] ?? "覆盖状态未提供");
export const plannerLabel = (value?: string) => ({ model_planned: "模型参与受限规划", deterministic_fallback: "确定性降级", deterministic_no_choice: "无需模型选择" }[value ?? ""] ?? "规划模式未提供");
export const stepLabel = (value?: string) => ({ planned: "等待执行", running: "执行中 · 尚未完成", succeeded: "执行成功", failed: "执行失败", skipped: "被跳过", not_executed: "未执行" }[value ?? ""] ?? "状态未提供");
export const focusLabel = (value?: string) => ({ similar: "相似消费／重复扣款线索", large: "大额消费", all: "综合异常调查" }[value ?? ""] ?? "重点未提供");
export const validInvestigationId = (id: unknown): id is string => typeof id === "string" && /^[a-f0-9]{32}$/.test(id);

// 仅将服务端日期区间转换为便于阅读的交集；不重算金额、笔数或异常间隔。
export function investigationCoveragePeriod(report: InvestigationReport): string {
  const dates = [report.period?.start_date, report.period?.end_date_exclusive, report.dataset?.coverage_start, report.dataset?.coverage_end_exclusive];
  if (!dates.every(value => value && /^\d{4}-\d{2}-\d{2}$/.test(value) && Number.isFinite(Date.parse(value)) && new Date(value).toISOString().slice(0, 10) === value)) return "尚无可核对的样本覆盖区间";
  const [requestStart, requestEnd, datasetStart, datasetEnd] = dates as string[];
  const start = requestStart > datasetStart ? requestStart : datasetStart;
  const end = requestEnd < datasetEnd ? requestEnd : datasetEnd;
  if (start >= end) return "本次请求与样本覆盖区间没有交集";
  const inclusiveEnd = new Date(end + "T00:00:00Z");
  inclusiveEnd.setUTCDate(inclusiveEnd.getUTCDate() - 1);
  return `${start} 至 ${inclusiveEnd.toISOString().slice(0, 10)}（北京时间，含结束日）`;
}

export function validateInvestigation(report: InvestigationReport, account: string, id?: string): void {
  if (!report || report.schema_version !== "investigation.v1" || report.account_id !== account ||
      !validInvestigationId(report.investigation_id) || (id && report.investigation_id !== id) ||
      (report.dataset && report.dataset.account_id !== account)) throw new Error("调查报告或账户归属不匹配，已拒绝展示。");
  const arrays = [report.steps, report.findings, report.errors, report.planning?.events,
    report.evidence?.records, report.evidence?.links, report.evidence?.cards];
  if (arrays.some(items => items != null && (!Array.isArray(items) || items.some(item => !item || typeof item !== "object"))))
    throw new Error("调查证据结构无效，请重新查询报告。");
  if (report.findings?.some(e => e.finding && !Array.isArray(e.finding.record_ids)) ||
      report.steps?.some(e => e.depends_on != null && !Array.isArray(e.depends_on)) ||
      report.steps?.some(e => e.audit_event_ids != null && !Array.isArray(e.audit_event_ids)) ||
      report.evidence?.cards?.some(e => e.source_link_refs != null && !Array.isArray(e.source_link_refs)))
    throw new Error("调查步骤或证据引用结构无效，请重新查询报告。");
  if (report.evidence?.records?.some(e => e.record && (e.record.account_id !== account || e.record.record_id !== e.record_id)) ||
      report.evidence?.cards?.some(e => e.card && (e.card.account_id !== account || e.card.card_id !== e.card_id)))
    throw new Error("调查证据账户或目标不匹配，已拒绝展示。");
}
export function validateInvestigationList(data: InvestigationsResponse, account: string): void {
  if (data.account_id !== account || !Array.isArray(data.items) || data.items.some(item => !validInvestigationId(item?.investigation_id) || (item.dataset && item.dataset.account_id !== account)))
    throw new Error("历史调查账户或结构不匹配，已拒绝展示。");
}
export function validateLatestCard(data: CardResponse, account: string, id: string): void {
  if (data.ok !== true || data.account_id !== account || data.card?.account_id !== account || data.card.card_id !== id || !data.read_at)
    throw new Error("卡片查询未返回匹配账户、目标和读取时间，已拒绝展示。");
}

export interface InvestigationReadView<T> { scope: string; loading: boolean; data?: T; error?: string }
// 与组件共用同一代际控制；卸载/换账户invalidate，旧成功和旧错误均不能覆盖新视图。
export function readInvestigation<T>(reader: LatestRead, scope: string, load: () => Promise<T>,
    validate: (data: T) => void, update: (view: InvestigationReadView<T>) => void): Promise<void> {
  return reader.run(async isLatest => {
    update({ scope, loading: true });
    try {
      const data = await load();
      if (!isLatest()) return;
      validate(data);
      update({ scope, loading: false, data });
    } catch (error) {
      if (isLatest()) update({ scope, loading: false, error: error instanceof Error ? error.message : "只读查询失败，请检查服务连接。" });
    }
  }, true);
}

export function investigationLink(account: string, id: string): string {
  return `/chat?investigation_id=${encodeURIComponent(id)}&investigation_account=${encodeURIComponent(account)}`;
}
