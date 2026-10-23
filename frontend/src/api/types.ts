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
  request_id?: string;
  audit_id?: string;
}
export interface Decision {
  operation: string;
  risk_level: RiskLevel;
  risk_reason: string;
  permission_action: "allow" | "pending_confirmation" | "require_verification";
  require_confirmation: boolean;
  require_verification: boolean;
}
interface PendingBase {
  action_id: string;
  account_id: string;
  risk_level: "yellow";
  created_at: string;
}
export interface TransferPendingAction extends PendingBase {
  action: "transfer_money" | "transfer";
  params: { to_account_id: string; amount: string };
  conversation_id?: string;
}
export interface CardPendingAction extends PendingBase {
  action: "temporarily_freeze_card" | "remove_temporary_freeze";
  params: { card_id: string; expected_version: number };
  conversation_id: string;
}
export type PendingAction = TransferPendingAction | CardPendingAction;
export interface Card {
  card_id: string;
  account_id: string;
  display_name: string;
  last_four: string;
  temporary_frozen: boolean;
  loss_status: "normal" | "reported";
  version: number;
  created_at: string;
  updated_at: string;
  source: "synthetic_card_demo_v1";
  effective_status: "active" | "temporarily_frozen" | "reported_lost";
  usable_in_demo: boolean;
}
export interface CardOperation {
  ok: boolean;
  operation_id: string;
  action_id: string;
  account_id: string;
  card_id: string;
  operation: "temporary_freeze" | "temporary_unfreeze";
  before: { temporary_frozen: boolean; loss_status: "normal" | "reported"; version: number };
  after: { temporary_frozen: boolean; loss_status: "normal" | "reported"; version: number };
  result_status: "completed";
  executed_at: string;
  source: "synthetic_card_demo_v1";
  request_id?: string;
  audit_id?: string;
}
export interface ToolData {
  cards?: Card[];
  card?: Card | null;
  card_operation?: CardOperation;
  linked?: boolean;
  reason_code?: string;
  record_id?: string;
  dataset_id?: string;
  payment_instrument_ref?: string | null;
  read_at?: string;
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
  analysis?: SpendingReport;
  comparison?: SpendingComparison;
  spending_records?: SpendingPage;
  spending_record?: SpendingRecordResult;
}
export interface CardResponse extends ToolData {
  ok: boolean;
  request_id: string;
  audit_id: string;
  decisions: Decision[];
  tool: string;
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
  investigation_id?: string | null;
  investigation?: InvestigationReport | null;
  execution_status?: InvestigationExecution | null;
  coverage_status?: InvestigationCoverage | null;
  planner_mode?: InvestigationPlanner | null;
}
export interface ActionResponse {
  status: "completed" | "cancelled" | "failed" | "execution_unknown" | "require_verification";
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
  decision?: Decision;
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

export interface SpendingDataset {
  dataset_id: string;
  account_id: string;
  version: string;
  source: "synthetic_demo";
  coverage_start: string;
  coverage_end_exclusive: string;
  coverage_note: string;
}
export interface SpendingRecord {
  record_id: string;
  account_id: string;
  occurred_at: string;
  business_date: string;
  amount: string;
  amount_cents: string;
  merchant_id: string;
  merchant_name: string;
  category: string;
  payment_instrument_ref: string | null;
  record_type: "purchase";
  status: "posted";
  currency: "CNY";
}
export interface SpendingGroup {
  category?: string;
  label?: string;
  merchant_id?: string;
  merchant_name?: string;
  count: number;
  amount: string;
  amount_cents: string;
  share_percent: string | null;
}
export interface SpendingReport {
  account_id: string;
  dataset: SpendingDataset;
  time_zone: "Asia/Shanghai";
  currency: "CNY";
  period: { start_date: string; end_date_exclusive: string; start_utc: string; end_utc_exclusive: string };
  coverage: { complete: boolean; code: string; note: string; covered_days: number; requested_days: number };
  summary: { count: number; amount: string; amount_cents: string; basis: string };
  categories: SpendingGroup[];
  merchants: SpendingGroup[];
  daily_trend: { date: string; covered: boolean; count: number | null; amount: string | null; share_percent: string | null }[];
  anomalies: { rule_id: string; reason: string; record_ids: string[] }[];
  anomaly_disclaimer: string;
}
export interface SpendingComparison {
  account_id: string;
  mode: "full_month" | "same_first_n_days";
  through_day: number | null;
  comparable: boolean;
  reason_code: string;
  previous: SpendingReport;
  current: SpendingReport;
  change_cents: string | null;
  change_amount: string | null;
  change_percent: string | null;
}
export interface SpendingPage {
  account_id: string;
  dataset_id: string;
  dataset: SpendingDataset;
  time_zone: string;
  period: { start_date: string; end_date_exclusive: string };
  total_count: number;
  limit: number;
  offset: number;
  records: SpendingRecord[];
}
export interface SpendingRecordResult {
  account_id: string;
  dataset_id: string;
  dataset: SpendingDataset;
  time_zone: string;
  record: SpendingRecord;
}
export interface AnalysisResponse extends ToolData {
  request_id: string;
  audit_id: string;
  tool: string;
  decisions: Decision[];
  data_status: "available" | "incomplete_coverage" | "no_records";
}

export type InvestigationExecution = "running" | "completed" | "partial" | "failed" | "interrupted";
export type InvestigationCoverage = "complete" | "incomplete" | "unknown";
export type InvestigationPlanner = "model_planned" | "deterministic_fallback" | "deterministic_no_choice";
export interface InvestigationSummary {
  investigation_id: string;
  period?: { start_date: string; end_date_exclusive: string; time_zone: string };
  focus?: "similar" | "large" | "all";
  dataset?: SpendingDataset | null;
  execution_status?: InvestigationExecution;
  coverage_status?: InvestigationCoverage;
  planner_mode?: InvestigationPlanner;
  started_at?: string;
  finished_at?: string | null;
  recovery?: { source: "stored_snapshot"; revision: number; updated_at: string; rerun: false };
}
export interface InvestigationStep {
  step_id: string;
  tool: string;
  evidence_ref?: string | null;
  depends_on?: string[];
  status: "planned" | "running" | "succeeded" | "failed" | "skipped" | "not_executed";
  started_at?: string | null;
  finished_at?: string | null;
  execution_index?: number | null;
  request_id?: string | null;
  audit_event_ids?: number[];
  decisions?: Decision[];
  code?: string | null;
}
export interface InvestigationReport extends InvestigationSummary {
  schema_version: "investigation.v1";
  account_id: string;
  status?: string;
  coverage?: SpendingReport["coverage"] | null;
  analysis_summary?: SpendingReport["summary"] | null;
  findings?: { evidence_ref: string; source_step_id: string; finding: { rule_id: string; record_ids: string[]; reason: string }; record_refs: string[]; status?: string; observed_interval_minutes?: string; observed_interval_milliseconds?: number }[];
  steps?: InvestigationStep[];
  evidence?: {
    records?: { evidence_ref: string; record_id: string; status: string; record?: SpendingRecord | null; read_at?: string; step_id?: string; link_ref?: string }[];
    links?: { evidence_ref: string; record_ref: string; status: string; linked: boolean | null; card_ref: string | null; reason_code?: string; read_at?: string; payment_instrument_ref?: string | null; step_id?: string }[];
    cards?: { evidence_ref: string; card_id: string; status: string; card?: Card | null; read_at?: string | null; step_id?: string; source_link_refs?: string[] }[];
  };
  planning?: { model_calls?: number; status?: string; fallback_reason?: string | null; events?: { index: number; status: string; code: string; step_id?: string; reason_code?: string; audit_event_id?: number | null }[] };
  audit?: { request_id?: string; event_ids?: number[]; status?: string };
  errors?: { step_id: string | null; code: string; message: string }[];
  limits?: string[];
  budget?: { tool_attempts: number; max_tool_calls: number; max_records: number; unfinished_records: number; record_limit_omitted: number };
  persistence_status?: string;
  disclaimer?: string;
  safety?: { note: string; business_writes_allowed: false; mode: string };
}
export interface InvestigationsResponse {
  account_id: string;
  items: InvestigationSummary[];
  total: number;
  limit: number;
  offset: number;
  request_id: string;
  audit_id: string;
  decision: Decision;
}
export interface InvestigationResponse {
  account_id: string;
  investigation: InvestigationReport;
  request_id: string;
  audit_id: string;
  decision: Decision;
}
