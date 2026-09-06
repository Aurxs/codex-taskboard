import { useEffect, useState } from "react";
import { t, localizeError } from "../i18n";
import { confirmPlan, createChild, deleteTask, generatePlan, getTask, replaceDependencies, updateTask } from "../api";
import type { ProposedTask, Task, TaskOperation } from "../types";
import { TaskEditor } from "./TaskEditor";
import { postEmbeddedHostMessage } from "../embeddedHost.mjs";

export function GroupTasks({ group, onRefresh, onOpen }: { group: Task; onRefresh: () => Promise<void>; onOpen: (task: Task) => void }) {
  const [editor, setEditor] = useState<Task | "new" | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [proposal, setProposal] = useState<ProposedTask[] | null>(null);
  const [proposalId, setProposalId] = useState<string | null>(null);
  const editable = ["preparing", "paused"].includes(group.groupPhase ?? "") && !["done", "canceled"].includes(group.status);
  const plans = (group.operations ?? []).filter(op => op.kind === "plan");
  const latest = plans.at(-1);
  useEffect(() => {
    if (latest?.state === "ready" && latest.payload.proposal && latest.id !== proposalId) {
      setProposal(latest.payload.proposal.tasks); setProposalId(latest.id);
    }
    if (latest?.state === "confirmed" && latest.id === proposalId) { setProposal(null); setProposalId(null); }
  }, [latest?.id, latest?.state]);
  async function perform(work: () => Promise<unknown>) {
    setBusy(true); setError("");
    try { await work(); await onRefresh(); }
    catch (cause) { setError(cause instanceof Error ? cause.message : t("操作失败")); }
    finally { setBusy(false); }
  }
  function changeProposal(index: number, changes: Partial<ProposedTask>) {
    setProposal(current => current?.map((item, i) => i === index ? { ...item, ...changes } : item) ?? null);
  }
  return <section className="group-tasks" aria-label={t("子任务")}>
    <div className="group-tasks-heading"><h2>{t("子任务")}</h2><span>{t("已集成 {0} / {1}", group.progress?.integrated ?? 0, group.progress?.total ?? 0)}</span></div>
    <div className="group-tasks-toolbar">
      <button type="button" className="button secondary" disabled={!editable || busy} onClick={() => setEditor("new")}>{t("添加子任务")}</button>
      <button type="button" className="button secondary" disabled={!editable || busy || !!latest && ["pending", "agent_running", "uncertain"].includes(latest.state)} onClick={() => void perform(() => generatePlan(group, crypto.randomUUID()))}>{t("AI 拆分")}</button>
      {!editable && <small>{t("暂停任务组后可调整结构")}</small>}
    </div>
    {!group.children?.length && <p className="activity-empty">{t("添加子任务并设置依赖，整理好后统一提交执行。")}</p>}
    <ul className="group-child-list">{group.children?.map(child => <li key={child.id}>
      <button type="button" className="group-child-open" onClick={() => onOpen(child)}><strong>{child.title}</strong><span>{child.identifier} · {child.mergeState === "merged" ? t("已集成") : child.parallel?.needsValidation ? t("需要重新验证") : child.waitReason ? localizeError(child.waitReason) : child.runState === "failed" ? t("执行失败") : child.status === "canceled" ? t("已取消") : child.status === "todo" ? t("待执行") : t("执行中")}</span></button>
      <div className="group-child-actions">
        {child.threadId && <button type="button" onClick={() => postEmbeddedHostMessage({ type: "taskboard:open-thread", payload: { threadId: child.threadId } })}>{t("打开会话")}</button>}
        {editable && !child.threadId && !child.worktreePath && <><button type="button" onClick={() => setEditor(child)}>{t("编辑")}</button><button type="button" disabled={busy} onClick={() => void perform(() => deleteTask(child))}>{t("移除")}</button></>}
      </div>
    </li>)}</ul>
    {latest && <PlanStatus operation={latest} />}
    {proposal && proposalId && <div className="plan-proposal"><h3>{t("子任务草案")}</h3><p>{t("确认后才会加入任务组，不会自动执行。")}</p>
      {proposal.map((item, index) => <div className="proposal-task" key={item.key}>
        <input aria-label={t("草案标题 {0}", index + 1)} value={item.title} disabled={!editable || busy} onChange={event => changeProposal(index, { title: event.target.value })} />
        <textarea aria-label={t("草案描述 {0}", index + 1)} rows={2} value={item.description} disabled={!editable || busy} onChange={event => changeProposal(index, { description: event.target.value })} />
        <details><summary>{t("依赖与修改范围")}</summary>
          {proposal.filter(other => other.key !== item.key).map(other => <label key={other.key} className="proposal-dependency"><input type="checkbox" checked={item.blockedByKeys?.includes(other.key) ?? false} disabled={!editable || busy} onChange={event => changeProposal(index, { blockedByKeys: event.target.checked ? [...(item.blockedByKeys ?? []), other.key] : item.blockedByKeys.filter(key => key !== other.key) })} />{other.title}</label>)}
          <textarea aria-label={t("草案范围 {0}", index + 1)} rows={2} value={(item.writeScopes ?? []).join("\n")} disabled={!editable || busy} placeholder={t("每行一个文件或目录，例如 web/ 或 src/api.py")} onChange={event => changeProposal(index, { writeScopes: event.target.value.split("\n") })} />
        </details>
      </div>)}
      <button type="button" className="button primary" disabled={!editable || busy} onClick={() => void perform(async () => { await confirmPlan(group, proposalId, proposal); setProposal(null); setProposalId(null); })}>{t("确认加入子任务")}</button>
    </div>}
    {error && <p className="form-error" role="alert">{localizeError(error)}</p>}
    {editor && <TaskEditor task={editor === "new" ? null : editor} parent={group} projectId={group.projectId} initialStatus="todo" candidates={group.children ?? []} onClose={() => setEditor(null)}
      onCreate={async input => { await createChild(group, input); await onRefresh(); }}
      onUpdate={async (task, input) => {
        const { blockedByIds, ...changes } = input;
        let next = await updateTask(task.id, task.version, changes);
        if (JSON.stringify(blockedByIds.slice().sort()) !== JSON.stringify(task.blockedBy.map(item => item.id).sort())) next = await replaceDependencies(next.id, next.version, blockedByIds);
        await getTask(next.id); await onRefresh();
      }} />}
  </section>;
}

function PlanStatus({ operation }: { operation: TaskOperation }) {
  const pending = ["pending", "agent_running", "uncertain"].includes(operation.state);
  return <div className="group-plan-status" role="status">
    {pending && <span>{operation.state === "uncertain" ? t("会话结果待核对") : t("正在生成拆分草案…")}</span>}
    {operation.state === "blocked" && <span>{typeof operation.payload.error === "string" ? localizeError(operation.payload.error) : t("草案生成失败，可保留现有子任务后重新生成。")}</span>}
    {operation.payload.threadId && <button type="button" onClick={() => postEmbeddedHostMessage({ type: "taskboard:open-thread", payload: { threadId: operation.payload.threadId } })}>{t("打开拆分会话")}</button>}
  </div>;
}
