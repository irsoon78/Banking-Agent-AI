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
      return {
        ...state,
        currentDecision: action.response,
        pendingAction: action.response.pending_action ?? state.pendingAction,
        audit_id: action.response.audit_id,
        executionResult: null,
        actionResponse: null,
        uncertain:
          state.uncertain || action.response.status === "execution_unknown",
        error: null,
      };
    case "action":
      return {
        ...state,
        actionResponse: action.response,
        pendingAction: null,
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
