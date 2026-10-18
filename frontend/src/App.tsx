import { NavLink, Navigate, Route, Routes, Link } from "react-router-dom";
import { HomePage } from "./pages/HomePage";
import { ChatPage } from "./pages/ChatPage";
import { RiskPage } from "./pages/RiskPage";
import { AuditPage } from "./pages/AuditPage";
import { Alert, Icon } from "./components/ui";
import { useDemo } from "./state/DemoProvider";

export function App() {
  const { state } = useDemo();
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
          <NavLink to="/risk">
            风险确认{state.pendingAction && <span className="nav-dot" />}
          </NavLink>
          <NavLink to="/audit">审计日志</NavLink>
        </nav>
        <div className="header-meta">
          <span className="version">V0.3.0</span>
          <span className="simulation">模拟银行操作</span>
          <span className="account-chip">账户 {state.account_id}</span>
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
        <Routes>
          <Route path="/home" element={<HomePage />} />
          <Route path="/chat" element={<ChatPage />} />
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
