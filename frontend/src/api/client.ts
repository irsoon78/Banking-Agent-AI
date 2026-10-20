import type { ApiErrorBody } from "./types";

export class RequestError extends Error {
  constructor(
    message: string,
    public code: string,
    public httpStatus = 0,
    public uncertain = false,
    public auditId?: string,
  ) {
    super(message);
  }
}

const baseUrl = (import.meta.env.VITE_API_BASE_URL?.trim() || "/api").replace(
  /\/$/,
  "",
);

// POST 不设自动超时或重试；连接中断不能被解释为交易未执行。
export async function request<T>(path: string, body?: object): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${baseUrl}${path}`, {
      method: body ? "POST" : "GET",
      headers: body ? { "Content-Type": "application/json" } : undefined,
      body: body ? JSON.stringify(body) : undefined,
      cache: "no-store",
    });
  } catch {
    throw new RequestError(
      "无法取得后端响应，请检查服务连接。如涉及转账，请先核对账单和审计。",
      "NETWORK_ERROR",
      0,
      !!body,
    );
  }
  let data: T & Partial<ApiErrorBody>;
  try {
    data = await response.json();
  } catch {
    throw new RequestError(
      "后端未返回有效 JSON，请检查服务。如涉及转账，请先核对账单和审计。",
      "INVALID_RESPONSE",
      response.status,
      !!body,
    );
  }
  if (!response.ok || data.status === "error") {
    throw new RequestError(
      data.message || `请求失败（HTTP ${response.status}）`,
      data.error_code || "HTTP_ERROR",
      response.status,
      !!body &&
        response.status >= 500 &&
        !["LLM_NOT_CONFIGURED", "LLM_SERVICE_ERROR", "LLM_TIMEOUT"].includes(
          data.error_code || "",
        ),
      data.audit_id,
    );
  }
  return data;
}
