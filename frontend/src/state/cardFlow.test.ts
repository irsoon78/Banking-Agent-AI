import { test } from "node:test";
import assert from "node:assert/strict";
import type { ActionResponse, Card, CardOperation, CardPendingAction, CardResponse, SecureResponse } from "../api/types.ts";
import { actionArguments, canResolve, hasCardReceipt, isCardPending, matchesRecovery, MutationGate, validCardOperation } from "./cardFlow.ts";
import { demoReducer, hasExecutionReceipt, initialState } from "./demoReducer.ts";

const pending: CardPendingAction = { action: "temporarily_freeze_card", account_id: "1001", action_id: "card-action", conversation_id: "original-session", params: { card_id: "card-demo-a", expected_version: 0 }, created_at: "2026-10-21T00:00:00Z", risk_level: "yellow" };
const decision: SecureResponse = { response_kind: "business_result", status: "pending", conversation_id: pending.conversation_id, action_id: pending.action_id, pending_action: pending, request_id: "audit-original", audit_id: "audit-original", message: "尚未执行", reply: "尚未执行", tool_results: [], decisions: [], risk_level: "yellow", permission_action: "pending_confirmation", require_confirmation: true, require_verification: false };
const card: Card = { card_id: pending.params.card_id, account_id: "1001", display_name: "模拟生活卡", last_four: "0101", temporary_frozen: false, loss_status: "normal", version: 0, created_at: "2026-10-01T00:00:00Z", updated_at: "2026-10-01T00:00:00Z", source: "synthetic_card_demo_v1", effective_status: "active", usable_in_demo: true };
const cards: CardResponse = { ok: true, account_id: "1001", cards: [card], request_id: "query", audit_id: "query", decisions: [], tool: "list_cards" };
const operation: CardOperation = { ok: true, operation_id: "server-operation", action_id: pending.action_id, card_id: card.card_id, account_id: "1001", operation: "temporary_freeze", before: { version: 0, loss_status: "normal", temporary_frozen: false }, after: { version: 1, loss_status: "normal", temporary_frozen: true }, executed_at: "2026-10-21T00:00:00Z", result_status: "completed", source: "synthetic_card_demo_v1", audit_id: "audit-original" };
const response = (status: ActionResponse["status"] = "completed"): ActionResponse => ({ status, action: pending.action, action_id: pending.action_id, request_id: "audit-original", audit_id: "audit-original", message: "后端响应", execution_result: { ok: true, card_operation: operation } });
const prepared = () => demoReducer({ ...initialState, cardsData: cards }, { type: "chat", response: { ...decision, decisions: [{ operation: pending.action, risk_level: "yellow", risk_reason: "确认", permission_action: "pending_confirmation", require_confirmation: true, require_verification: false }] } });
const unknown = () => demoReducer(prepared(), { type: "action", response: { ...response("execution_unknown"), execution_result: { ok: false, code: "EXECUTION_UNKNOWN" } } });

test("card task discrimination and confirmation arguments retain original server bindings", () => {
  assert.equal(isCardPending(pending), true);
  assert.deepEqual(actionArguments(pending), ["1001", "card-action", "original-session"]);
  assert.deepEqual(actionArguments({ ...pending, action: "transfer_money", params: { amount: "500", to_account_id: "1002" } }), ["1001", "card-action"]);
});
test("confirmation requires exact account session task and yellow permission", () => {
  assert.equal(canResolve(pending, decision, "1001", pending.conversation_id), true);
  for (const changed of [{ status: "require_verification" }, { risk_level: "red" }, { permission_action: "allow" }, { require_confirmation: false }, { require_verification: true }, { action_id: "other" }])
    assert.equal(canResolve(pending, { ...decision, ...changed } as SecureResponse, "1001", pending.conversation_id), false);
  assert.equal(canResolve(pending, decision, "1002", pending.conversation_id), false);
  assert.equal(canResolve(pending, decision, "1001", "wrong-session"), false);
});
test("prepare and cancel never optimistically freeze a card or create a receipt", () => {
  const before = prepared();
  assert.equal(before.cardsData?.cards?.[0].temporary_frozen, false);
  const after = demoReducer(before, { type: "action", response: { ...response("cancelled"), execution_result: {} } });
  assert.equal(after.cardsData?.cards?.[0].temporary_frozen, false);
  assert.equal(after.pendingAction, null);
  assert.equal(hasCardReceipt(after.actionResponse!), false);
});
test("successful card receipt and transfer receipt are mutually distinct", () => {
  assert.equal(hasCardReceipt(response()), true);
  assert.equal(hasExecutionReceipt(response()), false);
  assert.equal(hasExecutionReceipt({ ...response(), execution_result: { ok: true, transfer_id: "forged-for-card" } }), false);
  assert.equal(hasCardReceipt({ ...response(), action: "transfer_money" }), false);
  for (const status of ["failed", "cancelled", "execution_unknown", "require_verification"] as const) assert.equal(hasCardReceipt(response(status)), false);
});
test("invalid or mismatched card evidence is not displayed as committed", () => {
  assert.equal(validCardOperation({ ...operation, source: "untrusted" } as unknown as CardOperation), false);
  assert.equal(validCardOperation({ ...operation, after: { ...operation.after, version: 8 } }), false);
  assert.equal(validCardOperation({ ...operation, after: { ...operation.after, loss_status: "reported" } }), false);
  assert.equal(hasCardReceipt({ ...response(), action_id: "other" }), false);
});
test("expired and conflicting task results clear task but do not fabricate state", () => {
  for (const code of ["ACTION_EXPIRED", "VERSION_CONFLICT", "CARD_REPORTED_LOST", "SESSION_MISMATCH"]) {
    const after = demoReducer(prepared(), { type: "action", response: { ...response("failed"), error_code: code, execution_result: { ok: false, code } } });
    assert.equal(after.pendingAction, null); assert.equal(after.cardsData?.cards?.[0].version, 0); assert.equal(after.actionResponse?.error_code, code);
  }
});
test("ordinary queries and page-level reads preserve pending card task", () => {
  const state = prepared();
  const read = demoReducer(state, { type: "cards", account: "1001", response: cards });
  assert.deepEqual(read.pendingAction, pending);
  assert.equal(demoReducer(read, { type: "newConversation" }), read);
});
test("unknown result stays locked after reads or not-found recovery", () => {
  let state = unknown();
  assert.deepEqual(state.unknownCardAction, pending);
  state = demoReducer(state, { type: "cards", account: "1001", response: cards });
  state = demoReducer(state, { type: "recoveryStatus", status: "not_found" });
  assert.equal(state.uncertain, true);
  assert.equal(state.recoveryStatus, "not_found");
});
test("network-unknown confirmation retains original action for read-only recovery", () => {
  const state = demoReducer(prepared(), { type: "error", message: "connection lost", uncertain: true, unknownCardAction: pending });
  assert.equal(state.pendingAction?.action_id, pending.action_id);
  assert.equal(state.unknownCardAction?.conversation_id, pending.conversation_id);
  assert.equal(state.uncertain, true);
});
test("only matching persisted receipt can recover an unknown card operation", () => {
  for (const changes of [{ account_id: "1002" }, { card_id: "card-other" }, { action_id: "other" }, { before: { ...operation.before, version: 3 } }])
    assert.equal(matchesRecovery({ ...operation, ...changes }, pending), false);
  const read: CardResponse = { ok: true, card_operation: operation, request_id: "query", audit_id: "query", decisions: [], tool: "get_card_operation" };
  const before = unknown();
  assert.equal(demoReducer(before, { type: "recoveredCard", account: "1002", response: read }), before);
  const after = demoReducer(before, { type: "recoveredCard", account: "1001", response: read });
  assert.equal(after.uncertain, false); assert.equal(after.recoveryStatus, "completed");
  assert.equal(hasCardReceipt(after.actionResponse!), true); assert.equal(after.audit_id, "audit-original");
  // 查询操作凭据与读取卡状态是两件事，只有后续服务端列表可更新卡状态。
  assert.equal(after.cardsData?.cards?.[0].temporary_frozen, false);
  const refreshed = demoReducer(after, { type: "cards", account: "1001", response: { ...cards, cards: [{ ...card, temporary_frozen: true, version: 1 }] } });
  assert.equal(refreshed.cardsData?.cards?.[0].temporary_frozen, true);
});
test("account switching clears cards chat associations and ignores old responses", () => {
  const before = { ...initialState, cardsData: cards, balance: "10000", messages: [{ id: "old", role: "assistant" as const, text: "old card" }] };
  const after = demoReducer(before, { type: "account", account: "1002" });
  assert.equal(after.cardsData, null); assert.deepEqual(after.messages, []); assert.equal(after.balance, null);
  assert.equal(demoReducer(after, { type: "cards", account: "1001", response: cards }), after);
  assert.equal(demoReducer(after, { type: "overview", account_id: "1001", health: null, error: null, updatedAt: "old" }), after);
});
test("pending or unknown work prevents silent account/task loss", () => {
  const state = prepared(); assert.equal(demoReducer(state, { type: "account", account: "1002" }), state);
  const locked = unknown(); assert.equal(demoReducer(locked, { type: "account", account: "1002" }), locked);
});
test("mixed-account card rows cannot publish even with matching envelope", () => {
  const after = demoReducer(initialState, { type: "cards", account: "1001", response: { ...cards, cards: [{ ...card, account_id: "1002" }] } });
  assert.equal(after, initialState);
});
test("unknown or red card operation cannot gain a confirmation button via yellow fields", () => {
  for (const action of ["report_card_loss", "cancel_card_loss_report", "unregistered"]) {
    const forged = { ...pending, action } as unknown as CardPendingAction;
    assert.equal(canResolve(forged, decision, "1001", pending.conversation_id), false);
  }
});
test("failed card refresh removes stale status instead of claiming success", () => {
  const after = demoReducer({ ...initialState, cardsData: cards }, { type: "cardsError", account: "1001", message: "offline" });
  assert.equal(after.cardsData, null); assert.equal(after.cardsError, "offline");
});
test("synchronous mutation gate prevents duplicate requests before re-render", async () => {
  const gate = new MutationGate(); let requests = 0; let finish!: () => void;
  const resolve = async () => { if (!gate.claim()) return; try { requests++; await new Promise<void>(r => { finish = r; }); } finally { gate.release(); } };
  const first = resolve(); await resolve(); assert.equal(requests, 1); finish(); await first; assert.equal(gate.busy, false);
});
test("new task or conversation cannot reuse a previous recovery success notice", () => {
  const before = { ...initialState, recoveryStatus: "completed" as const };
  assert.equal(demoReducer(before, { type: "newConversation" }).recoveryStatus, "idle");
  assert.equal(demoReducer(before, { type: "chat", response: decision }).recoveryStatus, "idle");
});
