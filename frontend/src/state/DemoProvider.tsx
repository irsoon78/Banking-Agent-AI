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
import { actionArguments, canResolve, isCardPending, matchesRecovery, MutationGate } from "./cardFlow";
import { validateInvestigation } from "./investigationRead";

interface DemoContextValue {
  state: DemoState;
  refreshOverview: (force?: boolean) => Promise<void>;
  sendMessage: (message: string) => Promise<void>;
  resolveAction: (confirm: boolean) => Promise<void>;
  loadAudit: (filter?: string, offset?: number) => Promise<void>;
  newConversation: () => void;
  refreshCards: () => Promise<void>;
  recoverCardOperation: () => Promise<void>;
  switchAccount: (account: string) => void;
}
const DemoContext = createContext<DemoContextValue | null>(null);
const explain = (error: unknown) =>
  error instanceof Error ? error.message : "请求未完成，请检查后端连接。";

export function DemoProvider({ children }: { children: ReactNode }) {
  const [state, dispatch] = useReducer(demoReducer, initialState);
  const stateRef = useRef(state);
  stateRef.current = state;
  const mutationLock = useRef(new MutationGate());
  const overviewRead = useRef(new LatestRead());
  const auditSequence = useRef(0);
  const cardSequence = useRef(0);
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
      if (!isLatest() || stateRef.current.account_id !== account) return;
      dispatch({
        type: "overview",
        account_id: account,
        health: health.status === "fulfilled" ? health.value : null,
        account: balance.status === "fulfilled" ? balance.value : undefined,
        bills: bills.status === "fulfilled" ? bills.value.bills : undefined,
        error: [...new Set([...errors, ...warnings])].join("；") || null,
        updatedAt: new Date().toISOString(),
      });
      dispatch({ type: "loading", key: "overview", value: false });
    }, force);
  }, []);

  const refreshCards = useCallback(async () => {
    const account = stateRef.current.account_id;
    const sequence = ++cardSequence.current;
    dispatch({ type: "cardsStart" });
    dispatch({ type: "loading", key: "cards", value: true });
    try {
      const response = await banking.cards(account);
      if (sequence === cardSequence.current && stateRef.current.account_id === account) {
        if (response.account_id !== account || response.cards?.some(card => card.account_id !== account)) throw new Error("卡片结果账户不匹配，已拒绝展示。");
        dispatch({ type: "cards", account, response });
      }
    } catch (e) {
      if (sequence === cardSequence.current && stateRef.current.account_id === account)
        dispatch({ type: "cardsError", account, message: explain(e), auditId: e instanceof RequestError ? e.auditId : undefined });
    } finally {
      if (sequence === cardSequence.current && stateRef.current.account_id === account)
        dispatch({ type: "loading", key: "cards", value: false });
    }
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
        mutationLock.current.busy ||
        current.uncertain
      )
        return;
      if (!mutationLock.current.claim()) return;
      dispatch({ type: "loading", key: "chat", value: true });
      dispatch({ type: "error", message: null });
      message({ role: "user", text });
      try {
        const response = await banking.chat(
          current.account_id,
          text,
          current.conversation_id,
        );
        if (response.investigation) validateInvestigation(response.investigation, current.account_id, response.investigation_id ?? undefined);
        dispatch({ type: "chat", response });
        message({ role: "assistant", text: response.message, response });
        await Promise.all([
          response.tool_results.length ? refreshOverview() : Promise.resolve(),
          loadAudit(response.audit_id),
          refreshCards(),
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
        mutationLock.current.release();
      }
    },
    [refreshOverview, loadAudit, refreshCards],
  );

  const resolveAction = useCallback(
    async (confirm: boolean) => {
      const current = stateRef.current;
      const pending = current.pendingAction;
      const decision = current.pendingDecision;
      if (
        mutationLock.current.busy ||
        current.uncertain ||
        !pending || !canResolve(pending, decision, current.account_id, current.conversation_id)
      )
        return;
      if (!mutationLock.current.claim()) return;
      dispatch({ type: "loading", key: "action", value: true });
      dispatch({ type: "error", message: null });
      try {
        const response = await (confirm ? banking.confirm : banking.cancel)(
          ...actionArguments(pending),
        );
        if (isCardPending(pending) && response.status === "completed" &&
            (response.execution_result?.ok !== true || !matchesRecovery(response.execution_result?.card_operation, pending)))
          throw new RequestError("未取得与原卡片任务匹配的成功凭据，结果仍需核对，请勿重试。", "INVALID_CARD_RECEIPT", 200, true, response.audit_id);
        dispatch({ type: "action", response });
        message({ role: "assistant", text: response.message, response });
        await Promise.all([
          refreshOverview(true),
          loadAudit(response.audit_id),
          refreshCards(),
        ]);
      } catch (error) {
        const notFound =
          error instanceof RequestError && ["ACTION_NOT_FOUND", "SESSION_NOT_FOUND"].includes(error.code);
        const cardUnknown = isCardPending(pending) && error instanceof RequestError && (error.uncertain || error.code === "ACTION_NOT_FOUND");
        dispatch({
          type: "error",
          message: notFound
            ? "此任务或会话已失效，请重新查询卡片、账单与审计后再发起。"
            : explain(error),
          uncertain: (error instanceof RequestError && error.uncertain) || cardUnknown,
          invalidatePending: notFound,
          unknownCardAction: cardUnknown && isCardPending(pending) ? pending : undefined,
        });
      } finally {
        dispatch({ type: "loading", key: "action", value: false });
        mutationLock.current.release();
      }
    },
    [refreshOverview, loadAudit, refreshCards],
  );

  const recoverCardOperation = useCallback(async () => {
    const current = stateRef.current;
    const pending = current.unknownCardAction;
    if (!pending || current.loading.recovery) return;
    dispatch({ type: "loading", key: "recovery", value: true });
    dispatch({ type: "recoveryStatus", status: "loading" });
    try {
      const response = await banking.cardOperation(current.account_id, pending.action_id);
      if (!matchesRecovery(response.card_operation, pending)) throw new Error("凭据与原卡片操作不匹配，状态仍未知。");
      dispatch({ type: "recoveredCard", account: current.account_id, response });
      await Promise.all([refreshCards(), refreshOverview(true), loadAudit(response.card_operation.audit_id ?? response.audit_id)]);
    } catch (e) {
      dispatch({ type: "recoveryStatus", status: e instanceof RequestError && e.code === "OPERATION_NOT_FOUND" ? "not_found" : "unknown" });
      dispatch({ type: "error", message: e instanceof RequestError && e.code === "OPERATION_NOT_FOUND" ? "未查到此操作凭据，不能据此断定未执行。状态仍未知，请核对审计，不要重新执行。" : explain(e) });
    } finally {
      dispatch({ type: "loading", key: "recovery", value: false });
    }
  }, [refreshCards, refreshOverview, loadAudit]);

  useEffect(() => {
    void refreshOverview(true);
  }, [refreshOverview, state.account_id]);
  const newConversation = useCallback(() => {
    if (!mutationLock.current.busy) dispatch({ type: "newConversation" });
  }, []);
  const switchAccount = useCallback((account: string) => {
    if (mutationLock.current.busy || stateRef.current.pendingAction || stateRef.current.uncertain) return;
    ++auditSequence.current; ++cardSequence.current;
    dispatch({ type: "account", account });
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
        refreshCards,
        recoverCardOperation,
        switchAccount,
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
