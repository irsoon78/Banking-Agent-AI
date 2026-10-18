import { useEffect, useRef, useState } from "react";
import type { FormEvent } from "react";
import { useSearchParams } from "react-router-dom";
import { useDemo } from "../state/DemoProvider";
import { Alert, Icon, PageTitle } from "../components/ui";
import { AuditTimeline } from "../components/AuditTimeline";

export function AuditPage() {
  const { state, loadAudit } = useDemo();
  const [params, setParams] = useSearchParams();
  const filter = params.get("request_id") || "";
  const [draft, setDraft] = useState(filter);
  const loadedFilter = useRef<string | null>(null);
  useEffect(() => {
    setDraft(filter);
  }, [filter]);
  useEffect(() => {
    // 后台聊天返回时可能更新共享审计；本页面始终保留用户选择的筛选。
    if (loadedFilter.current !== filter || state.auditFilter !== filter) {
      loadedFilter.current = filter;
      void loadAudit(filter);
    }
  }, [filter, state.auditFilter, loadAudit]);
  const submit = (event: FormEvent) => {
    event.preventDefault();
    const value = draft.trim();
    if (value === filter) void loadAudit(filter);
    else setParams(value ? { request_id: value } : {});
  };
  return (
    <>
      <PageTitle eyebrow="AUDIT TRAIL" title="每一个决策，都能追溯。">
        按事件 ID 正序展示后端真实记录。时间以本机时区显示，不补写任何事件。
      </PageTitle>
      <section className="panel">
        <form className="audit-filter" onSubmit={submit}>
          <label htmlFor="audit-id">审计链编号 / request_id</label>
          <div className="filter-controls">
            <input
              id="audit-id"
              value={draft}
              maxLength={64}
              onChange={(event) => setDraft(event.target.value)}
              placeholder="留空查看账户全部历史"
            />
            <button className="button primary" disabled={state.loading.audit}>
              查询审计
            </button>
            <button
              type="button"
              className="button secondary"
              disabled={state.loading.audit}
              onClick={() => {
                setDraft("");
                if (filter) setParams({});
                else void loadAudit();
              }}
            >
              全部历史
            </button>
            {state.audit_id && (
              <button
                type="button"
                className="button secondary"
                disabled={state.loading.audit}
                onClick={() => {
                  setDraft(state.audit_id!);
                  if (filter === state.audit_id) void loadAudit(filter);
                  else setParams({ request_id: state.audit_id! });
                }}
              >
                当前操作
              </button>
            )}
          </div>
        </form>
        <div className="audit-summary">
          <span className="badge tone-blue">账户 {state.account_id}</span>
          <span>
            {state.loading.audit
              ? "读取中…"
              : `本页 ${state.auditLogs.length} 条事件`}
          </span>
          <span>后端分页 offset {state.auditOffset}</span>
        </div>
        {filter && (
          <p className="chain-id">
            当前审计链 <code>{filter}</code>
          </p>
        )}
        {state.auditError && <Alert tone="red">{state.auditError}</Alert>}
        <div className="audit-body" aria-busy={state.loading.audit}>
          {state.loading.audit ? (
            <div className="waiting">
              <span className="spinner" /> 正在读取审计记录
            </div>
          ) : (
            <AuditTimeline logs={state.auditLogs} />
          )}
        </div>
        <div className="pagination">
          <button
            className="button secondary"
            disabled={state.loading.audit || state.auditOffset === 0}
            onClick={() =>
              void loadAudit(filter, Math.max(0, state.auditOffset - 100))
            }
          >
            更新的记录
          </button>
          <span className="muted">每页最多 100 条，页内正序</span>
          <button
            className="button secondary"
            disabled={state.loading.audit || !state.auditHasMore}
            onClick={() => void loadAudit(filter, state.auditOffset + 100)}
          >
            更早的记录 <Icon name="arrow" size={16} />
          </button>
        </div>
      </section>
    </>
  );
}
