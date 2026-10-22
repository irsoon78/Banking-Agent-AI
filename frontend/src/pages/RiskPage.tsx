import { Link } from "react-router-dom";
import { RiskPanel } from "../components/RiskPanel";
import { AccountCard, Bills, PageTitle } from "../components/ui";
import { useDemo } from "../state/DemoProvider";

export function RiskPage() {
  const { state } = useDemo();
  return (
    <>
      <PageTitle eyebrow="RISK & AUTHORIZATION" title="执行之前，由你决定。">
        核对服务端保存的参数。确认只执行原任务，取消不会修改余额或卡片状态。
      </PageTitle>
      <div className="risk-layout">
        <div>
          <RiskPanel actionable />
          {!state.currentDecision && (
            <Link className="button primary" to="/chat">
              前往 AI 对话
            </Link>
          )}
        </div>
        <aside>
          <AccountCard />
          <section className="panel checklist">
            <h3>确认须知</h3>
            <p>01　操作及目标均使用服务端只读快照。</p>
            <p>02　转账复核日累计与余额；卡片复核归属、状态和版本。</p>
            <p>03　操作期间请勿刷新或重复发起。</p>
            <p>04　结果未知时，查询卡片操作凭据或转账账单及审计，不要重试。</p>
          </section>
        </aside>
      </div>
      <section className="panel">
        <div className="section-heading">
          <h2>当前账户最近账单</h2>
          <span className="muted">操作返回后刷新后端实际数据</span>
        </div>
        <Bills items={state.bills} />
      </section>
    </>
  );
}
