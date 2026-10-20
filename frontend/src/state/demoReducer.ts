import type {
  Account,
  ActionResponse,
  AuditLog,
  Bill,
  Health,
  PendingAction,
  SecureResponse,
  ToolData,
} from "../api/types.ts";

export interface Message {
  id: string;
  role: "user" | "assistant";
  text: string;
  response?: SecureResponse | ActionResponse;
  transportError?: boolean;
}
export type LoadKey = "overview" | "chat" | "action" | "audit";
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
  loading: { overview: false, chat: false, action: false, audit: false },
  error: null,
  overviewError: null,
  auditError: null,
  uncertain: false,
  updatedAt: null,
  auditFilter: "",
  auditOffset: 0,
  auditHasMore: false,
};
export type DemoAction =
  | { type: "loading"; key: LoadKey; value: boolean }
  | {
      type: "overview";
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
  | {
      type: "error";
      message: string | null;
      uncertain?: boolean;
      invalidatePending?: boolean;
    }
  | { type: "auditStart"; filter: string; offset: number }
  | { type: "audit"; logs: AuditLog[]; hasMore: boolean }
  | { type: "auditError"; message: string };

export function hasExecutionReceipt(response: ActionResponse): boolean {
  return response.status === "completed" && response.execution_result.ok === true &&
    typeof response.execution_result.transfer_id === "string" && response.execution_result.transfer_id.trim().length > 0;
}

export function demoReducer(state: DemoState, action: DemoAction): DemoState {
  switch (action.type) {
    case "loading":
      return {
        ...state,
        loading: { ...state.loading, [action.key]: action.value },
      };
    case "overview":
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
        error: null,
      };
    case "error":
      return {
        ...state,
        error: action.message,
        uncertain: state.uncertain || !!action.uncertain,
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
