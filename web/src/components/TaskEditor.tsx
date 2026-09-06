import { t, localizeError } from "../i18n";
import { AttachmentButton, pastedFiles, readAttachments, validateAttachments } from "./AttachmentButton";
import { useEffect, useRef, useState, type FormEvent } from "react";
import type { AttachmentInput, Task, TaskPriority, TaskStatus } from "../types";
import { PRIORITY_LABELS, TASK_PRIORITIES } from "../types";
import { DependencyPicker } from "./DependencyPicker";
import { TaskPropertyPicker } from "./TaskPropertyPicker";
import { ExecutionSettings } from "./ExecutionSettings";
import type { ExecutionOptions } from "../types";
import { getGitContext } from "../api";
import { LinearIcon } from "./LinearIcon";
import { TaskboardIcon } from "./TaskboardIcon";

export function TaskEditor({
  task,
  initialStatus,
  candidates,
  onClose,
  onCreate,
  onUpdate,
  projectId,
  parent,
}: {
  projectId?: string;
  parent?: Task;
  task: Task | null;
  initialStatus: TaskStatus;
  candidates: Task[];
  onClose: () => void;
  onCreate: (input: { title: string; description: string; priority: TaskPriority; blockedByIds: string[]; attachments: AttachmentInput[] } & ExecutionOptions) => Promise<void>;
  onUpdate: (task: Task, changes: { title: string; description: string; priority: TaskPriority; blockedByIds: string[]; attachments: AttachmentInput[] } & ExecutionOptions) => Promise<void>;
}) {
  const [title, setTitle] = useState(task?.title ?? "");
  const [description, setDescription] = useState(task?.description ?? "");
  const [priorityOpen, setPriorityOpen] = useState(false);
  const [kindOpen, setKindOpen] = useState(false);
  const [modeOpen, setModeOpen] = useState(false);
  const requestId = useRef(crypto.randomUUID());
  const [priority, setPriority] = useState<TaskPriority>(task?.priority ?? "none");
  const [blockedByIds, setBlockedByIds] = useState<string[]>(task?.blockedBy.map((item) => item.id) ?? []);
  const [execution, setExecution] = useState<ExecutionOptions>({ model: task?.model ?? null, reasoningEffort: task?.reasoningEffort ?? null, executionMode: task?.executionMode ?? "local", branch: task?.branch ?? null, kind: task?.kind ?? "task", schedulingMode: task?.schedulingMode ?? (parent ? "parallel" : "exclusive"), writeScopes: task?.writeScopes ?? [], targetBranch: task?.targetBranch ?? parent?.targetBranch ?? null });
  const [defaultTarget, setDefaultTarget] = useState<string | null>(task?.parallel?.defaultTarget as string ?? parent?.targetBranch ?? null);
  useEffect(() => {
    if (!projectId || task || parent) return;
    let active = true;
    void getGitContext(projectId).then(context => { if (active) { setDefaultTarget(context.currentBranch); setExecution(current => ({ ...current, targetBranch: current.targetBranch ?? context.currentBranch })); } }).catch(() => {});
    return () => { active = false; };
  }, [projectId, task?.id, parent?.id]);
  const [files, setFiles] = useState<File[]>([]);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    const close = (event: KeyboardEvent) => { if (event.key === "Escape" && !saving) onClose(); };
    document.addEventListener("keydown", close);
    return () => document.removeEventListener("keydown", close);
  }, [onClose, saving]);
  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!title.trim()) { setError(t("请输入任务标题。")); return; }
    setSaving(true);
    setError(null);
    try {
      const attachments = await readAttachments(files);
      const values = { title: title.trim(), description, priority, blockedByIds, attachments, ...execution, ...(task ? {} : { requestId: requestId.current }) };
      if (task) await onUpdate(task, values); else await onCreate(values);
      onClose();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : t("保存失败。"));
    } finally {
      setSaving(false);
    }
  }
  function addFiles(added: File[]) {
    if (saving || !added.length) return;
    try {
      validateAttachments([...(task?.attachments ?? []), ...files], added);
      setFiles(current => [...current, ...added]);
      setError(null);
    } catch (cause) { setError((cause as Error).message); }
  }
  const available = candidates.filter((candidate) => candidate.id !== task?.id && candidate.status !== "canceled" && (candidate.parentId ?? null) === (parent?.id ?? task?.parentId ?? null));
  return (
    <div className="delete-backdrop" role="presentation" onPointerDown={(event) => { if (event.target === event.currentTarget && !saving) onClose(); }}>
      <form className="task-dialog task-form" role="dialog" aria-modal="true" aria-labelledby="task-editor-title" onSubmit={submit}>
        <header className="dialog-header">
          <div className="dialog-context"><TaskboardIcon name="projectFolder" className="project-avatar" /><strong id="task-editor-title">{task ? t("编辑任务") : t("新建任务")}</strong><span>· {initialStatus === "todo" ? t("待认领") : initialStatus === "in_progress" ? t("处理中") : initialStatus === "in_review" ? t("等你确认") : t("已完成")}</span></div>
          <div className="dialog-header-actions"><button className="icon-button dialog-close" type="button" onClick={onClose} disabled={saving} aria-label={t("关闭")}><LinearIcon name="close" /></button></div>
        </header>
        <div className="form-body" onPaste={event => addFiles(pastedFiles(event))}>
          <label className="composer-title"><span className="sr-only">{t("任务标题")}</span><textarea aria-label={t("任务标题")} autoFocus rows={1} value={title} onChange={(event) => setTitle(event.target.value)} placeholder={t("任务标题")} /></label>
          <label className="composer-description"><span className="sr-only">{t("任务描述")}</span><textarea aria-label={t("任务描述")} rows={3} value={description} onChange={(event) => setDescription(event.target.value)} placeholder={t("补充上下文、验收标准和验证要求……")} /></label>
          {files.length > 0 && <div className="pending-attachments">
            <ul className="composer-attachment-list">{files.map((file, index) => <li key={index}><span className="composer-attachment-copy"><strong>{file.name}</strong><span>{Math.ceil(file.size / 1024)} KB</span></span><button type="button" disabled={saving} aria-label={t("移除 {0}", file.name)} onClick={() => setFiles(current => current.filter((_, i) => i !== index))}>×</button></li>)}</ul>
          </div>}
          <div className="composer-properties">
            {!parent && <TaskPropertyPicker ariaLabel={t("任务形态")} value={execution.kind ?? "task"} open={kindOpen} onOpenChange={setKindOpen} disabled={!!task || saving}
              options={[{ value: "task", label: t("普通任务") }, { value: "parallel_group", label: t("并行任务组") }]}
              onChange={kind => setExecution(current => ({ ...current, kind: kind === "parallel_group" ? "parallel_group" : "task", executionMode: kind === "parallel_group" || current.schedulingMode === "parallel" ? "worktree" : "local", writeScopes: [] }))} />}
            {!parent && <TaskPropertyPicker ariaLabel={t("执行方式")} value={execution.schedulingMode ?? "exclusive"} open={modeOpen} onOpenChange={setModeOpen} disabled={saving || !!task?.threadId || !!task?.worktreePath}
              options={[{ value: "exclusive", label: t("独占执行") }, { value: "parallel", label: t("允许并行") }]}
              onChange={mode => setExecution(current => ({ ...current, schedulingMode: mode === "parallel" ? "parallel" : "exclusive", executionMode: mode === "parallel" || current.kind === "parallel_group" ? "worktree" : "local", branch: null }))} />}

            <TaskPropertyPicker ariaLabel={t("优先级")} value={priority} open={priorityOpen} onOpenChange={setPriorityOpen} options={TASK_PRIORITIES.map(item => ({ value: item, label: PRIORITY_LABELS[item] }))} onChange={value => setPriority(value as TaskPriority)} />
            <DependencyPicker candidates={available} value={blockedByIds} onChange={setBlockedByIds} />
            <AttachmentButton count={(task?.attachments?.length ?? 0) + files.length} disabled={saving} onAdd={addFiles} />
            <ExecutionSettings defaultTarget={defaultTarget} value={execution} onChange={setExecution} isChild={!!parent} workspaceLocked={!!task?.threadId || !!task?.worktreePath} disabled={saving || (task?.status === "in_progress" && !task.parallel?.paused)} errorMessage={error} />
          </div>
          {priority === "draft" && <p className="composer-draft-note">{t("草稿不会被 Codex 认领，修改优先级后即可发布。")}</p>}
          {execution.kind === "parallel_group" && <p className="composer-draft-note">{t("创建后进入详情添加子任务，统一提交后执行。")}</p>}
          {error && <p className="form-error" role="alert">{localizeError(error)}</p>}
        </div>
        <footer className="dialog-footer"><span className="keyboard-note">{task ? t("保存更改") : t("创建后可拖动调整状态")}</span><div className="dialog-actions"><button className="button secondary" type="button" onClick={onClose} disabled={saving}>{t("取消")}</button><button className="button primary" type="submit" disabled={saving}>{saving ? t("保存中…") : task ? t("保存更改") : priority === "draft" ? t("保存草稿") : t("创建任务")}</button></div></footer>
      </form>
    </div>
  );
}
