import { useState } from "react";
import type { Task } from "../types";
import type { TaskAction } from "../api";
import { t } from "../i18n";
import { LinearIcon } from "./LinearIcon";

type PlanProps = {
  task: Task; disabled: boolean;
  onAction: (action: TaskAction, text?: string) => Promise<Task | null>;
};

export function planStatus(task: Task) {
  const plan = task.plan;
  if (task.interactions?.some(i => i.status === "pending" && i.payload?.threadId === plan?.threadId)) return t("计划模式 · 等待回应");
  if (["pending", "starting", "uncertain"].includes(plan?.state ?? "")) return t("计划启动中…");
  if (plan?.state === "ready") return t("正在保存计划…");
  if (plan?.state === "blocked") return t("计划需要处理");
  if (plan?.state === "agent_running") return t("正在执行计划模式");
  return t("计划模式 · 等待补充");
}

export function TaskPlanButton({ task, disabled, onAction }: PlanProps) {
  if (task.kind === "parallel_group" || task.status !== "todo") return null;
  return <button type="button" className="property-control task-plan-trigger" disabled={disabled || task.queued || ["pending", "starting"].includes(task.plan?.state ?? "")} title={task.plan?.hold ? t("取消规划") : undefined} onClick={() => void onAction(task.plan?.hold ? "plan_cancel" : "plan_start")}>
    <LinearIcon name="copy" /><span>{task.plan?.hold ? planStatus(task) : task.plan?.acceptedText ? t("重新规划") : t("开始计划")}</span>
  </button>;
}

export function TaskPlan({ task, disabled, onAction }: PlanProps) {
  const [draft, setDraft] = useState<string | null>(null);
  const plan = task.plan;
  const text = plan?.hold ? (plan.state === "ready" ? plan.text : null) : plan?.acceptedText;
  if (!text) return null;
  const editable = task.status === "todo" && !task.queued && (plan?.hold ? plan.state === "ready" : !!plan?.acceptedText);
  return <section className="task-plan-document" aria-label={t("任务计划")}>
    {draft !== null ? <>
      <textarea className="task-plan-editor" aria-label={t("编辑计划")} value={draft} disabled={disabled} onChange={e => setDraft(e.target.value)} autoFocus />
      <div className="interaction-actions"><button type="button" className="quiet-button" disabled={disabled} onClick={() => setDraft(null)}>{t("取消")}</button><button type="button" className="primary-button" disabled={disabled || !draft.trim()} onClick={async () => { if (await onAction("plan_save", draft)) setDraft(null); }}>{t("保存计划")}</button></div>
    </> : <div className="task-plan-content" role={editable ? "button" : undefined} tabIndex={0} aria-label={editable ? t("编辑计划") : t("最终计划")} aria-disabled={editable && disabled} onClick={() => { if (editable && !disabled) setDraft(text); }} onKeyDown={e => { if (editable && !disabled && (e.key === "Enter" || e.key === " ")) { e.preventDefault(); setDraft(text); } }}><pre>{text}</pre></div>}
  </section>;
}
