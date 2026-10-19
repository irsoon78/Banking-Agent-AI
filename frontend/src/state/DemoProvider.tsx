import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useReducer,
  useRef,
} from "react";
import type { ReactNode } from "react";
import { banking } from "../api/banking";
import { RequestError } from "../api/client";
import { demoReducer, initialState } from "./demoReducer";
import type { DemoState, Message } from "./demoReducer";
import { LatestRead } from "./latestRead";

interface DemoContextValue {
  state: DemoState;
  refreshOverview: (force?: boolean) => Promise<void>;
  sendMessage: (message: string) => Promise<void>;
  resolveAction: (confirm: boolean) => Promise<void>;
  loadAudit: (filter?: string, offset?: number) => Promise<void>;
  newConversation: () => void;
}
const DemoContext = createContext<DemoContextValue | null>(null);
const explain = (error: unknown) =>
  error instanceof Error ? error.message : "请求未完成，请检查后端连接。";

export function DemoProvider({ children }: { children: ReactNode }) {
  const [state, dispatch] = useReducer(demoReducer, initialState);
  const stateRef = useRef(state);
  stateRef.current = state;
  const mutationLock = useRef(false);
  const overviewRead = useRef(new LatestRead());
  const auditSequence = useRef(0);
  const message = (entry: Omit<Message, "id">) =>
    dispatch({
      type: "message",
      message: { ...entry, id: crypto.randomUUID() },
    });

  const refreshOverview = useCallback((force = false): Promise<void> => {
    return overviewRead.current.run(async (isLatest) => {
      dispatch({ type: "loading", key: "overview", value: true });
      const account = stateRef.current.account_id;
      const [health, balance, bills] = await Promise.allSettled([
        banking.health(),
        banking.balance(account),
        banking.bills(account),
      ]);
      const errors = [health, balance, bills].flatMap((result) =>
        result.status === "rejected" ? [explain(result.reason)] : [],
      );
      const warnings = [balance, bills].flatMap((result) =>
        result.status === "fulfilled" && result.value.audit_warning
          ? [result.value.audit_warning]
          : [],
      );
      if (!isLatest()) return;
      dispatch({
        type: "overview",
        health: health.status === "fulfilled" ? health.value : null,
        account: balance.status === "fulfilled" ? balance.value : undefined,
        bills: bills.status === "fulfilled" ? bills.value.bills : undefined,
        error: [...new Set([...errors, ...warnings])].join("；") || null,
        updatedAt: new Date().toISOString(),
      });
      dispatch({ type: "loading", key: "overview", value: false });
    }, force);
  }, []);

  const loadAudit = useCallback(async (filter = "", offset = 0) => {
    const sequence = ++auditSequence.current;
    dispatch({ type: "auditStart", filter, offset });
    dispatch({ type: "loading", key: "audit", value: true });
    try {
      const response = await banking.audit(
        stateRef.current.account_id,
        filter || undefined,
        offset,
      );
      if (sequence === auditSequence.current)
        dispatch({
          type: "audit",
          logs: response.logs,
          hasMore: response.logs.length === response.limit,
        });
    } catch (error) {
      if (sequence === auditSequence.current)
        dispatch({ type: "auditError", message: explain(error) });
    } finally {
      if (sequence === auditSequence.current)
        dispatch({ type: "loading", key: "audit", value: false });
    }
  }, []);

  const sendMessage = useCallback(
    async (text: string) => {
      const current = stateRef.current;
      text = text.trim();
      if (
        !text ||
        text.length > 2000 ||
        mutationLock.current ||
        current.uncertain
      )
        return;
      mutationLock.current = true; // 同步锁，先于 React 重渲染阻止连续点击。
      dispatch({ type: "loading", key: "chat", value: true });
      dispatch({ type: "error", message: null });
      message({ role: "user", text });
      try {
        const response = await banking.chat(
          current.account_id,
          text,
          current.conversation_id,
        );
        dispatch({ type: "chat", response });
        message({ role: "assistant", text: response.message, response });
        await Promise.all([
          response.tool_results.length ? refreshOverview() : Promise.resolve(),
          loadAudit(response.audit_id),
        ]);
      } catch (error) {
        dispatch({
          type: "error",
          message: explain(error),
          uncertain: error instanceof RequestError && error.uncertain,
        });
        message({
          role: "assistant",
          text: explain(error),
          transportError: true,
        });
      } finally {
        dispatch({ type: "loading", key: "chat", value: false });
        mutationLock.current = false;
      }
    },
    [refreshOverview, loadAudit],
  );

  const resolveAction = useCallback(
    async (confirm: boolean) => {
      const current = stateRef.current;
      const pending = current.pendingAction;
      const decision = current.pendingDecision;
      if (
        mutationLock.current ||
        current.uncertain ||
        !pending ||
        decision?.status !== "pending" ||
        decision.risk_level !== "yellow" ||
        decision.permission_action !== "pending_confirmation" ||
        decision.require_confirmation !== true ||
        decision.require_verification !== false ||
        decision.action_id !== pending.action_id ||
        pending.account_id !== current.account_id
      )
        return;
      mutationLock.current = true;
      dispatch({ type: "loading", key: "action", value: true });
      dispatch({ type: "error", message: null });
      try {
        const response = await (confirm ? banking.confirm : banking.cancel)(
          current.account_id,
          pending.action_id,
        );
        dispatch({ type: "action", response });
        message({ role: "assistant", text: response.message, response });
        await Promise.all([
          refreshOverview(true),
          loadAudit(response.audit_id),
        ]);
      } catch (error) {
        const notFound =
          error instanceof RequestError && error.code === "ACTION_NOT_FOUND";
        dispatch({
          type: "error",
          message: notFound
            ? "此任务已失效或已被处理，请先核对账单与审计。"
            : explain(error),
          uncertain: error instanceof RequestError && error.uncertain,
          invalidatePending: notFound,
        });
      } finally {
        dispatch({ type: "loading", key: "action", value: false });
        mutationLock.current = false;
      }
    },
    [refreshOverview, loadAudit],
  );

  useEffect(() => {
    void refreshOverview();
  }, [refreshOverview]);
  const newConversation = useCallback(() => {
    if (!mutationLock.current) dispatch({ type: "newConversation" });
  }, []);
  return (
    <DemoContext.Provider
      value={{
        state,
        refreshOverview,
        sendMessage,
        resolveAction,
        loadAudit,
        newConversation,
      }}
    >
      {children}
    </DemoContext.Provider>
  );
}

export function useDemo() {
  const value = useContext(DemoContext);
  if (!value) throw new Error("DemoProvider is required");
  return value;
}
