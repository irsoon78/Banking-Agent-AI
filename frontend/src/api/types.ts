export type RiskLevel = "green" | "yellow" | "red";
export type SecureStatus =
  | "completed"
  | "pending"
  | "require_verification"
  | "failed"
  | "needs_input"
  | "partial"
  | "execution_unknown";
export interface Health {
  status: string;
  llm_configured: boolean;
}
export interface Account {
  ok: boolean;
  account_id: string;
  owner: string;
  balance: string;
  balance_cents: number;
  currency: string;
  audit_warning?: string;
}
export interface Bill {
  id: number;
  account_id: string;
  kind: "opening" | "transfer_in" | "transfer_out";
  amount: string;
  amount_cents: number;
  direction: "in" | "out";
  counterparty_id: string | null;
  transfer_id: string | null;
  created_at: string;
}
export interface BillsResponse {
  ok: boolean;
  account_id: string;
  currency: string;
  bills: Bill[];
  audit_warning?: string;
}
export interface ApiErrorBody {
  status: "error";
  error_code: string;
  message: string;
  detail?: unknown;
}
export interface Decision {
  operation: string;
  risk_level: RiskLevel;
  risk_reason: string;
  permission_action: "allow" | "pending_confirmation" | "require_verification";
  require_confirmation: boolean;
  require_verification: boolean;
}
export interface PendingAction {
  action_id: string;
  account_id: string;
  action: string;
  params: { to_account_id: string; amount: string };
  risk_level: "yellow";
  created_at: string;
}
export interface ToolData {
  ok?: boolean;
  simulated?: boolean;
  account_id?: string;
  owner?: string;
  balance?: string;
  balance_cents?: number;
  currency?: string;
  bills?: Bill[];
  transfer_id?: string;
  from_account_id?: string;
  to_account_id?: string;
  amount?: string;
  amount_cents?: number;
  code?: string;
  error?: string;
  audit_warning?: string;
}
export interface SecureResponse extends Partial<Decision> {
  response_kind:
    | "conversation"
    | "capabilities"
    | "clarification"
    | "unsupported"
    | "business_result";
  conversation_id: string;
  status: SecureStatus;
  message: string;
  reply: string;
  tool_results: { tool: string; result: ToolData }[];
  request_id: string;
  audit_id: string;
  decisions: Decision[];
  action_id?: string;
  pending_action?: PendingAction;
  audit_warning?: string;
  error_code?: string;
  error?: ApiErrorBody;
}
export interface ActionResponse {
  status: "completed" | "cancelled" | "failed" | "execution_unknown";
  message: string;
  execution_result: ToolData;
  audit_id: string;
  request_id: string;
  action_id?: string;
  action?: string;
  result?: ToolData;
  reply?: string;
  audit_warning?: string;
  error_code?: string;
  error?: ApiErrorBody;
}
export interface AuditLog {
  id: number;
  timestamp: string;
  request_id: string;
  action_id: string | null;
  event_type: string;
  account_id: string;
  user_request: string;
  operation: string | null;
  risk_level: RiskLevel | null;
  permission_action: string | null;
  confirmation_status: string;
  execution_result: string;
  details: Record<string, unknown>;
}
export interface AuditResponse {
  account_id: string;
  logs: AuditLog[];
  limit: number;
  offset: number;
}
