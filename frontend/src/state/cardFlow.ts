import type { ActionResponse, CardOperation, CardPendingAction, PendingAction, SecureResponse } from "../api/types.ts";

export const CARD_NOTICE = "银行卡及消费数据均为模拟数据。临时冻结只改变模拟卡片状态，不代表已阻断真实支付，不影响模拟账户转账余额和历史消费数据。";
export const isCardPending = (pending: PendingAction): pending is CardPendingAction =>
  pending.action === "temporarily_freeze_card" || pending.action === "remove_temporary_freeze";
export const cardActionLabel = (action: string) => ({ temporarily_freeze_card: "临时冻结", remove_temporary_freeze: "解除临时冻结", report_card_loss: "正式挂失", cancel_card_loss_report: "解挂" }[action] ?? action);

export function canResolve(pending: PendingAction | null, decision: SecureResponse | null, account: string, conversation: string | null): boolean {
  return !!pending && ["transfer", "transfer_money", "temporarily_freeze_card", "remove_temporary_freeze"].includes(pending.action) && decision?.status === "pending" && decision.risk_level === "yellow" &&
    decision.permission_action === "pending_confirmation" && decision.require_confirmation === true &&
    decision.require_verification === false && decision.action_id === pending.action_id && pending.account_id === account &&
    (!isCardPending(pending) || (!!pending.conversation_id && pending.conversation_id === conversation));
}

export function validCardOperation(r: CardOperation | undefined): r is CardOperation {
  return !!r && r.ok === true && r.result_status === "completed" && r.source === "synthetic_card_demo_v1" &&
    !!r.operation_id && !!r.action_id && !!r.card_id && !!r.account_id && !!r.executed_at &&
    Number.isSafeInteger(r.before?.version) && Number.isSafeInteger(r.after?.version) && r.before.version >= 0 &&
    r.after.version === r.before.version + 1 && r.before.loss_status === "normal" && r.after.loss_status === "normal" &&
    ((r.operation === "temporary_freeze" && r.before.temporary_frozen === false && r.after.temporary_frozen === true) ||
     (r.operation === "temporary_unfreeze" && r.before.temporary_frozen === true && r.after.temporary_frozen === false));
}

export function hasCardReceipt(response: ActionResponse): boolean {
  const r = response.execution_result.card_operation;
  return response.status === "completed" && response.execution_result.ok === true && validCardOperation(r) &&
    response.action_id === r.action_id && response.action === (r.operation === "temporary_freeze" ? "temporarily_freeze_card" : "remove_temporary_freeze");
}

export function matchesRecovery(r: CardOperation | undefined, pending: CardPendingAction): r is CardOperation {
  return validCardOperation(r) && r.account_id === pending.account_id && r.action_id === pending.action_id &&
    r.card_id === pending.params.card_id && r.before.version === pending.params.expected_version &&
    r.operation === (pending.action === "temporarily_freeze_card" ? "temporary_freeze" : "temporary_unfreeze");
}

export function actionArguments(pending: PendingAction): [string, string, string?] {
  return isCardPending(pending) ? [pending.account_id, pending.action_id, pending.conversation_id] : [pending.account_id, pending.action_id];
}

// 与Provider共用的同步门闩，先于React重渲染阻止双击。授权始终由后端决定。
export class MutationGate {
  busy = false;
  claim() { if (this.busy) return false; this.busy = true; return true; }
  release() { this.busy = false; }
}
