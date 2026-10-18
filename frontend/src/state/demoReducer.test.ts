import { test } from "node:test";
import assert from "node:assert/strict";
import { demoReducer, initialState } from "./demoReducer.ts";
import type { AuditLog, PendingAction, SecureResponse } from "../api/types.ts";

const pending: PendingAction = {
  action_id: "test-action",
  account_id: "1001",
  action: "transfer_money",
  params: { to_account_id: "1002", amount: "12.34" },
  risk_level: "yellow",
  created_at: "2026-01-01T00:00:00Z",
};
const decision: SecureResponse = {
  status: "pending",
  message: "test",
  reply: "test",
  tool_results: [],
  decisions: [],
  audit_id: "test-chain",
  request_id: "test-chain",
  action_id: pending.action_id,
  pending_action: pending,
};
test("read-only account/audit updates retain the pending task and original parameters", () => {
  const withPending = demoReducer(initialState, {
    type: "chat",
    response: decision,
  });
  const withAudit = demoReducer(withPending, {
    type: "auditStart",
    filter: "other-chain",
    offset: 0,
  });
  const withReads = demoReducer(withAudit, {
    type: "overview",
    health: null,
    error: "offline",
    updatedAt: "now",
  });
  assert.deepEqual(withReads.pendingAction, pending);
  assert.equal(withReads.audit_id, "test-chain");
});
test("cancel clears pending without creating any execution receipt", () => {
  const before = demoReducer(initialState, {
    type: "chat",
    response: decision,
  });
  const after = demoReducer(before, {
    type: "action",
    response: {
      status: "cancelled",
      message: "未执行",
      execution_result: {},
      audit_id: "test-chain",
      request_id: "test-chain",
    },
  });
  assert.equal(after.pendingAction, null);
  assert.deepEqual(after.executionResult, {});
  assert.equal(after.executionResult?.transfer_id, undefined);
  assert.equal(before.pendingAction?.action_id, pending.action_id);
});
test("unknown execution stays locked after subsequent read success or error clearing", () => {
  const unknown = demoReducer(initialState, {
    type: "error",
    message: "connection lost",
    uncertain: true,
  });
  const cleared = demoReducer(unknown, { type: "error", message: null });
  assert.equal(cleared.uncertain, true);
});
test("audit sorting uses numeric event id without mutating input or adding events", () => {
  const logs = [{ id: 12 }, { id: 10 }, { id: 11 }] as AuditLog[];
  const after = demoReducer(initialState, {
    type: "audit",
    logs,
    hasMore: false,
  });
  assert.deepEqual(
    after.auditLogs.map((log) => log.id),
    [10, 11, 12],
  );
  assert.deepEqual(
    logs.map((log) => log.id),
    [12, 10, 11],
  );
});
test("expired task is removed without inventing a success result", () => {
  const before = demoReducer(initialState, {
    type: "chat",
    response: decision,
  });
  const after = demoReducer(before, {
    type: "error",
    message: "ACTION_NOT_FOUND",
    invalidatePending: true,
  });
  assert.equal(after.pendingAction, null);
  assert.equal(after.executionResult, null);
});
