import { ActivityTool, ActivityToolIcon } from "./ActivityTool";
import { PlanQuestions } from "./PlanQuestions";
import { TaskPlan, TaskPlanButton, planStatus } from "./TaskPlan";
import { GroupTasks } from "./GroupTasks";
import { getTask, type TaskAction } from "../api";
import { t, getLocale, localizeError } from "../i18n";
import { AttachmentCard } from "./AttachmentCard";
import { AttachmentPreview } from "./AttachmentPreview";
import { AttachmentButton, pastedFiles, readAttachments, validateAttachments } from "./AttachmentButton";
import { DependencyPicker } from "./DependencyPicker";
import { useEffect, useMemo, useRef, useState } from "react";
import type { ActivityItem, Interaction, Task, TaskPriority, TaskStatus } from "../types";
import { PRIORITY_LABELS, RUN_STATE_LABELS, STATUS_LABELS, TASK_PRIORITIES } from "../types";
import { LinearIcon } from "./LinearIcon";
import { PriorityIcon, ProjectIcon, StatusIcon } from "./SemanticIcons";
import { DetailExecutionSettings as ExecutionSettings } from "./DetailExecutionSettings";
import { IssueRelations } from "./IssueRelations";
import { TaskPropertyPicker } from "./TaskPropertyPicker";
import { postEmbeddedHostMessage } from "../embeddedHost.mjs";

function relativeTime(value?: string | null) {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  const seconds = Math.max(0, Math.floor((Date.now() - date.getTime()) / 1000));
  if (seconds < 60) return t("刚刚");
  if (seconds < 3600) return t("{0} 分钟前", Math.floor(seconds / 60));
  if (seconds < 86400) return t("{0} 小时前", Math.floor(seconds / 3600));
  return date.toLocaleDateString(getLocale(), { month: "short", day: "numeric" });
}

function plainMarkdown(value: string) {
  return value.replace(/```[\s\S]*?```/g, "").replace(/!\[[^\]]*\]\([^)]*\)/g, "").replace(/[`*_>#-]/g, "").trim();
}

function activityLabel(kind?: string) {
  return kind === "agentMessage" ? "Codex" : kind === "userMessage" ? t("用户") : kind === "fileChange" ? t("文件变化") : t("工具调用");
}

function isToolActivity(item: ActivityItem) {
  return !["agentMessage", "userMessage"].includes(item.kind);
}

function InteractionRow({ interaction, onResolve }: { interaction: Interaction; onResolve: (interaction: Interaction, response: unknown) => Promise<void> }) {
  const [answer, setAnswer] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const questions = interaction.questions ?? [];
  const kind = interaction.kind.toLowerCase();
  const userInput = kind === "user_input" || kind === "async_user_input" || kind.includes("requestuserinput");
  const permission = kind.includes("permission");
  const approval = !permission && !userInput;
  async function resolve(decision: string) {
    setBusy(true);
    try {
      const response = decision === "cancel" ? { decision: "cancel" } : userInput
        ? { answers: Object.fromEntries(questions.map((question, index) => [question.id ?? String(index), { answers: answer[question.id ?? String(index)] ? [answer[question.id ?? String(index)]] : [] }])) }
        : permission
          ? { permissions: (decision === "accept" || decision === "acceptForSession") ? interaction.payload?.permissions ?? {} : {}, scope: decision === "acceptForSession" ? "session" : "turn" }
          : { decision };
      await onResolve(interaction, response);
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="interaction-card">
      <div className="interaction-heading"><span className="interaction-icon">{userInput ? "?" : "!"}</span><div><strong>{interaction.title ?? (userInput ? t("Codex 需要你的回答") : t("Codex 请求批准"))}</strong><small>{interaction.message ?? interaction.command ?? t("请确认这个请求")}</small></div></div>
      {interaction.permissionScope && <div className="scope-list">{interaction.permissionScope.map((scope) => <code key={scope}>{scope}</code>)}</div>}
      {questions.map((question, index) => {
        const id = question.id ?? String(index);
        return <fieldset className="interaction-question" key={id} disabled={busy}>
          <legend>{question.header && <strong>{question.header} · </strong>}{question.question}</legend>
          {question.options?.map(option => <label className="question-option" key={option.label}>
            <input type="radio" name={`${interaction.id}-${id}`} checked={answer[id] === option.label} onChange={() => setAnswer(current => ({ ...current, [id]: option.label }))} />
            <span>{option.label}{option.description && <small>{option.description}</small>}</span>
          </label>)}
          <textarea aria-label={question.question} value={answer[id] ?? ""} onChange={event => setAnswer(current => ({ ...current, [id]: event.target.value }))} placeholder={t("输入你的回答或补充说明…")} />
        </fieldset>;
      })}
      <div className="interaction-actions approval-actions">
        <button className="quiet-button" type="button" disabled={busy} onClick={() => void resolve("cancel")}>{t("取消")}</button>
        {(approval || permission) && <button className="secondary-button" type="button" disabled={busy} onClick={() => void resolve("decline")}>{t("拒绝")}</button>}
        {(approval || permission) && <button className="secondary-button" type="button" disabled={busy} onClick={() => void resolve("acceptForSession")}>{t("当前会话批准")}</button>}
        <button className="primary-button" type="button" disabled={busy || (userInput && questions.some((question, index) => !answer[question.id ?? String(index)]?.trim()))} onClick={() => void resolve(userInput ? "submit" : "accept")}>{userInput ? t("提交回答") : permission ? t("批准权限") : t("本次批准")}</button>
      </div>
    </div>
  );
}

export function TaskDetail({
  task,
  tasks,
  onBack,
  onUpdate,
  onDependencies,
  onAction,
  onResolveInteraction,
  onOpenTask,
}: {
  onOpenTask: (task: Task) => void;
  task: Task;
  tasks: Task[];
  onBack: () => void;
  onUpdate: (task: Task, changes: (Partial<Pick<Task, "title" | "description" | "priority" | "model" | "reasoningEffort" | "executionMode" | "branch" | "kind" | "schedulingMode" | "writeScopes" | "targetBranch">> & { attachments?: import("../types").AttachmentInput[]; removeAttachmentIds?: string[] })) => Promise<Task | null>;
  onDependencies: (task: Task, ids: string[]) => Promise<Task | null>;
  onAction: (task: Task, action: TaskAction, feedback?: string, targetStatus?: "in_review" | "done", attachmentIds?: string[]) => Promise<Task | null>;
  onResolveInteraction: (interaction: Interaction, response: unknown) => Promise<void>;
}) {
  const [current, setCurrent] = useState(task);
  const dirty = useRef({ title: false, description: false, priority: false });
  const drafts = useRef({ title: task.title, description: task.description, priority: task.priority });
  const [title, setTitle] = useState(task.title);
  const [description, setDescription] = useState(task.description);
  const [priority, setPriority] = useState<TaskPriority>(task.priority);
  const [editingDescription, setEditingDescription] = useState(false);
  const [feedback, setFeedback] = useState("");
  const [saving, setSaving] = useState(false);
  const [propertyOpen, setPropertyOpen] = useState(false);
  const [followup, setFollowup] = useState("");
  const [sending, setSending] = useState(false);
  const [followupAttachmentIds, setFollowupAttachmentIds] = useState<string[]>([]);
  const followupAttachments = (current.attachments ?? []).filter(file => followupAttachmentIds.includes(file.id));
  const [threadInput, setThreadInput] = useState("");
  const [parentTask, setParentTask] = useState<Task | null>(null);
  useEffect(() => { let active = true; if (task.parentId) void getTask(task.parentId).then(parent => { if (active) setParentTask(parent); }).catch(() => {}); else setParentTask(null); return () => { active = false; }; }, [task.parentId, task.version]);
  const [worktreeInput, setWorktreeInput] = useState("");
  const isGroup = current.kind === "parallel_group";
  const managed = !!current.parallel?.managed;
  const paused = !!current.parallel?.paused;
  const dependencyCandidates = current.parentId ? parentTask?.children ?? [] : tasks;
  const dependencyLocked = !!current.parentId && (!!current.threadId || !!current.worktreePath || !parentTask || !["preparing", "paused"].includes(parentTask.groupPhase ?? ""));
  async function refreshCurrent() { setCurrent(await getTask(current.id)); }
  const planning = !!current.plan?.hold;
  const planStarting = ["pending", "starting", "uncertain"].includes(current.plan?.state ?? "");
  const [planBusy, setPlanBusy] = useState(false);
  const planSending = useRef(false);
  async function planAction(action: TaskAction, text?: string) {
    if (planSending.current) return null;
    planSending.current = true; setPlanBusy(true);
    try {
      const next = await onAction(current, action, text);
      if (next) setCurrent(previous => next.version >= previous.version ? next : previous);
      return next;
    } finally { planSending.current = false; setPlanBusy(false); }
  }
  const sendingRef = useRef(false);
  async function sendFollowup() {
    if (planning && (planStarting || !current.plan?.threadId || planSending.current)) return;
    if ((!followup.trim() && !followupAttachments.length) || sendingRef.current || uploading.current || saving) return;
    sendingRef.current = true;
    setSending(true);
    const text = followup;
    try {
      const next = planning ? await planAction("plan_continue", text.trim()) : await onAction(current, "follow_up", text.trim() || t("请查看附件。"), undefined, followupAttachments.map(file => file.id));
      if (next) {
        setCurrent(previous => next.version >= previous.version ? next : previous);
        setFollowup(value => value === text ? "" : value);
        setFollowupAttachmentIds([]);
      }
    } finally { sendingRef.current = false; setSending(false); }
  }

  const [previewFile, setPreviewFile] = useState<import("../types").TaskAttachment | null>(null);
  useEffect(() => setPreviewFile(null), [task.id]);
  const uploading = useRef(false);
  const [attachmentError, setAttachmentError] = useState<string | null>(null);
  const [addingAttachments, setAddingAttachments] = useState(false);
  const [removingAttachmentId, setRemovingAttachmentId] = useState<string | null>(null);
  async function addFiles(files: File[], forFollowup = false) {
    if (!files.length || uploading.current || saving || sendingRef.current) return;
    uploading.current = true;
    setAddingAttachments(true);
    setAttachmentError(null);
    try {
      validateAttachments(current.attachments ?? [], files);
      const attachments = await readAttachments(files);
      const next = await onUpdate(current, { attachments });
      if (next) {
        setCurrent(previous => next.version >= previous.version ? next : previous);
        if (forFollowup) setFollowupAttachmentIds(ids => [...ids, ...(next.attachments ?? []).filter(file => !(current.attachments ?? []).some(existing => existing.id === file.id)).map(file => file.id)]);
      }
      else setAttachmentError(t("附件未保存，请重新添加。"));
    } catch (cause) { setAttachmentError(cause instanceof Error ? cause.message : t("附件添加失败。")); }
    finally { uploading.current = false; setAddingAttachments(false); }
  }

  async function removeFile(id: string) {
    if (uploading.current || saving) return;
    uploading.current = true;
    setRemovingAttachmentId(id);
    setAttachmentError(null);
    try {
      const next = await onUpdate(current, { removeAttachmentIds: [id] });
      if (next) {
        setCurrent(previous => next.version >= previous.version ? next : previous);
        setPreviewFile(file => file?.id === id ? null : file);
      } else setAttachmentError(t("附件未移除，请重试。"));
    } catch (cause) { setAttachmentError(cause instanceof Error ? cause.message : t("附件未移除，请重试。")); }
    finally { uploading.current = false; setRemovingAttachmentId(null); }
  }

  useEffect(() => {
    if (task.version < current.version) return;
    setCurrent(task);
    if (!dirty.current.title) setTitle(task.title);
    if (!dirty.current.description) setDescription(task.description);
    if (!dirty.current.priority) setPriority(task.priority);
  }, [task]);

  const pendingInteractions = useMemo(() => (current.interactions ?? []).filter((interaction) => interaction.status === "pending"), [current.interactions]);
  const planInteractions = planning ? pendingInteractions.filter(i => i.payload?.threadId === current.plan?.threadId) : [];
  async function save(changes: (Partial<Pick<Task, "title" | "description" | "priority" | "model" | "reasoningEffort" | "executionMode" | "branch" | "kind" | "schedulingMode" | "writeScopes" | "targetBranch">> & { attachments?: import("../types").AttachmentInput[]; removeAttachmentIds?: string[] })) {
    setSaving(true);
    try {
      const next = await onUpdate(current, changes);
      if (next) {
        setCurrent(current => next.version >= current.version ? next : current);
        for (const field of ["title", "description", "priority"] as const) {
          if (changes[field] !== undefined && drafts.current[field] === changes[field]) dirty.current[field] = false;
        }
      }
      return next;
    } finally {
      setSaving(false);
    }
  }
  async function saveText() {
    const next = await save({ description });
    if (next) setEditingDescription(false);
  }
  async function dependencyChange(ids: string[]) {
    const next = await onDependencies(current, ids);
    if (next) setCurrent(next);
  }

  return (
    <div className="issue-detail">
      {previewFile && <AttachmentPreview key={previewFile.id} file={previewFile} onClose={() => setPreviewFile(null)} />}
      <div className="issue-parent-link has-parent"><button className="detail-back-button" type="button" onClick={onBack} aria-label={t("返回议题看板")}><LinearIcon name="chevronLeft" /></button><span className="issue-parent-prefix">{t("返回")}</span><span className="issue-relation-id">{current.identifier}</span></div>
      <div className="issue-detail-scroll">
        <div className="issue-detail-layout">
          <div className="issue-detail-main">
            <div className="issue-detail-main-scroll">
            <div className="issue-editor">
              <div className="issue-editor-content" onPaste={event => { void addFiles(pastedFiles(event)); }}>
                <textarea className="issue-title-input" rows={1} value={title} disabled={saving} onChange={(event) => { dirty.current.title = true; drafts.current.title = event.target.value; setTitle(event.target.value); }} onBlur={() => { if (title.trim() && title.trim() !== current.title) { drafts.current.title = title.trim(); setTitle(title.trim()); void save({ title: title.trim() }); } }} />
                {editingDescription ? <div className="issue-description-composer"><textarea className="issue-description-input" rows={10} value={description} onChange={(event) => { dirty.current.description = true; drafts.current.description = event.target.value; setDescription(event.target.value); }} onBlur={() => void saveText()} autoFocus /></div> : <div className={`issue-description-read${description ? "" : " empty"}`} tabIndex={0} onClick={() => setEditingDescription(true)} onKeyDown={(event) => { if (event.key === "Enter") setEditingDescription(true); }}><div className="issue-description-document">{description ? description.split(/\n\n+/).map((paragraph, index) => <p key={index}>{plainMarkdown(paragraph)}</p>) : t("添加描述…")}</div></div>}
                {(current.attachments ?? []).length > 0 && <section className="issue-attachments"><h2>{t("附件")}</h2><ul className="attachment-card-list">{current.attachments?.map(file => <AttachmentCard key={file.id} file={file} disabled={saving || addingAttachments || removingAttachmentId !== null} onPreview={() => setPreviewFile(file)} onRemove={() => void removeFile(file.id)} />)}</ul></section>}
                <div className="property-row issue-detail-inline-properties">
                  <TaskPropertyPicker value={priority} options={TASK_PRIORITIES.map((item) => ({ value: item, label: PRIORITY_LABELS[item], className: `priority-${item}`, icon: <PriorityIcon priority={item} size={14} /> }))} open={propertyOpen} onOpenChange={setPropertyOpen} onChange={(value) => { const next = value as TaskPriority; dirty.current.priority = true; drafts.current.priority = next; setPriority(next); void save({ priority: next }); }} ariaLabel={t("优先级")} title={t("优先级：{0}", PRIORITY_LABELS[priority])} triggerClassName={`property-priority priority-${priority}`} />
                  <span className="property-control"><StatusIcon status={current.status === "canceled" ? "todo" : current.status as TaskStatus} size={14} /><span>{current.status === "canceled" ? t("已取消") : STATUS_LABELS[current.status as TaskStatus] ?? current.status}</span></span>
                  <DependencyPicker disabled={dependencyLocked} candidates={dependencyCandidates.filter(item => item.id !== current.id && item.status !== "canceled")} value={current.blockedBy.map(item => item.id)} onChange={ids => void dependencyChange(ids)} />
                  <AttachmentButton count={current.attachments?.length ?? 0} disabled={saving || addingAttachments || removingAttachmentId !== null} onAdd={files => void addFiles(files)} />
                  <TaskPlanButton task={current} disabled={saving || editingDescription || planBusy} onAction={planAction} />
                  {(current.threadId || current.plan?.threadId) && <button type="button" className="property-control" onClick={() => postEmbeddedHostMessage({ type: "taskboard:open-thread", payload: { threadId: planning ? current.plan?.threadId : current.threadId || current.plan?.threadId } })}><ProjectIcon size={14} /><span>{t("在 Codex 中打开")}</span></button>}
                  {current.threadId && current.plan?.threadId && !planning && <button type="button" className="property-control" onClick={() => postEmbeddedHostMessage({ type: "taskboard:open-thread", payload: { threadId: current.plan?.threadId } })}>{t("打开计划会话")}</button>}
                </div>
                {!isGroup && <TaskPlan task={current} disabled={saving || planBusy} onAction={planAction} />}
                {current.priority === "draft" && <p className="composer-draft-note">{t("草稿不会被 Codex 认领，修改优先级后即可发布。")}</p>}
                {addingAttachments && <p className="composer-draft-note" role="status">{t("正在添加附件…")}</p>}
                {removingAttachmentId && <p className="composer-draft-note" role="status">{t("正在移除附件…")}</p>}
                {attachmentError && <p className="form-error" role="alert">{localizeError(attachmentError)}</p>}
              </div>
            </div>
            {isGroup && <GroupTasks group={current} onRefresh={refreshCurrent} onOpen={onOpenTask} />}
            {(current.waitReason || paused || current.parallel?.needsValidation) && <p className="task-wait-reason" role="status">{current.waitReason ? localizeError(current.waitReason) : current.parallel?.needsValidation ? t("前置成果已修改，需要重新验证") : t("已暂停")}</p>}
            {managed && current.mergeState && current.mergeState !== "none" && <p className="task-merge-state">{t("合入状态")} · {current.mergeState === "pending_review" ? t("等待审阅") : current.mergeState === "queued" ? t("等待合入") : current.mergeState === "merging" ? t("合入中") : current.mergeState === "merged" ? t("已合入") : t("需要处理")}</p>}
            <section className="activity-section">
              <div className="activity-heading"><h2>{t("执行记录")}</h2><span>{planning ? planStatus(current) : current.runState ? RUN_STATE_LABELS[current.runState] : t("未运行")}</span></div>
              {current.activityError && <p className="activity-empty">{localizeError(current.activityError)}</p>}
              <div className="activity-stream">
                {current.lastError && <div className="activity-entry"><span className="activity-rail-icon"><LinearIcon name="alert" /></span><p><strong>{t("执行错误")}</strong> {localizeError(current.lastError)}</p><time>{relativeTime(current.updatedAt)}</time></div>}
                {current.lastMessage && !(current.activity ?? []).some(item => item.kind === "agentMessage" && item.message === current.lastMessage) && <div className="activity-entry activity-agent-message"><span className="activity-rail-icon">✦</span><p><strong>Codex</strong> {current.lastMessage}</p><time>{relativeTime(current.updatedAt)}</time></div>}
                {[...(current.activity ?? [])].reverse().map((item, index) => <div className={`activity-entry${item.kind === "agentMessage" ? " activity-agent-message" : ""}${isToolActivity(item) ? " activity-tool-entry" : ""}`} key={item.id ?? `${item.createdAt}-${index}`}>
                  <span className="activity-rail-icon">{item.kind === "agentMessage" ? "✦" : isToolActivity(item) ? <ActivityToolIcon item={item} /> : "↗"}</span>
                  <div className="activity-content">{isToolActivity(item) ? <ActivityTool item={item} /> : <><p><strong>{activityLabel(item.kind)}</strong>{item.status === "running" && <small>  {t("· 进行中")}</small>}</p><p>{item.message ?? item.summary ?? item.detail ?? ""}</p></>}</div>
                  <time>{relativeTime(item.createdAt)}</time>
                </div>)}
                {[...(current.runs ?? [])].reverse().map((run, index) => <div className="activity-entry" key={run.id ?? `run-${index}`}><span className="activity-rail-icon"><LinearIcon name="terminal" /></span><p><strong>TaskRun</strong> {run.lastOutputSummary ?? run.summary ?? run.phase ?? run.runState ?? run.state ?? t("运行记录")}</p><time>{relativeTime(run.updatedAt ?? run.createdAt)}</time></div>)}
                {current.lastError == null && current.lastMessage == null && (current.activity ?? []).length === 0 && (!current.runs || current.runs.length === 0) && <p className="activity-empty">{t("Codex 开始工作后，最新进展会显示在这里。")}</p>}
              </div>
              {pendingInteractions.length > 0 && <div className="interaction-list">{pendingInteractions.filter(i => !current.plan?.hold || i.payload?.threadId !== current.plan?.threadId).map((interaction) => <InteractionRow key={interaction.id} interaction={interaction} onResolve={onResolveInteraction} />)}</div>}
            </section>
            </div>
            {planning && current.plan?.error && <p className="form-error" role="alert">{localizeError(current.plan.error)}</p>}
            {planInteractions.length > 0 ? <div className="plan-interaction-composer">{planInteractions.map(interaction => ["user_input", "async_user_input"].includes(interaction.kind) || interaction.kind.toLowerCase().includes("requestuserinput") ? <PlanQuestions key={interaction.id} interaction={interaction} onResolve={onResolveInteraction} /> : <InteractionRow key={interaction.id} interaction={interaction} onResolve={onResolveInteraction} />)}</div> : !isGroup && (planning || current.threadId && ["in_progress", "in_review", "done"].includes(current.status)) && <form className={`task-followup${planning ? " task-plan-composer" : ""}`} onPaste={event => { if (!planning) void addFiles(pastedFiles(event), true); }} onSubmit={event => { event.preventDefault(); void sendFollowup(); }}>
              {followupAttachments.length > 0 && <ul className="attachment-card-list task-followup-attachments">{followupAttachments.map(file => <AttachmentCard key={file.id} file={file} disabled={sending || saving || addingAttachments || removingAttachmentId !== null} onPreview={() => setPreviewFile(file)} onRemove={() => void removeFile(file.id)} />)}</ul>}
              <textarea aria-label={planning ? t("补充计划要求") : t("跟进 Codex 会话")} placeholder={planning ? t("补充计划要求，或请 Codex 根据回答生成最终计划…") : t("向 Codex 补充细节或继续处理…")} title={t("Enter 发送 · Shift+Enter 换行")} rows={2} value={followup} onChange={event => setFollowup(event.target.value)} onKeyDown={event => { if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) { event.preventDefault(); void sendFollowup(); } }} />
              <div className="task-followup-footer">{planning ? null : <AttachmentButton iconOnly count={followupAttachments.length} disabled={sending || saving || addingAttachments || removingAttachmentId !== null} onAdd={files => void addFiles(files, true)} />}<button className="task-followup-send" type="submit" aria-label={planning ? t("发送补充") : t("发送跟进消息")} title={sending ? t("正在发送…") : planning ? t("发送补充") : t("发送跟进消息")} aria-busy={sending} disabled={sending || saving || planning && (planBusy || planStarting || !current.plan?.threadId) || addingAttachments || removingAttachmentId !== null || (!followup.trim() && !followupAttachments.length)}><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M12 19V5m-6 6 6-6 6 6" /></svg></button></div>
              {addingAttachments && <p className="task-followup-status" role="status">{t("正在添加附件…")}</p>}
              {attachmentError && <p className="form-error" role="alert">{localizeError(attachmentError)}</p>}
            </form>}
          </div>
          <aside className="issue-properties">
            <h2>{t("任务属性")}</h2>
            <div className="detail-primary-actions">
              {isGroup ? <>
                {current.groupPhase === "preparing" && <button className="detail-open-thread-action" type="button" disabled={!current.progress?.total || saving} onClick={() => void onAction(current, "group_submit")}>{t("提交执行")}</button>}
                {current.groupPhase === "submitted" && current.status === "todo" && <button className="detail-open-thread-action" type="button" onClick={() => void onAction(current, "run")}>{t("运行任务组")}</button>}
                {(current.groupPhase === "submitted" || current.groupPhase === "pausing") && !["done", "canceled"].includes(current.status) && <button className="detail-copy-action" type="button" onClick={() => void onAction(current, "group_pause")}>{current.groupPhase === "pausing" ? t("确认暂停") : t("暂停任务组")}</button>}
                {current.groupPhase === "paused" && current.status !== "canceled" && <button className="detail-open-thread-action" type="button" onClick={() => void onAction(current, "group_resume")}>{t("继续任务组")}</button>}
              </> : <>
                {current.status === "todo" && !paused && !current.parentId && <button className="detail-open-thread-action" type="button" disabled={!current.ready || saving || current.queued || current.plan?.hold} onClick={() => void onAction(current, "run")}><LinearIcon name="play" />{current.queued ? t("已排队") : current.priority === "draft" ? t("草稿暂不执行") : current.ready ? t("立即交给 Codex") : t("等待前置任务完成")}</button>}
                {current.status === "in_progress" && !paused && ["waiting_quota", "failed"].includes(current.runState ?? "") && current.mergeState !== "blocked" && <button className="detail-open-thread-action" type="button" disabled={current.queued} onClick={() => void onAction(current, "retry")}><LinearIcon name="play" />{t("重试任务")}</button>}
                {(current.status === "in_progress" || managed && current.status === "in_review") && !paused && !current.parentId && <button className="detail-copy-action" type="button" onClick={() => void onAction(current, managed ? "pause" : "interrupt_requeue")}><LinearIcon name="pause" />{managed ? t("暂停任务") : t("暂停并退回草稿")}</button>}
                {paused && !current.parentId && current.status !== "canceled" && <button className="detail-open-thread-action" type="button" onClick={() => void onAction(current, "resume")}>{t("继续任务")}</button>}
              </>}
              {current.status === "in_review" && !paused && current.mergeState !== "merging" && current.mergeState !== "queued" && <button className="detail-open-thread-action" type="button" onClick={() => void onAction(current, "complete")}><LinearIcon name="check" />{managed ? t("确认并合入") : t("接受并完成")}</button>}
              {!isGroup && current.status === "in_review" && !paused && <><textarea className="review-feedback-input" rows={3} value={feedback} onChange={(event) => setFeedback(event.target.value)} placeholder={t("审阅未通过时填写反馈…")} /><button className="detail-copy-action" type="button" disabled={!feedback.trim()} onClick={() => { void onAction(current, "submit_review_feedback", feedback.trim()).then(next => { if (next) setFeedback(""); }); }}>{t("退回修改")}</button></>}
              {managed && current.mergeState === "blocked" && !paused && <button type="button" className="detail-open-thread-action" onClick={() => void onAction(current, "merge_retry")}>{t("重试合并")}</button>}
              {!!current.parallel?.uncertainThread && <div className="worktree-recovery"><input aria-label={t("已有会话 ID")} placeholder={t("已有会话 ID")} value={threadInput} onChange={event => setThreadInput(event.target.value)} /><button type="button" disabled={!threadInput.trim()} onClick={() => void onAction(current, "attach_thread", threadInput.trim())}>{t("关联已有会话")}</button></div>}
              {current.parallel?.uncertainWorktree && <div className="worktree-recovery"><input aria-label={t("已有工作树路径")} placeholder={t("已有工作树路径")} value={worktreeInput} onChange={event => setWorktreeInput(event.target.value)} /><button type="button" disabled={!worktreeInput.trim()} onClick={() => void onAction(current, "attach_worktree", worktreeInput.trim())}>{t("关联已有工作树")}</button></div>}
              {current.operations?.filter(op => op.kind === "merge" && op.payload.threadId && op.state !== "completed").map(op => <button key={op.id} type="button" className="detail-copy-action" onClick={() => postEmbeddedHostMessage({ type: "taskboard:open-thread", payload: { threadId: op.payload.threadId } })}>{t("打开合并会话")}</button>)}
            </div>
            <div className="issue-property-list"><button className="detail-property-row" type="button"><span>{t("编号")}</span><strong>{current.identifier}</strong></button><div className="detail-property-row"><span>{t("状态")}</span><strong>{current.status === "canceled" ? t("已取消") : STATUS_LABELS[current.status as TaskStatus] ?? current.status}</strong></div><div className="detail-property-row"><span>{t("优先级")}</span><strong>{PRIORITY_LABELS[current.priority]}</strong></div><div className="detail-property-row"><span>{t("更新")}</span><strong>{relativeTime(current.updatedAt)}</strong></div><div className="detail-property-row"><span>{t("运行阶段")}</span><strong>{current.runState ? RUN_STATE_LABELS[current.runState] : "—"}</strong></div></div>
            <ExecutionSettings key={current.id} defaultTarget={current.parallel?.defaultTarget as string ?? null} scopeSummary={isGroup ? [...new Set(current.children?.flatMap(child => child.writeScopes ?? []) ?? [])] : undefined} value={current} isChild={!!current.parentId} workspaceLocked={!!current.threadId || !!current.worktreePath || !!current.parallel?.baseCommit} disabled={saving || (current.status === "in_progress" && !paused)} onChange={async changes => !!await save(changes)} />
            {current.status === "in_progress" && !paused && <small className="settings-note">{t("暂停任务后可修改；下次执行生效。")}</small>}
            {current.worktreePath && <small className="task-worktree-path">{current.worktreePath}</small>}
            {managed && <details className="merge-history"><summary>{t("合入记录")} · {(current.operations ?? []).filter(op => op.kind === "merge").length}</summary>
              <p>{t("合入目标")} <code>{current.targetBranch ?? "—"}</code></p>
              {typeof current.parallel?.baseCommit === "string" && <p>{t("起始提交")} <code title={current.parallel.baseCommit}>{current.parallel.baseCommit.slice(0, 12)}</code></p>}
              {(current.operations ?? []).filter(op => op.kind === "merge").slice().reverse().map(op => <div className="merge-history-entry" key={op.id}>
                <strong>{String(op.payload.targetBranch ?? "")} · {op.state === "completed" ? t("已合入") : op.state === "blocked" || op.state === "uncertain" ? t("需要处理") : op.state === "superseded" ? t("目标已更新") : t("合入中")}</strong>
                <p>{t("源提交")} <code title={String(op.payload.sourceCommit ?? "")}>{String(op.payload.sourceCommit ?? "—").slice(0, 12)}</code></p>
                <p>{t("结果提交")} <code title={String(op.payload.resultCommit ?? "")}>{String(op.payload.resultCommit ?? "—").slice(0, 12)}</code></p>
                {op.payload.threadId && <button className="detail-copy-action" type="button" onClick={() => postEmbeddedHostMessage({ type: "taskboard:open-thread", payload: { threadId: op.payload.threadId } })}>{t("打开合并会话")}</button>}
              </div>)}
            </details>}
            <IssueRelations disabled={dependencyLocked} task={current} candidates={dependencyCandidates} onChange={(ids) => void dependencyChange(ids)} />
            {current.status !== "done" && current.status !== "canceled" && <button className="detail-copy-action detail-cancel-action" type="button" onClick={() => { if (window.confirm(t("确定取消这个任务吗？"))) void onAction(current, "cancel"); }}>{t("取消任务")}</button>}
          </aside>
        </div>
      </div>
    </div>
  );
}
