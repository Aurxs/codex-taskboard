import { useRef, useState, type ReactNode } from "react";
import type { Task } from "../types";
import type { TaskAction } from "../api";
import { localizeError, t } from "../i18n";
import { postEmbeddedHostMessage } from "../embeddedHost.mjs";

export function TaskPlan({ task, disabled, onAction, children }: {
  task: Task; disabled: boolean; children?: ReactNode;
  onAction: (action: TaskAction, text?: string) => Promise<Task | null>;
}) {
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const sending = useRef(false);
  const plan = task.plan;
  const hold = plan?.hold;
  const starting = ["pending", "starting", "uncertain"].includes(plan?.state ?? "");
  const pending = task.interactions?.some(i => i.status === "pending" && i.payload?.threadId === plan?.threadId);
  async function act(action: TaskAction) {
    if (sending.current) return;
    sending.current = true; setBusy(true);
    try { if (await onAction(action, action === "plan_continue" ? text.trim() : undefined)) setText(""); }
    finally { sending.current = false; setBusy(false); }
  }
  if (!plan?.operationId && task.status !== "todo") return null;
  const stateLabel = starting ? t("计划启动中…") : plan?.state === "ready" ? t("最终计划已保存，待确认") : plan?.state === "agent_running" ? t("正在规划…") : plan?.state === "blocked" ? t("计划需要处理") : t("可继续补充计划要求");
  return <section className="task-plan-section" aria-label={t("任务计划")}>
    <div className="activity-heading"><h2>{t("任务计划")}</h2>
      {!hold && task.status === "todo" && <button className="secondary-button" disabled={disabled || busy || task.queued} onClick={() => void act("plan_start")}>Plan · {plan?.acceptedText ? t("重新规划") : t("生成详细计划")}</button>}
      {hold && <span role="status">{stateLabel}</span>}
    </div>
    <p className="settings-note">{hold ? t("规划期间不会执行此任务。回答下方问题或补充要求，确认最终计划后恢复调度。") : t("先与 Codex 澄清需求；确认后的计划会自动附加到后续执行中。")}</p>
    {plan?.error && hold && <p className="form-error" role="alert">{localizeError(plan.error)}</p>}
    {plan?.text && hold && <details open className="task-plan-document"><summary>{t("最终计划")}</summary><pre>{plan.text}</pre></details>}
    {plan?.acceptedText && <details className="task-plan-document" open={!hold}><summary>{t("已确认的执行计划")}</summary><pre>{plan.acceptedText}</pre></details>}
    {plan?.threadId && <button className="quiet-button" onClick={() => postEmbeddedHostMessage({ type: "taskboard:open-thread", payload: { threadId: plan.threadId } })}>{t("打开计划会话")}</button>}
    {children}
    {hold && <>
      <textarea className="plan-followup" aria-label={t("补充计划要求")} placeholder={t("补充计划要求，或请 Codex 根据回答生成最终计划…")} value={text} onChange={event => setText(event.target.value)} disabled={busy} />
      <div className="interaction-actions">
        <button className="quiet-button" disabled={busy || ["pending", "starting"].includes(plan?.state ?? "")} onClick={() => void act("plan_cancel")}>{t("取消规划")}</button>
        <button className="secondary-button" disabled={busy || starting || !plan?.threadId || !text.trim()} onClick={() => void act("plan_continue")}>{t("发送补充")}</button>
        <button className="primary-button" disabled={busy || plan?.state !== "ready" || pending} onClick={() => void act("plan_accept")}>{t("确认计划")}</button>
      </div>
    </>}
  </section>;
}
