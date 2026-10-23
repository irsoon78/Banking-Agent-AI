import { request } from "./client";
import type {
  Account,
  ActionResponse,
  AuditResponse,
  BillsResponse,
  Health,
  SecureResponse,
  AnalysisResponse,
  CardResponse,
  InvestigationsResponse,
  InvestigationResponse,
} from "./types";

export const banking = {
  investigations: (account: string, offset = 0) => request<InvestigationsResponse>(`/accounts/${encodeURIComponent(account)}/investigations?limit=20&offset=${offset}`),
  investigation: (account: string, id: string) => request<InvestigationResponse>(`/accounts/${encodeURIComponent(account)}/investigations/${encodeURIComponent(id)}`),
  health: () => request<Health>("/health"),
  cards: (account: string) => request<CardResponse>(`/accounts/${encodeURIComponent(account)}/cards`),
  card: (account: string, card: string) => request<CardResponse>(`/accounts/${encodeURIComponent(account)}/cards/${encodeURIComponent(card)}`),
  spendingCard: (account: string, record: string) => request<CardResponse>(`/accounts/${encodeURIComponent(account)}/analysis-records/${encodeURIComponent(record)}/card`),
  cardOperation: (account: string, action: string) => request<CardResponse>(`/accounts/${encodeURIComponent(account)}/card-operations/${encodeURIComponent(action)}`),
  balance: (account: string) =>
    request<Account>(`/accounts/${encodeURIComponent(account)}/balance`),
  bills: (account: string) =>
    request<BillsResponse>(
      `/accounts/${encodeURIComponent(account)}/bills?limit=10`,
    ),
  analysis: (account: string, params: URLSearchParams) =>
    request<AnalysisResponse>(`/accounts/${encodeURIComponent(account)}/bill-analysis?${params}`),
  analysisRecords: (account: string, params: URLSearchParams) =>
    request<AnalysisResponse>(`/accounts/${encodeURIComponent(account)}/analysis-records?${params}`),
  chat: (
    account_id: string,
    message: string,
    conversation_id?: string | null,
  ) =>
    request<SecureResponse>("/chat/secure", {
      account_id,
      message,
      ...(conversation_id ? { conversation_id } : {}),
    }),
  confirm: (account_id: string, action_id: string, conversation_id?: string) =>
    request<ActionResponse>("/confirm", {
      account_id,
      action_id,
      confirmed: true,
      ...(conversation_id ? { conversation_id } : {}),
    }),
  cancel: (account_id: string, action_id: string, conversation_id?: string) =>
    request<ActionResponse>("/cancel", { account_id, action_id, ...(conversation_id ? { conversation_id } : {}) }),
  audit: (account: string, auditId?: string, offset = 0) => {
    const params = new URLSearchParams({
      limit: "100",
      offset: String(offset),
    });
    if (auditId) params.set("request_id", auditId);
    return request<AuditResponse>(
      `/audit/${encodeURIComponent(account)}?${params}`,
    );
  },
};
