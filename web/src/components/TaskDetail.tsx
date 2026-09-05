import { AttachmentPreview } from "./AttachmentPreview";
import { AttachmentButton, pastedFiles, readAttachments, validateAttachments } from "./AttachmentButton";
import { DependencyPicker } from "./DependencyPicker";
import { useEffect, useMemo, useRef, useState } from "react";
import type { ActivityItem, Interaction, Task, TaskPriority, TaskStatus } from "../types";
import { PRIORITY_LABELS, RUN_STATE_LABELS, STATUS_LABELS, TASK_PRIORITIES } from "../types";
import { LinearIcon } from "./LinearIcon";
import { PriorityIcon, ProjectIcon, StatusIcon } from "./SemanticIcons";
import { ExecutionSettings } from "./ExecutionSettings";
import { IssueRelations } from "./IssueRelations";
import { TaskPropertyPicker } from "./TaskPropertyPicker";

function relativeTime(value?: string | null) {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  const seconds = Math.max(0, Math.floor((Date.now() - date.getTime()) / 1000));
  if (seconds < 60) return "刚刚";
  if (seconds < 3600) return `${Math.floor(seconds / 60)} 分钟前`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)} 小时前`;
  return date.toLocaleDateString("zh-CN", { month: "short", day: "numeric" });
}

function plainMarkdown(value: string) {
  return value.replace(/```[\s\S]*?```/g, "").replace(/!\[[^\]]*\]\([^)]*\)/g, "").replace(/[`*_>#-]/g, "").trim();
}

function CommandDetails({ item }: { item: ActivityItem }) {
  const command = typeof item.data?.command === "string" ? item.data.command : item.message ?? item.summary ?? item.detail ?? "";
  const executable = command.trim().match(/^(?:"([^"]+)"|'([^']+)'|(\S+))/);
  const program = executable?.[1] ?? executable?.[2] ?? executable?.[3] ?? "";
  const output = item.data?.aggregatedOutput ?? item.data?.output;
  return <>
    <p className="activity-command-heading"><strong>命令</strong><span title={program}>{program}</span>{item.status === "running" && <small>进行中</small>}</p>
    <details>
      <summary>展开详细</summary>
      <div className="activity-command-box"><strong>命令</strong><pre>{command || "暂无命令"}</pre></div>
      <div className="activity-command-box"><strong>输出</strong><pre>{typeof output === "string" ? output || "暂无输出" : output == null ? "暂无输出" : JSON.stringify(output, null, 2)}</pre></div>
    </details>
  </>;
}

function InteractionRow({ interaction, onResolve }: { interaction: Interaction; onResolve: (interaction: Interaction, response: unknown) => Promise<void> }) {
  const [answer, setAnswer] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const questions = interaction.questions ?? [];
  const kind = interaction.kind.toLowerCase();
  const userInput = kind === "user_input" || kind.includes("requestuserinput");
  const permission = kind.includes("permission");
  const approval = !permission && !userInput;
  async function resolve(decision: string) {
    setBusy(true);
    try {
      const response = userInput
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
      <div className="interaction-heading"><span className="interaction-icon">{userInput ? "?" : "!"}</span><div><strong>{interaction.title ?? (userInput ? "Codex 需要你的回答" : "Codex 请求批准")}</strong><small>{interaction.message ?? interaction.command ?? "请确认这个请求"}</small></div></div>
      {interaction.permissionScope && <div className="scope-list">{interaction.permissionScope.map((scope) => <code key={scope}>{scope}</code>)}</div>}
      {questions.map((question, index) => <label className="interaction-question" key={question.id ?? String(index)}><span>{question.header ?? question.question}</span><input value={answer[question.id ?? String(index)] ?? ""} onChange={(event) => setAnswer((current) => ({ ...current, [question.id ?? String(index)]: event.target.value }))} placeholder={question.question} /></label>)}
      <div className="interaction-actions approval-actions">
        <button className="quiet-button" type="button" disabled={busy} onClick={() => void resolve("cancel")}>取消</button>
        {(approval || permission) && <button className="secondary-button" type="button" disabled={busy} onClick={() => void resolve("decline")}>拒绝</button>}
        {(approval || permission) && <button className="secondary-button" type="button" disabled={busy} onClick={() => void resolve("acceptForSession")}>当前会话批准</button>}
        <button className="primary-button" type="button" disabled={busy} onClick={() => void resolve(userInput ? "submit" : "accept")}>{userInput ? "提交回答" : permission ? "批准权限" : "本次批准"}</button>
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
}: {
  task: Task;
  tasks: Task[];
  onBack: () => void;
  onUpdate: (task: Task, changes: (Partial<Pick<Task, "title" | "description" | "priority" | "model" | "reasoningEffort">> & { attachments?: import("../types").AttachmentInput[] })) => Promise<Task | null>;
  onDependencies: (task: Task, ids: string[]) => Promise<Task | null>;
  onAction: (task: Task, action: "run" | "retry" | "interrupt_requeue" | "submit_review_feedback" | "complete" | "cancel", feedback?: string, targetStatus?: "in_review" | "done") => Promise<Task | null>;
  onResolveInteraction: (interaction: Interaction, response: unknown) => Promise<void>;
}) {
  const [current, setCurrent] = useState(task);
  const [title, setTitle] = useState(task.title);
  const [description, setDescription] = useState(task.description);
  const [priority, setPriority] = useState<TaskPriority>(task.priority);
  const [editingDescription, setEditingDescription] = useState(false);
  const [feedback, setFeedback] = useState("");
  const [saving, setSaving] = useState(false);
  const [propertyOpen, setPropertyOpen] = useState(false);

  const [previewFile, setPreviewFile] = useState<import("../types").TaskAttachment | null>(null);
  useEffect(() => setPreviewFile(null), [task.id]);
  const uploading = useRef(false);
  const [attachmentError, setAttachmentError] = useState<string | null>(null);
  const [addingAttachments, setAddingAttachments] = useState(false);
  async function addFiles(files: File[]) {
    if (!files.length || uploading.current || saving) return;
    uploading.current = true;
    setAddingAttachments(true);
    setAttachmentError(null);
    try {
      validateAttachments(current.attachments ?? [], files);
      const attachments = await readAttachments(files);
      const next = await onUpdate(current, { attachments });
      if (next) setCurrent(next);
      else setAttachmentError("附件未保存，请重新添加。");
    } catch (cause) { setAttachmentError(cause instanceof Error ? cause.message : "附件添加失败。"); }
    finally { uploading.current = false; setAddingAttachments(false); }
  }

  useEffect(() => {
    setCurrent(task);
    setTitle(task.title);
    setDescription(task.description);
    setPriority(task.priority);
  }, [task]);

  const pendingInteractions = useMemo(() => (current.interactions ?? []).filter((interaction) => interaction.status === "pending"), [current.interactions]);
  async function save(changes: (Partial<Pick<Task, "title" | "description" | "priority" | "model" | "reasoningEffort">> & { attachments?: import("../types").AttachmentInput[] })) {
    setSaving(true);
    try {
      const next = await onUpdate(current, changes);
      if (next) setCurrent(next);
    } finally {
      setSaving(false);
    }
  }
  async function saveText() {
    const next = await onUpdate(current, { title: title.trim(), description });
    if (next) setCurrent(next);
    setEditingDescription(false);
  }
  async function dependencyChange(ids: string[]) {
    const next = await onDependencies(current, ids);
    if (next) setCurrent(next);
  }

  return (
    <div className="issue-detail">
      {previewFile && <AttachmentPreview key={previewFile.id} file={previewFile} onClose={() => setPreviewFile(null)} />}
      <div className="issue-detail-scroll">
        <div className="issue-detail-layout">
          <div className="issue-detail-main">
            <div className="issue-editor">
              <div className="issue-editor-content" onPaste={event => { void addFiles(pastedFiles(event)); }}>
                <div className="issue-parent-link has-parent"><button className="detail-back-button" type="button" onClick={onBack} aria-label="返回议题看板"><LinearIcon name="chevronLeft" /></button><span className="issue-parent-prefix">任务</span><span className="issue-relation-id">{current.identifier}</span></div>
                <textarea className="issue-title-input" rows={1} value={title} disabled={saving} onChange={(event) => setTitle(event.target.value)} onBlur={() => { if (title.trim() && title.trim() !== current.title) void save({ title: title.trim() }); }} />
                {editingDescription ? <div className="issue-description-composer"><textarea className="issue-description-input" rows={10} value={description} onChange={(event) => setDescription(event.target.value)} onBlur={() => void saveText()} autoFocus /></div> : <div className={`issue-description-read${description ? "" : " empty"}`} tabIndex={0} onClick={() => setEditingDescription(true)} onKeyDown={(event) => { if (event.key === "Enter") setEditingDescription(true); }}><div className="issue-description-document">{description ? description.split(/\n\n+/).map((paragraph, index) => <p key={index}>{plainMarkdown(paragraph)}</p>) : "添加描述…"}</div></div>}
                {(current.attachments ?? []).length > 0 && <section className="issue-attachments"><h2>附件</h2><ul className="attachment-list">{current.attachments?.map(file => <li key={file.id}><button type="button" className="attachment-link" onClick={() => setPreviewFile(file)}><span className="attachment-copy"><strong>{file.name}</strong><span>{Math.ceil(file.size / 1024)} KB · 查看</span></span></button></li>)}</ul></section>}
                <div className="property-row issue-detail-inline-properties">
                  <TaskPropertyPicker value={priority} options={TASK_PRIORITIES.map((item) => ({ value: item, label: PRIORITY_LABELS[item], className: `priority-${item}`, icon: <PriorityIcon priority={item} size={14} /> }))} open={propertyOpen} onOpenChange={setPropertyOpen} onChange={(value) => { const next = value as TaskPriority; setPriority(next); void save({ priority: next }); }} ariaLabel="优先级" title={`优先级：${PRIORITY_LABELS[priority]}`} triggerClassName={`property-priority priority-${priority}`} />
                  <span className="property-control"><StatusIcon status={current.status === "canceled" ? "todo" : current.status as TaskStatus} size={14} /><span>{current.status === "canceled" ? "已取消" : STATUS_LABELS[current.status as TaskStatus] ?? current.status}</span></span>
                  <DependencyPicker candidates={tasks.filter(item => item.id !== current.id && item.status !== "canceled")} value={current.blockedBy.map(item => item.id)} onChange={ids => void dependencyChange(ids)} />
                  <AttachmentButton count={current.attachments?.length ?? 0} disabled={saving || addingAttachments} onAdd={files => void addFiles(files)} />
                  {current.threadId && <span className="property-control"><ProjectIcon size={14} /><span>Codex thread</span></span>}
                </div>
                {current.priority === "draft" && <p className="composer-draft-note">草稿不会被 Codex 认领，修改优先级后即可发布。</p>}
                {addingAttachments && <p className="composer-draft-note" role="status">正在添加附件…</p>}
                {attachmentError && <p className="form-error" role="alert">{attachmentError}</p>}
              </div>
            </div>
            <section className="activity-section">
              <div className="activity-heading"><h2>执行记录</h2><span>{current.runState ? RUN_STATE_LABELS[current.runState] : "未运行"}</span></div>
              {current.activityError && <p className="activity-empty">{current.activityError}</p>}
              <div className="activity-stream">
                {current.lastError && <div className="activity-entry"><span className="activity-rail-icon"><LinearIcon name="alert" /></span><p><strong>执行错误</strong> {current.lastError}</p><time>{relativeTime(current.updatedAt)}</time></div>}
                {current.lastMessage && !(current.activity ?? []).some(item => item.kind === "agentMessage" && item.message === current.lastMessage) && <div className="activity-entry"><span className="activity-rail-icon">✦</span><p><strong>Codex</strong> {current.lastMessage}</p><time>{relativeTime(current.updatedAt)}</time></div>}
                {(current.activity ?? []).map((item, index) => <div className="activity-entry" key={item.id ?? `${item.createdAt}-${index}`}><span className="activity-rail-icon">{item.kind === "agentMessage" ? "✦" : "↗"}</span><div className="activity-content">{item.kind === "commandExecution" ? <CommandDetails item={item} /> : <><p><strong>{item.kind === "agentMessage" ? "Codex" : item.kind === "fileChange" ? "文件变化" : "工具调用"}</strong>{item.status === "running" && <small> · 进行中</small>}</p><p>{item.message ?? item.summary ?? item.detail ?? ""}</p>{item.data && item.kind !== "agentMessage" && <details><summary>查看调用详情</summary><pre>{JSON.stringify(item.data, null, 2)}</pre></details>}</>}</div><time>{relativeTime(item.createdAt)}</time></div>)}
                {current.runs?.map((run, index) => <div className="activity-entry" key={run.id ?? `run-${index}`}><span className="activity-rail-icon"><LinearIcon name="terminal" /></span><p><strong>TaskRun</strong> {run.lastOutputSummary ?? run.summary ?? run.phase ?? run.runState ?? run.state ?? "运行记录"}</p><time>{relativeTime(run.updatedAt ?? run.createdAt)}</time></div>)}
                {current.lastError == null && current.lastMessage == null && (current.activity ?? []).length === 0 && (!current.runs || current.runs.length === 0) && <p className="activity-empty">Codex 开始工作后，最新进展会显示在这里。</p>}
              </div>
              {pendingInteractions.length > 0 && <div className="interaction-list">{pendingInteractions.map((interaction) => <InteractionRow key={interaction.id} interaction={interaction} onResolve={onResolveInteraction} />)}</div>}
            </section>
          </div>
          <aside className="issue-properties">
            <h2>任务属性</h2>
            <div className="detail-primary-actions">
              {current.status === "todo" && <button className="detail-open-thread-action" type="button" disabled={!current.ready || saving} onClick={() => void onAction(current, "run")}><LinearIcon name="play" />{current.priority === "draft" ? "草稿暂不执行" : current.ready ? "立即交给 Codex" : "等待前置任务完成"}</button>}
              {current.status === "in_progress" && current.runState === "waiting_quota" && <button className="detail-open-thread-action" type="button" onClick={() => void onAction(current, "retry")}><LinearIcon name="play" />手动续跑</button>}
              {current.status === "in_progress" && current.runState === "failed" && <button className="detail-open-thread-action" type="button" onClick={() => void onAction(current, "retry")}><LinearIcon name="play" />重试任务</button>}
              {current.status === "in_progress" && <button className="detail-copy-action" type="button" onClick={() => void onAction(current, "interrupt_requeue")}><LinearIcon name="pause" />暂停并重新排队</button>}
              {current.status === "in_review" && <><button className="detail-open-thread-action" type="button" onClick={() => void onAction(current, "complete")}><LinearIcon name="check" />接受并完成</button><textarea className="review-feedback-input" rows={3} value={feedback} onChange={(event) => setFeedback(event.target.value)} placeholder="审阅未通过时填写反馈…" /><button className="detail-copy-action" type="button" disabled={!feedback.trim()} onClick={() => { void onAction(current, "submit_review_feedback", feedback.trim()); setFeedback(""); }}>退回修改</button></>}
            </div>
            <div className="issue-property-list"><button className="detail-property-row" type="button"><span>编号</span><strong>{current.identifier}</strong></button><div className="detail-property-row"><span>状态</span><strong>{STATUS_LABELS[current.status as TaskStatus] ?? current.status}</strong></div><div className="detail-property-row"><span>优先级</span><strong>{PRIORITY_LABELS[current.priority]}</strong></div><div className="detail-property-row"><span>更新</span><strong>{relativeTime(current.updatedAt)}</strong></div><div className="detail-property-row"><span>运行阶段</span><strong>{current.runState ? RUN_STATE_LABELS[current.runState] : "—"}</strong></div></div>
            <ExecutionSettings value={current} disabled={saving || current.status === "in_progress"} onChange={value => void save(value)} />
            <IssueRelations task={current} candidates={tasks} onChange={(ids) => void dependencyChange(ids)} />
            {(current.status === "todo" || current.status === "in_review") && <button className="detail-copy-action detail-cancel-action" type="button" onClick={() => { if (window.confirm("确定取消这个任务吗？")) void onAction(current, "cancel"); }}>取消任务</button>}
          </aside>
        </div>
      </div>
    </div>
  );
}
