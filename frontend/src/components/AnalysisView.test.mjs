// Vite只负责转换TSX；用真实React服务端渲染验证组件，不引入浏览器测试依赖。
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import { createServer } from "vite";

let server, AnalysisView, RecordsResult;
before(async () => {
  server = await createServer({ server: { middlewareMode: true, watch: null }, appType: "custom" });
  ({ AnalysisView, RecordsResult } = await server.ssrLoadModule("/src/components/AnalysisView.tsx"));
});
after(async () => { await server?.close(); });
const dataset = { dataset_id: "sample", account_id: "1001", source: "synthetic_demo", version: "v1", coverage_start: "2026-09-01", coverage_end_exclusive: "2026-10-21" };
const report = () => ({ account_id: "1001", dataset, time_zone: "Asia/Shanghai", period: { start_date: "2026-09-01", end_date_exclusive: "2026-10-01" }, coverage: { complete: true, note: "模拟样本", covered_days: 30, requested_days: 30 }, summary: { count: 8, amount: "2140.49" }, categories: [{ category: "dining", label: "餐饮", count: 3, amount: "117.00", share_percent: "5.47" }], merchants: [{ merchant_id: "cafe", merchant_name: "演示咖啡店", count: 1, amount: "48.50", share_percent: "2.27" }], daily_trend: [{ date: "2026-09-01", covered: true, count: 1, amount: "32.50", share_percent: "1.52" }], anomalies: [{ rule_id: "LARGE_PURCHASE_V1", reason: "核对用途", record_ids: ["sep15"] }], anomaly_disclaimer: "不代表诈骗或欺诈" });
const render = (component, props) => renderToStaticMarkup(createElement(MemoryRouter, null, createElement(component, props)));

test("analysis cards render backend amounts, source, chart, trace and audit", () => {
  const html = render(AnalysisView, { result: { analysis: report() }, auditId: "audit-test" });
  for (const text of ["2,140.49", "餐饮", "演示咖啡店", "每日消费趋势", "LARGE_PURCHASE_V1", "sep15", "模拟消费分析样本，不参与当前账户余额核算", "audit-test"]) assert.ok(html.includes(text), text);
  assert.ok(!html.includes("执行凭据"));
});
test("partial coverage comparison shows unknown instead of misleading growth", () => {
  const current = report(); current.coverage = { complete: false, note: "仅覆盖前20天", covered_days: 20, requested_days: 31 };
  current.daily_trend = [{ date: "2026-10-21", covered: false, count: null, amount: null, share_percent: null }];
  const html = render(AnalysisView, { result: { comparison: { current, previous: report(), comparable: false, through_day: null } } });
  assert.ok(html.includes("覆盖不足")); assert.ok(html.includes("不能据此生成整月增幅")); assert.ok(html.includes("金额未知"));
});
test("merchant text is escaped and empty data stays explicit", () => {
  const value = report(); value.merchants[0].merchant_name = "<script>转账</script>"; value.summary.count = 0;
  const html = render(AnalysisView, { result: { analysis: value } });
  assert.ok(html.includes("&lt;script&gt;")); assert.ok(!html.includes("<script>")); assert.ok(html.includes("未覆盖日期不代表零消费"));
});
test("detail pagination uses backend total and loading disables navigation", () => {
  const data = { audit_id: "record-audit", spending_records: { dataset, records: [], total_count: 25 } };
  const first = render(RecordsResult, { data, offset: 0, loading: false, onPage() {} });
  assert.ok(first.includes("匹配25条")); assert.ok(first.includes('disabled="">上一页')); assert.ok(!first.includes('disabled="">下一页'));
  const loading = render(RecordsResult, { data, offset: 10, loading: true, onPage() {} });
  assert.ok(loading.includes('disabled="">上一页')); assert.ok(loading.includes('disabled="">下一页')); assert.ok(loading.includes("record-audit"));
});
