import type {
  Account,
  ActionResponse,
  AuditLog,
  Bill,
  Health,
  PendingAction,
  SecureResponse,
  ToolData,
  CardResponse,
  CardPendingAction,
} from "../api/types.ts";
import { isCardPending, matchesRecovery } from "./cardFlow.ts";

export interface Message {
  id: string;
  role: "user" | "assistant";
  text: string;
  response?: SecureResponse | ActionResponse;
  transportError?: boolean;
}
export type LoadKey = "overview" | "chat" | "action" | "audit" | "cards" | "recovery";
export interface DemoState {
  account_id: string;
  account: Account | null;
  balance: string | null;
  bills: Bill[];
  health: Health | null;
  messages: Message[];
  currentDecision: SecureResponse | null;
  pendingDecision: SecureResponse | null;
  conversation_id: string | null;
  pendingAction: PendingAction | null;
  audit_id: string | null;
  auditLogs: AuditLog[];
  executionResult: ToolData | null;
  actionResponse: ActionResponse | null;
  loading: Record<LoadKey, boolean>;
  error: string | null;
  overviewError: string | null;
  auditError: string | null;
  uncertain: boolean;
  updatedAt: string | null;
  auditFilter: string;
  auditOffset: number;
  auditHasMore: boolean;
  cardsData: CardResponse | null;
  cardsError: string | null;
  cardsErrorAudit: string | null;
  unknownCardAction: CardPendingAction | null;
  recoveryStatus: "idle" | "loading" | "completed" | "not_found" | "unknown";
}
export const initialState: DemoState = {
  account_id: "1001",
  account: null,
  balance: null,
  bills: [],
  health: null,
  messages: [],
  currentDecision: null,
  pendingDecision: null,
  conversation_id: null,
  pendingAction: null,
  audit_id: null,
  auditLogs: [],
  executionResult: null,
  actionResponse: null,
  loading: { overview: false, chat: false, action: false, audit: false, cards: false, recovery: false },
  error: null,
  overviewError: null,
  auditError: null,
  uncertain: false,
  updatedAt: null,
  auditFilter: "",
  auditOffset: 0,
  auditHasMore: false,
  cardsData: null,
  cardsError: null,
  cardsErrorAudit: null,
  unknownCardAction: null,
  recoveryStatus: "idle",
};
export type DemoAction =
  | { type: "loading"; key: LoadKey; value: boolean }
  | {
      type: "overview";
      account_id?: string;
      account?: Account;
      bills?: Bill[];
      health: Health | null;
      error: string | null;
      updatedAt: string;
    }
  | { type: "message"; message: Message }
  | { type: "chat"; response: SecureResponse }
  | { type: "newConversation" }
  | { type: "action"; response: ActionResponse }
  | { type: "account"; account: string }
  | { type: "cardsStart" }
  | { type: "cards"; account: string; response: CardResponse }
  | { type: "cardsError"; account: string; message: string; auditId?: string }
  | { type: "recoveryStatus"; status: DemoState["recoveryStatus"] }
  | { type: "recoveredCard"; account: string; response: CardResponse }
  | {
      type: "error";
      message: string | null;
      uncertain?: boolean;
      invalidatePending?: boolean;
      unknownCardAction?: CardPendingAction;
    }
  | { type: "auditStart"; filter: string; offset: number }
  | { type: "audit"; logs: AuditLog[]; hasMore: boolean }
  | { type: "auditError"; message: string };

export function hasExecutionReceipt(response: ActionResponse): boolean {
  return response.status === "completed" && response.execution_result.ok === true &&
    !response.execution_result.card_operation && response.action !== "temporarily_freeze_card" && response.action !== "remove_temporary_freeze" &&
    typeof response.execution_result.transfer_id === "string" && response.execution_result.transfer_id.trim().length > 0;
}

export function demoReducer(state: DemoState, action: DemoAction): DemoState {
  switch (action.type) {
    case "account":
      if (state.pendingAction || state.uncertain || state.loading.action || state.loading.chat || !["1001", "1002"].includes(action.account)) return state;
      return { ...initialState, account_id: action.account };
    case "cardsStart":
      return { ...state, cardsData: null, cardsError: null, cardsErrorAudit: null };
    case "cards":
      if (action.account !== state.account_id || action.response.account_id !== state.account_id || action.response.cards?.some(card => card.account_id !== state.account_id)) return state;
      return { ...state, cardsData: action.response, cardsError: null, cardsErrorAudit: null };
    case "cardsError":
      return action.account === state.account_id ? { ...state, cardsData: null, cardsError: action.message, cardsErrorAudit: action.auditId ?? null } : state;
    case "recoveryStatus":
      return { ...state, recoveryStatus: action.status };
    case "recoveredCard": {
      const pending = state.unknownCardAction;
      const receipt = action.response.card_operation;
      if (action.account !== state.account_id || !pending || !matchesRecovery(receipt, pending)) return state;
      const response: ActionResponse = { status: "completed", action: pending.action, action_id: pending.action_id,
        message: "已查询到此操作的持久化成功凭据，未重发写操作。", execution_result: { ok: true, card_operation: receipt },
        audit_id: receipt.audit_id ?? action.response.audit_id, request_id: receipt.request_id ?? action.response.request_id };
      return { ...state, uncertain: false, unknownCardAction: null, pendingAction: null, pendingDecision: null,
        recoveryStatus: "completed", actionResponse: response, executionResult: response.execution_result, audit_id: response.audit_id, error: null };
    }
    case "loading":
      return {
        ...state,
        loading: { ...state.loading, [action.key]: action.value },
      };
    case "overview":
      if (action.account_id && action.account_id !== state.account_id) return state;
      return {
        ...state,
        account: action.account ?? state.account,
        balance: action.account?.balance ?? state.balance,
        bills: action.bills ?? state.bills,
        health: action.health,
        overviewError: action.error,
        updatedAt: action.updatedAt,
      };
    case "message":
      return { ...state, messages: [...state.messages, action.message] };
    case "chat":
      const hasDecision = action.response.decisions.length > 0;
      return {
        ...state,
        conversation_id: action.response.conversation_id,
        currentDecision: hasDecision ? action.response : state.currentDecision,
        pendingAction:
          state.pendingAction ?? action.response.pending_action ?? null,
        pendingDecision: state.pendingAction
          ? state.pendingDecision
          : action.response.pending_action
            ? action.response
            : null,
        audit_id: action.response.audit_id,
        actionResponse:
          hasDecision && !state.pendingAction ? null : state.actionResponse,
        uncertain:
          state.uncertain || action.response.status === "execution_unknown",
        error: null,
        recoveryStatus: action.response.pending_action && !state.pendingAction ? "idle" : state.recoveryStatus,
      };
    case "action":
      const previousDecision = state.pendingDecision ?? state.currentDecision;
      return {
        ...state,
        currentDecision: previousDecision && action.response.decision
          ? { ...previousDecision, ...action.response.decision,
              status: action.response.status === "cancelled" ? previousDecision.status : action.response.status,
              message: action.response.message, decisions: [action.response.decision] }
          : previousDecision,
        actionResponse: action.response,
        pendingAction: null,
        pendingDecision: null,
        audit_id: action.response.audit_id,
        executionResult: action.response.execution_result,
        uncertain:
          state.uncertain || action.response.status === "execution_unknown",
        unknownCardAction: action.response.status === "execution_unknown" && state.pendingAction && isCardPending(state.pendingAction) ? state.pendingAction : state.unknownCardAction,
        recoveryStatus: action.response.status === "execution_unknown" ? "unknown" : "idle",
        error: null,
      };
    case "error":
      return {
        ...state,
        error: action.message,
        uncertain: state.uncertain || !!action.uncertain,
        unknownCardAction: action.unknownCardAction ?? state.unknownCardAction,
        pendingAction: action.invalidatePending ? null : state.pendingAction,
        pendingDecision: action.invalidatePending
          ? null
          : state.pendingDecision,
      };
    case "newConversation":
      if (
        state.pendingAction ||
        state.uncertain ||
        state.loading.chat ||
        state.loading.action
      )
        return state;
      return {
        ...state,
        conversation_id: null,
        messages: [],
        currentDecision: null,
        pendingDecision: null,
        actionResponse: null,
        executionResult: null,
        recoveryStatus: "idle",
        unknownCardAction: null,
        audit_id: null,
        error: null,
      };
    case "auditStart":
      return {
        ...state,
        auditFilter: action.filter,
        auditOffset: action.offset,
        auditLogs: [],
        auditError: null,
      };
    case "audit":
      return {
        ...state,
        auditLogs: [...action.logs].sort((a, b) => a.id - b.id),
        auditHasMore: action.hasMore,
        auditError: null,
      };
    case "auditError":
      return { ...state, auditLogs: [], auditError: action.message };
  }
}
