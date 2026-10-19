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
  response_kind: "business_result",
  conversation_id: "test-conversation",
  status: "pending",
  message: "test",
  reply: "test",
  tool_results: [],
  decisions: [
    {
      operation: "transfer_money",
      risk_level: "yellow",
      risk_reason: "test",
      permission_action: "pending_confirmation",
      require_confirmation: true,
      require_verification: false,
    },
  ],
  risk_level: "yellow",
  permission_action: "pending_confirmation",
  require_confirmation: true,
  require_verification: false,
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

const greeting: SecureResponse = {
  response_kind: "conversation",
  conversation_id: "test-conversation",
  status: "completed",
  message: "你好",
  reply: "你好",
  tool_results: [],
  decisions: [],
  request_id: "greeting-chain",
  audit_id: "greeting-chain",
};
test("conversation preserves pending parameters, original decision and audit link", () => {
  const before = demoReducer(initialState, {
    type: "chat",
    response: decision,
  });
  const after = demoReducer(before, { type: "chat", response: greeting });
  assert.equal(after.pendingAction, before.pendingAction);
  assert.equal(after.pendingDecision, decision);
  assert.equal(after.pendingDecision?.audit_id, "test-chain");
  assert.equal(after.currentDecision, decision);
  assert.equal(after.executionResult, null);
  assert.equal(after.audit_id, "greeting-chain");
});
test("read-only query can update latest decision without replacing the pending authorization", () => {
  const before = demoReducer(initialState, {
    type: "chat",
    response: decision,
  });
  const query: SecureResponse = {
    ...greeting,
    response_kind: "business_result",
    decisions: [
      {
        operation: "get_balance",
        risk_level: "green",
        risk_reason: "query",
        permission_action: "allow",
        require_confirmation: false,
        require_verification: false,
      },
    ],
  };
  const after = demoReducer(before, { type: "chat", response: query });
  assert.equal(after.currentDecision, query);
  assert.equal(after.pendingDecision, decision);
  assert.equal(after.pendingAction?.action_id, pending.action_id);
});
test("unexpected second pending response cannot overwrite the original task", () => {
  const before = demoReducer(initialState, {
    type: "chat",
    response: decision,
  });
  const after = demoReducer(before, {
    type: "chat",
    response: {
      ...decision,
      action_id: "other",
      pending_action: {
        ...pending,
        action_id: "other",
        params: { amount: "999", to_account_id: "other" },
      },
    },
  });
  assert.deepEqual(after.pendingAction, pending);
  assert.equal(after.pendingDecision, decision);
});
test("confirm after conversation retains original decision and real receipt", () => {
  const before = demoReducer(
    demoReducer(initialState, { type: "chat", response: decision }),
    { type: "chat", response: greeting },
  );
  const result = { ok: true, simulated: true, transfer_id: "isolated-receipt" };
  const after = demoReducer(before, {
    type: "action",
    response: {
      status: "completed",
      message: "test",
      audit_id: "test-chain",
      request_id: "test-chain",
      action_id: pending.action_id,
      execution_result: result,
    },
  });
  assert.equal(after.currentDecision, decision);
  assert.equal(after.pendingAction, null);
  assert.equal(after.pendingDecision, null);
  assert.equal(after.executionResult, result);
  const greeted = demoReducer(after, { type: "chat", response: greeting });
  assert.equal(greeted.executionResult, result);
  assert.equal(greeted.actionResponse, after.actionResponse);
});
test("new conversation cannot discard a pending task or clear an uncertainty lock", () => {
  const before = demoReducer(initialState, {
    type: "chat",
    response: decision,
  });
  assert.equal(demoReducer(before, { type: "newConversation" }), before);
  const unknown = demoReducer(initialState, {
    type: "error",
    message: "unknown",
    uncertain: true,
  });
  assert.equal(demoReducer(unknown, { type: "newConversation" }), unknown);
  const clean = demoReducer(initialState, { type: "chat", response: greeting });
  assert.equal(
    demoReducer(clean, { type: "newConversation" }).conversation_id,
    null,
  );
});
