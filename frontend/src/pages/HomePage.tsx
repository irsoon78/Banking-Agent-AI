import { useEffect } from "react";
import { Link } from "react-router-dom";
import { useDemo } from "../state/DemoProvider";
import {
  AccountCard,
  Alert,
  Bills,
  Icon,
  PageTitle,
  dateTime,
} from "../components/ui";

export function HomePage() {
  const { state, refreshOverview } = useDemo();
  useEffect(() => {
    void refreshOverview();
  }, [refreshOverview]);
  return (
    <>
      <PageTitle eyebrow="ACCOUNT OVERVIEW" title="每一步操作，都有据可循。">
        用自然语言办理模拟银行业务，让风险判断、执行与审计清晰可见。
      </PageTitle>
      {state.overviewError && <Alert tone="red">{state.overviewError}</Alert>}
      <div className="home-top">
        <AccountCard />
        <section className="panel welcome-panel">
          <div className="eyebrow">YOUR BANKING COPILOT</div>
          <h2>
            把需求说出来，
            <br />
            把决定留给你。
          </h2>
          <p>
            查询自动执行，转账先经过风险与权限判断。
            <br />
            每一次操作，都能回到真实审计记录。
          </p>
          <Link className="button primary" to="/chat">
            开始 AI 对话 <Icon name="arrow" />
          </Link>
          <span className="subtle-note">本地模拟银行 · 所有结果来自后端</span>
        </section>
      </div>
      <div className="service-grid">
        <section className="panel service-card">
          <span
            className={`status-dot ${state.health?.status === "ok" ? "online" : ""}`}
          />
          <div>
            <span>后端服务</span>
            <strong>
              {state.health?.status === "ok"
                ? "运行正常"
                : state.loading.overview
                  ? "检查中"
                  : "未连接"}
            </strong>
          </div>
          <code>/health</code>
        </section>
        <section className="panel service-card">
          <Icon name="chat" />
          <div>
            <span>LLM 配置</span>
            <strong>
              {state.health
                ? state.health.llm_configured
                  ? "已配置"
                  : "未配置"
                : "未知"}
            </strong>
          </div>
          <span className="muted">以 /health 为准</span>
        </section>
        <section className="panel service-card">
          <Icon name="shield" />
          <div>
            <span>操作环境</span>
            <strong>模拟银行操作</strong>
          </div>
          <span className="badge tone-blue">V0.3.0</span>
        </section>
      </div>
      <section className="panel">
        <div className="section-heading">
          <div>
            <div className="eyebrow">RECENT ACTIVITY</div>
            <h2>最近账单</h2>
          </div>
          <span className="muted">
            {state.updatedAt
              ? `最近读取 ${dateTime(state.updatedAt)}`
              : "等待后端数据"}
          </span>
        </div>
        <Bills items={state.bills} />
      </section>
      <section className="flow-strip" aria-label="业务流程说明">
        <span className="eyebrow">安全操作流程</span>
        {[
          "自然语言请求",
          "Agent 工具选择",
          "风险与权限判断",
          "必要时用户确认",
          "执行与结果",
          "审计追踪",
        ].map((step, index) => (
          <span key={step}>
            <small>{String(index + 1).padStart(2, "0")}</small>
            {step}
          </span>
        ))}
      </section>
    </>
  );
}
