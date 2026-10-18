import type { AuditLog } from "../api/types";
import { Empty, dateTime } from "./ui";

const eventLabels: Record<string, string> = {
  request_received: "收到请求",
  risk_evaluated: "风险判断",
  permission_decided: "权限决策",
  pending_created: "创建待确认任务",
  confirmation_confirmed: "用户已确认",
  confirmation_cancelled: "用户已取消",
  confirmation_rejected: "确认请求被拒绝",
  tool_execution_started: "开始执行工具",
  tool_executed: "工具执行结果",
  request_returned: "请求已返回",
  request_failed: "请求失败",
  tool_validation_failed: "工具参数校验失败",
};
export function AuditTimeline({
  logs,
  compact = false,
}: {
  logs: AuditLog[];
  compact?: boolean;
}) {
  if (!logs.length) return <Empty>暂无审计事件</Empty>;
  return (
    <ol className={`timeline ${compact ? "compact" : ""}`}>
      {[...logs]
        .sort((a, b) => a.id - b.id)
        .map((log) => (
          <li key={log.id} data-event-id={log.id}>
            <span
              className={`timeline-dot dot-${log.risk_level || "neutral"}`}
            />
            <div className="row">
              <strong>{eventLabels[log.event_type] || log.event_type}</strong>
              <small>#{log.id}</small>
            </div>
            <code className="event-type">{log.event_type}</code>
            <time>{dateTime(log.timestamp)}</time>
            {!compact && (
              <>
                <div className="event-meta">
                  <span>
                    风险 <b>{log.risk_level || "—"}</b>
                  </span>
                  <span>
                    权限 <b>{log.permission_action || "—"}</b>
                  </span>
                  <span>
                    确认 <b>{log.confirmation_status}</b>
                  </span>
                  <span>
                    执行 <b>{log.execution_result}</b>
                  </span>
                </div>
                <details>
                  <summary>事件详情</summary>
                  <pre>{JSON.stringify(log, null, 2)}</pre>
                </details>
              </>
            )}
          </li>
        ))}
    </ol>
  );
}
