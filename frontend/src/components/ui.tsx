import type { ReactNode } from "react";
import { Link } from "react-router-dom";
import { hasExecutionReceipt } from "../state/demoReducer";
import type {
  ActionResponse,
  Bill,
  SecureResponse,
  ToolData,
} from "../api/types";
import { useDemo } from "../state/DemoProvider";
import { AnalysisView } from "./AnalysisView";
import { CardDataView, CardOperationView } from "./CardView";
import { hasCardReceipt } from "../state/cardFlow";
import { InvestigationView } from "./InvestigationView";

export function Icon({
  name,
  size = 20,
}: {
  name: "bank" | "arrow" | "shield" | "chat" | "audit" | "refresh" | "check";
  size?: number;
}) {
  const paths = {
    bank: "M3 9l9-6 9 6M4 10h16M6 10v9m6-9v9m6-9v9M3 21h18",
    arrow: "M4 12h16m-6-6 6 6-6 6",
    shield: "M12 3l8 3v6c0 5-8 9-8 9s-8-4-8-9V6l8-3zm-4 9 3 3 5-6",
    chat: "M21 11a8 8 0 0 1-8 8H8l-5 3V11a9 9 0 0 1 18 0zM7 10h10M7 14h6",
    audit: "M7 3h10l3 3v15H4V3h3zm1 6h8m-8 4h8m-8 4h5",
    refresh: "M20 7v5h-5M4 17v-5h5m10-4a8 8 0 0 0-14-2m0 10a8 8 0 0 0 14 2",
    check: "m5 12 4 4L19 6",
  };
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.7"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d={paths[name]} />
    </svg>
  );
}
export const money = (value?: string | null) => {
  if (value == null || !/^\d+(\.\d+)?$/.test(value)) return "—";
  const [whole, fraction = ""] = value.split(".");
  return `${whole.replace(/\B(?=(\d{3})+(?!\d))/g, ",")}.${fraction.padEnd(2, "0")}`;
};
export const dateTime = (value?: string | null) =>
  value ? new Date(value).toLocaleString("zh-CN", { hour12: false }) : "—";
export function Empty({ children }: { children: ReactNode }) {
  return <div className="empty">{children}</div>;
}
export function Alert({
  children,
  tone = "yellow",
}: {
  children: ReactNode;
  tone?: string;
}) {
  return (
    <div className={`alert tone-${tone}`} role="alert">
      <Icon name="shield" />
      <div>{children}</div>
    </div>
  );
}
export function PageTitle({
  eyebrow,
  title,
  children,
}: {
  eyebrow: string;
  title: string;
  children: ReactNode;
}) {
  return (
    <div className="page-title">
      <div>
        <div className="eyebrow">{eyebrow}</div>
        <h1>{title}</h1>
        <p>{children}</p>
      </div>
    </div>
  );
}
export function AuditLink({
  id,
  children = "查看审计",
}: {
  id?: string | null;
  children?: ReactNode;
}) {
  return (
    <Link
      className="button secondary"
      to={id ? `/audit?request_id=${encodeURIComponent(id)}` : "/audit"}
    >
      <Icon name="audit" />
      {children}
    </Link>
  );
}
export function AccountCard({ compact = false }: { compact?: boolean }) {
  const { state, refreshOverview } = useDemo();
  return (
    <section
      className={`account-card ${compact ? "compact" : ""}`}
      aria-label="账户信息"
    >
      <div className="row">
        <span className="eyebrow">DEMO ACCOUNT</span>
        <Icon name="bank" size={26} />
      </div>
      <div className="account-owner">
        {state.account?.owner || "待加载"}
        <span>账户 {state.account_id}</span>
      </div>
      <div className="balance-label">当前可用余额</div>
      <div className="balance-amount" data-testid="balance">
        {money(state.balance)}
        <small>{state.account?.currency || "—"}</small>
      </div>
      <div className="account-footer">
        <span>
          {state.loading.overview
            ? "正在读取后端…"
            : state.overviewError
              ? "数据待核对 / 可能为上次结果"
              : "来自银行演示账户"}
        </span>
        <button
          className="icon-button"
          aria-label="刷新账户信息"
          disabled={
            state.loading.overview || state.loading.chat || state.loading.action
          }
          onClick={() => void refreshOverview()}
        >
          <Icon name="refresh" />
        </button>
      </div>
    </section>
  );
}
export function Bills({ items }: { items: Bill[] }) {
  if (!items.length) return <Empty>暂无账单数据</Empty>;
  return (
    <div className="table-scroll">
      <table>
        <thead>
          <tr>
            <th>交易类型 / 时间</th>
            <th>对方账户</th>
            <th>金额（CNY）</th>
            <th>交易编号</th>
          </tr>
        </thead>
        <tbody>
          {items.map((item) => (
            <tr key={item.id}>
              <td>
                <strong>
                  {
                    {
                      opening: "演示初始入账",
                      transfer_in: "转入",
                      transfer_out: "转出",
                    }[item.kind]
                  }
                </strong>
                <small>{dateTime(item.created_at)}</small>
              </td>
              <td>{item.counterparty_id || "—"}</td>
              <td
                className={`numeric ${item.direction === "in" ? "text-green" : ""}`}
              >
                {item.direction === "out" ? "−" : "+"}
                {money(item.amount)}
              </td>
              <td>
                <code
                  className="short-code"
                  title={item.transfer_id || undefined}
                >
                  {item.transfer_id || "—"}
                </code>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
export const statusLabels: Record<string, string> = {
  completed: "已完成",
  pending: "等待用户确认",
  require_verification: "强验证阻断",
  failed: "业务失败",
  needs_input: "需要补充信息",
  partial: "部分完成",
  execution_unknown: "结果待核对",
  cancelled: "已取消 · 未执行",
};
export function ToolResult({
  tool,
  result,
  auditId,
}: {
  tool: string;
  result: ToolData;
  auditId?: string;
}) {
  return (
    <div className="tool-result">
      <div className="row">
        <code>{tool}</code>
        <span className={`badge ${result.ok ? "tone-green" : "tone-red"}`}>
          {result.ok ? "工具已返回" : "工具失败"}
        </span>
      </div>
      {result.balance != null && (
        <p>
          账户余额{" "}
          <strong>
            {money(result.balance)} {result.currency}
          </strong>
        </p>
      )}
      {result.bills && (
        <p>
          返回 <strong>{result.bills.length}</strong> 条实际账单
        </p>
      )}
      {result.error && <p className="text-red">{result.error}</p>}
      <AnalysisView result={result} auditId={auditId} />
      <CardDataView result={result} />
      <details>
        <summary>查看工具原始结果</summary>
        <pre>{JSON.stringify(result, null, 2)}</pre>
      </details>
    </div>
  );
}
export function Receipt({ response }: { response: ActionResponse }) {
  const result = response.execution_result;
  if (response.status === "require_verification")
    return (
      <Alert tone="red">
        <strong>需要强验证，当前操作未执行。</strong>
        <p><code>require_verification · verification_required · not_executed</code></p>
        <p>{response.message}</p>
      </Alert>
    );
  if (response.status === "cancelled")
    return (
      <Alert tone="neutral">
        <strong>操作已取消，未执行。</strong>
        <p>没有生成交易凭据。</p>
      </Alert>
    );
  if (response.status === "execution_unknown")
    return (
      <Alert tone="red">
        <strong>执行结果未知，禁止自动重试。</strong>
        <p>请按原action_id查询卡片操作凭据，或核对转账账单与审计记录；未查到结果也不能自动重试。</p>
      </Alert>
    );
  if (response.status === "failed")
    return (
      <Alert tone="red">
        <strong>业务执行失败</strong>
        <p>{result.error || response.message}</p>
        <code>{response.error_code || result.code}</code>
        <p>请重新查询实际状态和审计，核对原因后再明确发起新请求；页面不会自动重试。</p>
      </Alert>
    );
  if (result.card_operation || response.action === "temporarily_freeze_card" || response.action === "remove_temporary_freeze")
    return hasCardReceipt(response) && result.card_operation ? <CardOperationView operation={result.card_operation} /> : <Alert tone="red">未取得匹配的卡片操作凭据，请核对服务端。</Alert>;
  if (!hasExecutionReceipt(response))
    return <Alert tone="red">未取得有效的执行凭据，请核对账单与审计。</Alert>;
  return (
    <section className="receipt">
      <div className="row">
        <h3>
          <Icon name="check" /> 执行凭据
        </h3>
        <span className="badge tone-green">
          {result.simulated ? "模拟转账成功" : "已完成"}
        </span>
      </div>
      <dl>
        <div>
          <dt>交易金额</dt>
          <dd>
            {money(result.amount)} {result.currency}
          </dd>
        </div>
        <div>
          <dt>转出 → 收款账户</dt>
          <dd>
            {result.from_account_id || "—"} → {result.to_account_id || "—"}
          </dd>
        </div>
        <div>
          <dt>转出后余额</dt>
          <dd>
            {money(result.balance)} {result.currency}
          </dd>
        </div>
        <div>
          <dt>transfer_id</dt>
          <dd>
            <code data-testid="transfer-id">{result.transfer_id || "—"}</code>
          </dd>
        </div>
      </dl>
      <details>
        <summary>查看 execution_result</summary>
        <pre>{JSON.stringify(result, null, 2)}</pre>
      </details>
    </section>
  );
}
export function ResponseContent({
  response,
  account,
}: {
  response: SecureResponse | ActionResponse;
  account?: string;
}) {
  if ("investigation" in response && response.investigation) {
    return <><InvestigationView report={response.investigation} account={account ?? response.investigation.account_id} />
      {response.audit_warning && <Alert>{response.audit_warning}</Alert>}
      <div className="message-links"><AuditLink id={response.audit_id} /></div></>;
  }
  if (
    "response_kind" in response &&
    response.response_kind !== "business_result"
  ) {
    const labels = {
      conversation: "对话回复",
      capabilities: "能力说明",
      clarification: "需要补充信息",
      unsupported: "暂不支持",
    };
    return (
      <>
        <span className="badge tone-blue">
          {labels[response.response_kind]}
        </span>
        <p className="response-text">{response.message}</p>
        {response.tool_results.map((item, index) => (
          <ToolResult key={index} {...item} auditId={response.audit_id} />
        ))}
        {response.audit_warning && <Alert>{response.audit_warning}</Alert>}
        <div className="message-links">
          <AuditLink id={response.audit_id} />
        </div>
      </>
    );
  }
  return (
    <>
      <span
        className={`badge tone-${response.status === "require_verification" || response.status === "failed" || response.status === "execution_unknown" ? "red" : response.status === "completed" ? "green" : "yellow"}`}
      >
        {statusLabels[response.status] || response.status}
      </span>
      <p className="response-text">{response.message}</p>
      {response.status === "require_verification" && <Alert tone="red">强验证阻断 · 当前Demo不能放行。<br /><code>red · require_verification · verification_required · not_executed</code></Alert>}
      {response.status === "partial" && (
        <Alert>部分完成：以下仅展示已获得的实际工具结果。</Alert>
      )}
      {response.status === "needs_input" && (
        <p className="muted">请重新发送包含必要账户编号、金额的完整指令。</p>
      )}
      {response.status === "execution_unknown" && (
        <Alert tone="red">
          禁止自动重试。请先核对账单和审计，不要再次执行。
        </Alert>
      )}
      {"tool_results" in response &&
        response.tool_results.map((item, index) => (
          <ToolResult key={index} {...item} auditId={response.audit_id} />
        ))}
      {"execution_result" in response && <Receipt response={response} />}
      {response.audit_warning && <Alert>{response.audit_warning}</Alert>}
      {response.error_code && (
        <code className="error-code">{response.error_code}</code>
      )}
      <div className="message-links">
        {response.status === "pending" && (
          <Link className="text-link" to="/risk">
            进入风险确认 →
          </Link>
        )}
        <AuditLink id={response.audit_id} />
      </div>
    </>
  );
}
