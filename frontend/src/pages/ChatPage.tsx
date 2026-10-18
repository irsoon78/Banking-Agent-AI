import { useEffect, useRef, useState } from "react";
import type { FormEvent } from "react";
import { Link } from "react-router-dom";
import { useDemo } from "../state/DemoProvider";
import {
  AccountCard,
  Alert,
  AuditLink,
  Icon,
  PageTitle,
  ResponseContent,
} from "../components/ui";
import { RiskPanel } from "../components/RiskPanel";
import { AuditTimeline } from "../components/AuditTimeline";

const examples = ["查询我的余额", "给账户1002转500元", "给账户1002转5000元"];
export function ChatPage() {
  const { state, sendMessage } = useDemo();
  const [draft, setDraft] = useState("");
  const bottom = useRef<HTMLDivElement>(null);
  const blocked =
    state.loading.chat ||
    state.loading.action ||
    !!state.pendingAction ||
    state.uncertain;
  useEffect(() => {
    bottom.current?.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }, [state.messages.length, state.loading.chat]);
  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (blocked || !draft.trim()) return;
    const text = draft;
    setDraft("");
    void sendMessage(text);
  };
  return (
    <>
      <PageTitle eyebrow="AI BANKING WORKSPACE" title="你的需求，安全地执行。">
        从一句话到一条可追踪的操作记录。
      </PageTitle>
      <div className="chat-layout">
        <aside className="left-rail">
          <AccountCard compact />
          <section className="panel guide-card">
            <h3>演示场景</h3>
            <p>点击填入指令，再发送给 Agent。</p>
            {examples.map((text, index) => (
              <button
                className="example"
                key={text}
                disabled={blocked}
                onClick={() => setDraft(text)}
              >
                <span>0{index + 1}</span>
                {text}
                <Icon name="arrow" size={16} />
              </button>
            ))}
          </section>
          <div className="rail-note">
            <Icon name="shield" />
            <p>风险与权限由后端规则决定。这里只展示已返回的真实结果。</p>
          </div>
        </aside>
        <section className="panel chat-panel">
          <div className="chat-panel-header">
            <div className="agent-avatar">
              <Icon name="chat" size={25} />
            </div>
            <div>
              <h2>Banking Agent</h2>
              <span>
                {state.loading.chat ? "正在等待后端响应…" : "自然语言银行助手"}
              </span>
            </div>
            <span className="badge tone-blue">安全入口</span>
          </div>
          <div
            className="messages"
            aria-live="polite"
            aria-busy={state.loading.chat}
          >
            {!state.messages.length && (
              <div className="chat-empty">
                <div className="intro-icon">
                  <Icon name="chat" size={34} />
                </div>
                <h2>今天有什么可以帮你？</h2>
                <p>
                  查余额、看账单，或发起一笔模拟转账。
                  <br />
                  工具、风险和执行结果将在这里展示。
                </p>
                <div className="suggestions">
                  {examples.map((text) => (
                    <button
                      disabled={blocked}
                      key={text}
                      onClick={() => setDraft(text)}
                    >
                      {text} <Icon name="arrow" size={16} />
                    </button>
                  ))}
                </div>
              </div>
            )}
            {state.messages.map((entry) => (
              <article className={`message ${entry.role}`} key={entry.id}>
                <div className="message-author">
                  {entry.role === "user"
                    ? "你 · 账户 " + state.account_id
                    : "Banking Agent"}
                </div>
                <div className="message-body">
                  {entry.response ? (
                    <ResponseContent response={entry.response} />
                  ) : (
                    <p
                      className={
                        entry.transportError
                          ? "text-red response-text"
                          : "response-text"
                      }
                    >
                      {entry.text}
                    </p>
                  )}
                </div>
              </article>
            ))}
            {state.loading.chat && (
              <div className="waiting">
                <span className="spinner" /> 后端处理中，请勿重复发送
              </div>
            )}
            <div ref={bottom} />
          </div>
          {state.pendingAction && (
            <div className="composer-notice">
              有一笔待确认任务，请先 <Link to="/risk">确认或取消</Link>
              ，再发送新指令。
            </div>
          )}
          {state.uncertain && (
            <Alert tone="red">
              执行结果待核对，已暂停发送。请查看首页账单和审计。
            </Alert>
          )}
          <form className="composer" onSubmit={submit}>
            <label className="sr-only" htmlFor="message">
              银行操作指令
            </label>
            <textarea
              id="message"
              maxLength={2000}
              rows={3}
              value={draft}
              disabled={blocked}
              onChange={(event) => setDraft(event.target.value)}
              placeholder="例如：查询我的余额"
              onKeyDown={(event) => {
                if (
                  event.key === "Enter" &&
                  !event.shiftKey &&
                  !event.nativeEvent.isComposing
                ) {
                  event.preventDefault();
                  if (!blocked && draft.trim())
                    event.currentTarget.form?.requestSubmit();
                }
              }}
            />
            <div className="row">
              <small>
                Enter 发送 · Shift + Enter 换行 · {draft.length}/2000
              </small>
              <button
                className="button primary"
                type="submit"
                disabled={blocked || !draft.trim()}
              >
                {state.loading.chat ? "处理中…" : "发送指令"}
                <Icon name="arrow" size={18} />
              </button>
            </div>
          </form>
        </section>
        <aside className="right-rail">
          <RiskPanel />
          <section className="panel">
            <div className="row">
              <h2>
                <Icon name="audit" /> 审计追踪
              </h2>
              <span className="live-label">真实事件</span>
            </div>
            {state.loading.audit ? (
              <p>读取中…</p>
            ) : (
              <AuditTimeline
                logs={state.auditLogs.filter(
                  (log) => log.request_id === state.audit_id,
                )}
                compact
              />
            )}
            {state.auditError && <p className="text-red">{state.auditError}</p>}
            <AuditLink id={state.audit_id}>打开完整审计</AuditLink>
          </section>
        </aside>
      </div>
    </>
  );
}
