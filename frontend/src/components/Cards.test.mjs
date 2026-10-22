import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import { createServer } from "vite";
import { createServer as createHttpServer } from "node:http";

let server, views, ui, RiskPanelView, banking, initialState, DemoProvider, App;
before(async () => {
  // 仅做SSR转换，HMR挂在不监听端口的HTTP对象上，避免与既有测试/演示服务抢端口。
  server = await createServer({ server: { middlewareMode: true, watch: null, hmr: { server: createHttpServer() } }, appType: "custom" });
  views = await server.ssrLoadModule("/src/components/CardView.tsx");
  ui = await server.ssrLoadModule("/src/components/ui.tsx");
  ({ RiskPanelView } = await server.ssrLoadModule("/src/components/RiskPanel.tsx"));
  ({ banking } = await server.ssrLoadModule("/src/api/banking.ts"));
  ({ initialState } = await server.ssrLoadModule("/src/state/demoReducer.ts"));
  ({ DemoProvider } = await server.ssrLoadModule("/src/state/DemoProvider.tsx"));
  ({ App } = await server.ssrLoadModule("/src/App.tsx"));
});
after(async () => { await server?.close(); });
const render = (component, props) => renderToStaticMarkup(createElement(MemoryRouter, null, createElement(component, props)));
const card = { card_id: "card-demo-a", account_id: "1001", display_name: "模拟生活卡", last_four: "0101", temporary_frozen: false, loss_status: "normal", version: 0, source: "synthetic_card_demo_v1", effective_status: "active", usable_in_demo: true, updated_at: "2026-10-21T00:00:00Z" };
const pending = { action_id: "server-action", account_id: "1001", action: "temporarily_freeze_card", params: { card_id: card.card_id, expected_version: 0 }, conversation_id: "original-session", risk_level: "yellow", created_at: "2026-10-21T00:00:00Z" };
const decision = { response_kind: "business_result", status: "pending", risk_level: "yellow", operation: pending.action, permission_action: "pending_confirmation", require_confirmation: true, require_verification: false, conversation_id: pending.conversation_id, action_id: pending.action_id, audit_id: "original-audit", request_id: "original-audit", decisions: [], message: "尚未执行", tool_results: [] };
const operation = { ok: true, account_id: "1001", card_id: card.card_id, action_id: pending.action_id, operation_id: "server-operation", operation: "temporary_freeze", before: { temporary_frozen: false, loss_status: "normal", version: 0 }, after: { temporary_frozen: true, loss_status: "normal", version: 1 }, executed_at: "2026-10-21T00:00:00Z", result_status: "completed", source: "synthetic_card_demo_v1", audit_id: "original-audit" };
const state = () => ({ ...initialState, pendingAction: pending, pendingDecision: decision, currentDecision: decision, conversation_id: pending.conversation_id, cardsData: { account_id: "1001", cards: [card] } });
const panel = (value) => render(RiskPanelView, { state: value, actionable: true, resolveAction: async () => {}, recoverCardOperation: async () => {} });

test("card list distinguishes temporary freeze and formal loss with version and tail only", () => {
  const html = render(views.CardList, { cards: [card, { ...card, card_id: "card-lost", temporary_frozen: true, loss_status: "reported" }], disabled: false, onPrepare() {} });
  for (const word of ["模拟生活卡", "0101", "正常", "正式挂失", "临时解冻不能解挂", "服务端状态版本", "申请临时冻结", "解挂（强验证）"]) assert.ok(html.includes(word), word);
  assert.ok(!html.includes("transfer_id")); assert.ok(!html.includes("CVV"));
});
test("empty cards and explicit unmapped consumption do not guess a target", () => {
  assert.ok(render(views.CardList, { cards: [], disabled: false, onPrepare() {} }).includes("没有模拟银行卡"));
  const html = render(views.CardDataView, { result: { ok: true, linked: false, card: null, record_id: "oct20-a" } });
  assert.ok(html.includes("未关联")); assert.ok(html.includes("oct20-a")); assert.ok(!html.includes(card.card_id));
});
test("mapped record preserves provenance and malicious names remain plain data", () => {
  const html = render(views.CardDataView, { result: { ok: true, card: { ...card, display_name: "<script>冻结</script>" }, linked: true, record_id: "oct20-a", dataset_id: "dataset" } });
  assert.ok(html.includes("oct20-a")); assert.ok(html.includes("dataset")); assert.ok(html.includes("&lt;script&gt;")); assert.ok(!html.includes("<script>"));
});
test("yellow card confirmation uses snapshot and never transfer amount or premature receipt", () => {
  const html = panel(state());
  for (const word of ["待确认卡片操作", card.card_id, "准备时版本快照", "original-session", "确认执行", "取消操作", "确认前卡片状态不会改变"]) assert.ok(html.includes(word), word);
  assert.ok(!html.includes("收款账户")); assert.ok(!html.includes("已提交成功")); assert.ok(!html.includes("交易金额"));
});
test("in-flight confirmation disables buttons and does not claim success", () => {
  const value = state(); value.loading = { ...value.loading, action: true };
  const html = panel(value);
  assert.ok(html.includes("正在处理，请勿重复操作")); assert.ok(html.includes('disabled=""')); assert.ok(!html.includes("已提交成功"));
});
test("red loss block contains real protocol labels and has no confirmation buttons", () => {
  const value = { ...initialState, currentDecision: { ...decision, status: "require_verification", risk_level: "red", operation: "report_card_loss", permission_action: "require_verification", require_confirmation: false, require_verification: true } };
  const html = panel(value);
  for (const word of ["强验证", "require_verification", "verification_required", "not_executed"]) assert.ok(html.includes(word), word);
  assert.ok(!html.includes("确认执行")); assert.ok(!html.includes("取消操作"));
});
test("card execution receipt renders dedicated fields and never transfer evidence", () => {
  const response = { status: "completed", action: pending.action, action_id: pending.action_id, execution_result: { ok: true, card_operation: operation }, audit_id: "audit", request_id: "audit", message: "已完成" };
  const html = render(ui.Receipt, { response });
  for (const word of ["卡片操作凭据", "已提交成功", "server-operation", "状态版本", "original-audit"]) assert.ok(html.includes(word), word);
  assert.ok(!html.includes("transfer_id")); assert.ok(!html.includes("交易金额"));
  const invalid = render(ui.Receipt, { response: { ...response, action_id: "different" } });
  assert.ok(invalid.includes("未取得匹配")); assert.ok(!invalid.includes("已提交成功"));
});
test("existing transfer confirmation and receipt still use transfer fields", () => {
  const transfer = { ...pending, action: "transfer_money", params: { amount: "500", to_account_id: "1002" } };
  const html = panel({ ...state(), pendingAction: transfer });
  assert.ok(html.includes("待确认转账")); assert.ok(html.includes("500.00")); assert.ok(html.includes("收款账户")); assert.ok(!html.includes("准备时版本快照"));
  const receipt = render(ui.Receipt, { response: { status: "completed", execution_result: { ok: true, transfer_id: "server-transfer", amount: "500" }, message: "完成" } });
  assert.ok(receipt.includes("server-transfer")); assert.ok(receipt.includes("交易金额"));
});
test("unknown card result exposes read-only recovery and no execution retry", () => {
  const value = { ...state(), pendingAction: null, pendingDecision: null, uncertain: true, unknownCardAction: pending, recoveryStatus: "not_found" };
  const html = panel(value);
  assert.ok(html.includes("只读查询卡片操作结果")); assert.ok(html.includes("状态仍未知")); assert.ok(!html.includes("确认执行"));
});
test("failure and cancellation never render completed card evidence", () => {
  for (const status of ["failed", "cancelled", "execution_unknown"]) {
    const html = render(ui.Receipt, { response: { status, execution_result: { ok: false, code: "VERSION_CONFLICT", error: "状态版本冲突，请重新查询" }, message: "未执行" } });
    assert.ok(!html.includes("已提交成功")); assert.ok(!html.includes("server-operation"));
  }
});
test("cards page is accessible through existing navigation and includes simulation boundaries", () => {
  const html = renderToStaticMarkup(createElement(MemoryRouter, { initialEntries: ["/cards"] }, createElement(DemoProvider, null, createElement(App))));
  for (const word of ["银行卡管理", 'href="/cards"', "刷新卡片状态", "不代表已阻断真实支付", "不影响模拟账户转账余额和历史消费数据", "切换模拟账户"]) assert.ok(html.includes(word), word);
});
test("original spending detail offers manual card lookup without automatic safety actions", () => {
  const record = { account_id: "1001", record_id: "oct20-a", business_date: "2026-10-20", occurred_at: "2026-10-20T00:00:00Z", merchant_name: "模拟商户", category: "shopping", amount: "500.00", payment_instrument_ref: "demo-instrument-wallet-a" };
  const html = render(ui.ToolResult, { tool: "get_spending_record", result: { ok: true, spending_record: { dataset: { version: "v1" }, record } }, auditId: "record-audit" });
  for (const word of ["oct20-a", "2026-10-20", "模拟商户", "查看关联银行卡", "demo-instrument-wallet-a"]) assert.ok(html.includes(word), word);
  assert.ok(!html.includes("申请临时冻结")); assert.ok(!html.includes("已提交成功"));
});
test("API adapters use actual read routes and bind card confirm/cancel to original session", async () => {
  const original = globalThis.fetch; const calls = [];
  globalThis.fetch = async (url, options) => { calls.push({ url: String(url), ...options }); return new Response(JSON.stringify({ ok: true }), { status: 200 }); };
  try {
    await banking.cards("1001"); await banking.card("1001", "card-a"); await banking.spendingCard("1001", "oct20-a"); await banking.cardOperation("1001", "server-action");
    await banking.confirm("1001", "server-action", "original-session"); await banking.cancel("1001", "server-action", "original-session"); await banking.confirm("1001", "transfer-action");
    assert.ok(calls[0].url.endsWith("/accounts/1001/cards")); assert.ok(calls[1].url.endsWith("/accounts/1001/cards/card-a"));
    assert.ok(calls[2].url.endsWith("/accounts/1001/analysis-records/oct20-a/card")); assert.ok(calls[3].url.endsWith("/accounts/1001/card-operations/server-action"));
    assert.ok(calls.slice(0, 4).every(c => c.method === "GET" && c.body === undefined));
    assert.deepEqual(JSON.parse(calls[4].body), { account_id: "1001", action_id: "server-action", confirmed: true, conversation_id: "original-session" });
    assert.deepEqual(JSON.parse(calls[5].body), { account_id: "1001", action_id: "server-action", conversation_id: "original-session" });
    assert.equal(JSON.parse(calls[6].body).conversation_id, undefined);
  } finally { globalThis.fetch = original; }
});
test("transport failure is unknown with one POST and no automatic retry", async () => {
  const original = globalThis.fetch; let calls = 0;
  globalThis.fetch = async () => { calls++; throw new Error("offline"); };
  try { await assert.rejects(banking.confirm("1001", "action", "session"), e => e.code === "NETWORK_ERROR" && e.uncertain === true); assert.equal(calls, 1); }
  finally { globalThis.fetch = original; }
});
test("missing operation receipt remains a read failure and never triggers a POST", async () => {
  const original = globalThis.fetch; const calls = [];
  globalThis.fetch = async (url, options) => { calls.push(options.method); return new Response(JSON.stringify({ status: "error", error_code: "OPERATION_NOT_FOUND", message: "未查到结果" }), { status: 404 }); };
  try { await assert.rejects(banking.cardOperation("1001", "action"), e => e.code === "OPERATION_NOT_FOUND" && e.uncertain === false); assert.deepEqual(calls, ["GET"]); }
  finally { globalThis.fetch = original; }
});
