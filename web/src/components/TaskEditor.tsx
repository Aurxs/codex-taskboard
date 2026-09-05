import { useEffect, useState, type FormEvent } from "react";
import type { Task, TaskPriority, TaskStatus } from "../types";
import { PRIORITY_LABELS, TASK_PRIORITIES } from "../types";
import { DependencyPicker } from "./DependencyPicker";
import { TaskPropertyPicker } from "./TaskPropertyPicker";
import { ExecutionSettings } from "./ExecutionSettings";
import type { ExecutionOptions } from "../types";
import { LinearIcon } from "./LinearIcon";
import { TaskboardIcon } from "./TaskboardIcon";

export function TaskEditor({
  task,
  initialStatus,
  candidates,
  onClose,
  onCreate,
  onUpdate,
}: {
  task: Task | null;
  initialStatus: TaskStatus;
  candidates: Task[];
  onClose: () => void;
  onCreate: (input: { title: string; description: string; priority: TaskPriority; blockedByIds: string[] } & ExecutionOptions) => Promise<void>;
  onUpdate: (task: Task, changes: { title: string; description: string; priority: TaskPriority; blockedByIds: string[] } & ExecutionOptions) => Promise<void>;
}) {
  const [title, setTitle] = useState(task?.title ?? "");
  const [description, setDescription] = useState(task?.description ?? "");
  const [priorityOpen, setPriorityOpen] = useState(false);
  const [priority, setPriority] = useState<TaskPriority>(task?.priority ?? "none");
  const [blockedByIds, setBlockedByIds] = useState<string[]>(task?.blockedBy.map((item) => item.id) ?? []);
  const [execution, setExecution] = useState<ExecutionOptions>({ model: task?.model ?? null, reasoningEffort: task?.reasoningEffort ?? null });
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    const close = (event: KeyboardEvent) => { if (event.key === "Escape") onClose(); };
    document.addEventListener("keydown", close);
    return () => document.removeEventListener("keydown", close);
  }, [onClose]);
  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!title.trim()) { setError("请输入任务标题。"); return; }
    setSaving(true);
    setError(null);
    try {
      const values = { title: title.trim(), description, priority, blockedByIds, ...execution };
      if (task) await onUpdate(task, values); else await onCreate(values);
      onClose();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "保存失败。");
    } finally {
      setSaving(false);
    }
  }
  const available = candidates.filter((candidate) => candidate.id !== task?.id && candidate.status !== "canceled");
  return (
    <div className="delete-backdrop" role="presentation" onPointerDown={(event) => { if (event.target === event.currentTarget && !saving) onClose(); }}>
      <form className="task-dialog task-form" role="dialog" aria-modal="true" aria-labelledby="task-editor-title" onSubmit={submit}>
        <header className="dialog-header">
          <div className="dialog-context"><TaskboardIcon name="projectFolder" className="project-avatar" /><strong id="task-editor-title">{task ? "编辑任务" : "新建任务"}</strong><span>· {initialStatus === "todo" ? "待认领" : initialStatus === "in_progress" ? "处理中" : initialStatus === "in_review" ? "等你确认" : "已完成"}</span></div>
          <div className="dialog-header-actions"><button className="icon-button dialog-close" type="button" onClick={onClose} aria-label="关闭"><LinearIcon name="close" /></button></div>
        </header>
        <div className="form-body">
          <label className="composer-title"><span className="sr-only">任务标题</span><textarea autoFocus rows={1} value={title} onChange={(event) => setTitle(event.target.value)} placeholder="任务标题" /></label>
          <label className="composer-description"><span className="sr-only">任务描述</span><textarea rows={3} value={description} onChange={(event) => setDescription(event.target.value)} placeholder="补充上下文、验收标准和验证要求……" /></label>
          <div className="composer-properties">
            <TaskPropertyPicker ariaLabel="优先级" value={priority} open={priorityOpen} onOpenChange={setPriorityOpen} options={TASK_PRIORITIES.map(item => ({ value: item, label: PRIORITY_LABELS[item] }))} onChange={value => setPriority(value as TaskPriority)} />
            <DependencyPicker candidates={available} value={blockedByIds} onChange={setBlockedByIds} />
          </div>
          <ExecutionSettings value={execution} onChange={setExecution} />
          {error && <p className="form-error" role="alert">{error}</p>}
        </div>
        <footer className="dialog-footer"><span className="keyboard-note">{task ? "保存更改" : "创建后可拖动调整状态"}</span><div className="dialog-actions"><button className="button secondary" type="button" onClick={onClose} disabled={saving}>取消</button><button className="button primary" type="submit" disabled={saving}>{saving ? "保存中…" : task ? "保存更改" : "创建任务"}</button></div></footer>
      </form>
    </div>
  );
}
