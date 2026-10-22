import { test } from "node:test";
import assert from "node:assert/strict";
import { analysisQuery, recordsQuery, barPercent, displayAmount } from "./analysisQuery.ts";
import { LatestRead } from "./latestRead.ts";
import { demoReducer, hasExecutionReceipt, initialState } from "./demoReducer.ts";
import type { SecureResponse } from "../api/types.ts";

test("analysis requests pin sample source and explicit comparison window", () => {
  const q = analysisQuery("2026-10", true, "20");
  assert.equal(q.get("source"), "synthetic_demo");
  assert.equal(q.get("mode"), "compare");
  assert.equal(q.get("through_day"), "20");
  assert.equal(analysisQuery("this_month").get("period"), "this_month");
  assert.equal(analysisQuery("2026-10-01/2026-10-21").get("mode"), "summary");
});
test("invalid comparison windows are not silently reinterpreted", () => {
  for (const fn of [() => analysisQuery(""), () => analysisQuery("recent", true), () => analysisQuery("2026-10-01/2026-10-21", true), () => analysisQuery("2026-10", true, "32"), () => analysisQuery("2026-10", false, "20")]) assert.throws(fn);
});
test("record paging keeps filters and provenance", () => {
  const q = recordsQuery("2026-09", 10, "dining", "cafe");
  assert.equal(q.get("offset"), "10");
  assert.equal(q.get("category"), "dining");
  assert.equal(q.get("merchant_id"), "cafe");
  assert.equal(q.get("source"), "synthetic_demo");
  assert.equal(recordsQuery("2026-10", 0, undefined, undefined, "oct20-a").get("record_id"), "oct20-a");
});
test("amount display retains decimal strings without floating point conversion", () => {
  assert.equal(displayAmount("90090000000000.01"), "90,090,000,000,000.01");
  assert.equal(displayAmount("-720.52"), "-720.52");
  assert.equal(displayAmount(null), "—");
  assert.equal(barPercent("35.73"), 35.73);
  assert.equal(barPercent(null), 0);
});
test("switching analysis filters prevents old result publication", async () => {
  const reads = new LatestRead();
  let old!: () => void;
  const published: string[] = [];
  const first = reads.run(async latest => { await new Promise<void>(r => { old = r; }); if (latest()) published.push("September"); }, true);
  await reads.run(async latest => { if (latest()) published.push("October"); }, true);
  old(); await first;
  assert.deepEqual(published, ["October"]);
});
test("analysis chat result retains pending transfer and cannot count as a receipt", () => {
  const pending = { action_id: "pending", account_id: "1001", action: "transfer_money" as const, params: { to_account_id: "1002", amount: "500" }, risk_level: "yellow" as const, created_at: "2026-10-20T00:00:00Z" };
  const response: SecureResponse = { response_kind: "business_result", conversation_id: "conversation", status: "completed", message: "analysis", reply: "analysis", tool_results: [{ tool: "analyze_spending", result: { ok: true } }], request_id: "read", audit_id: "read", decisions: [{ operation: "analyze_spending", risk_level: "green", risk_reason: "read", permission_action: "allow", require_confirmation: false, require_verification: false }] };
  const state = demoReducer({ ...initialState, pendingAction: pending }, { type: "chat", response });
  assert.deepEqual(state.pendingAction, pending);
  assert.equal(hasExecutionReceipt({ status: "completed", message: "analysis", execution_result: { ok: true }, audit_id: "read", request_id: "read" }), false);
});
