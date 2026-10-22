import { NavLink, Navigate, Route, Routes, Link } from "react-router-dom";
import { HomePage } from "./pages/HomePage";
import { ChatPage } from "./pages/ChatPage";
import { RiskPage } from "./pages/RiskPage";
import { AuditPage } from "./pages/AuditPage";
import { AnalysisPage } from "./pages/AnalysisPage";
import { CardsPage } from "./pages/CardsPage";
import { Alert, Icon } from "./components/ui";
import { useDemo } from "./state/DemoProvider";

export function App() {
  const { state, switchAccount } = useDemo();
  return (
    <>
      <header className="topbar">
        <Link to="/home" className="brand">
          <span className="brand-mark">
            <Icon name="bank" size={26} />
          </span>
          <span>
            <strong>Banking Agent AI</strong>
            <small>比赛演示台</small>
          </span>
        </Link>
        <nav aria-label="主导航">
          <NavLink to="/home">首页</NavLink>
          <NavLink to="/chat">AI 对话</NavLink>
          <NavLink to="/analysis">账单分析</NavLink>
          <NavLink to="/cards">银行卡管理</NavLink>
          <NavLink to="/risk">
            风险确认{state.pendingAction && <span className="nav-dot" />}
          </NavLink>
          <NavLink to="/audit">审计日志</NavLink>
        </nav>
        <div className="header-meta">
          <span className="version">V0.3.0</span>
          <span className="simulation">模拟银行操作</span>
          <label className="account-chip">模拟账户 <select aria-label="切换模拟账户" value={state.account_id} disabled={!!state.pendingAction || state.uncertain || state.loading.chat || state.loading.action} onChange={e => switchAccount(e.target.value)}><option value="1001">1001</option><option value="1002">1002</option></select></label>
        </div>
      </header>
      <div className="workspace-label">
        <span>
          <span className="status-dot blue" />
          SECURE AGENT WORKSPACE
        </span>
        <span>自然语言驱动 · 后端风险决策 · 全程审计</span>
      </div>
      <main>
        {state.error && <Alert tone="red">{state.error}</Alert>}
        {state.overviewError && state.currentDecision && (
          <Alert>
            {state.overviewError} 账户卡片可能显示上次读取值，请核对后端。
          </Alert>
        )}
        <Routes key={state.account_id}>
          <Route path="/home" element={<HomePage />} />
          <Route path="/chat" element={<ChatPage />} />
          <Route path="/analysis" element={<AnalysisPage />} />
          <Route path="/cards" element={<CardsPage />} />
          <Route path="/risk" element={<RiskPage />} />
          <Route path="/audit" element={<AuditPage />} />
          <Route path="*" element={<Navigate to="/home" replace />} />
        </Routes>
      </main>
      <footer>
        <span>
          <Icon name="shield" size={16} /> Banking Agent AI · 比赛演示环境
        </span>
        <span>模拟账户与交易 · 非真实银行服务</span>
      </footer>
    </>
  );
}
