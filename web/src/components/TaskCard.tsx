import completeIcon from "../assets/figma-taskboard/card-complete.svg";
import processingAnimation from "../assets/figma-taskboard/loading-16.svg";
import type { Task } from "../types";
import { PRIORITY_LABELS } from "../types";
import { PriorityIcon } from "./SemanticIcons";

function markdownExcerpt(value: string) {
  return value
    .replace(/!\[[^\]]*\]\([^)]*\)/g, "")
    .replace(/[`#>*_~\-]/g, "")
    .replace(/\s+/g, " ")
    .trim();
}

function elapsedTime(value: string | null, now = Date.now()) {
  if (!value) return "";
  const seconds = Math.max(0, Math.floor((now - new Date(value).getTime()) / 1000));
  if (seconds < 60) return `${seconds}s`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m`;
  return `${Math.floor(minutes / 60)}h`;
}

export function TaskCard({
  task,
  isDragging,
  onEdit,
  onComplete,
  onDragStart,
  onDragEnd,
}: {
  task: Task;
  isDragging: boolean;
  onEdit: (task: Task) => void;
  onComplete: (task: Task) => void;
  onDragStart: (task: Task, height: number) => void;
  onDragEnd: () => void;
}) {
  const blocked = task.status === "todo" && (!task.ready || task.blockedBy.some((item) => item.status !== "done"));
  const pendingInteraction = task.interactions?.some((interaction) => interaction.status === "pending");
  const processing = task.status === "in_progress";
  const excerpt = markdownExcerpt(task.description);
  const processingLabel = task.runState === "waiting_quota"
    ? "等待额度"
    : task.runState === "waiting_approval"
      ? "等待批准"
      : task.runState === "waiting_input"
        ? "等待回答"
        : task.runState === "failed"
          ? "执行失败"
          : task.runState === "starting"
            ? "启动中"
            : "执行中";
  return (
    <article
      className={`task-card task-card-main status-${task.status}${processing ? " is-processing-card" : ""}${processing && task.runState === "running" ? " is-running-card" : ""}${isDragging ? " is-dragging" : ""}${pendingInteraction ? " is-unread" : ""}`}
      data-task-id={task.id}
      draggable={task.status !== "done" && task.status !== "canceled"}
      aria-labelledby={`task-${task.id}-title`}
      onDragStart={(event) => {
        event.dataTransfer.effectAllowed = "move";
        event.dataTransfer.setData("text/plain", task.id);
        event.dataTransfer.setData("application/x-taskboard-task", task.id);
        onDragStart(task, event.currentTarget.offsetHeight);
      }}
      onDragEnd={onDragEnd}
    >
      <button className="task-card-open" type="button" aria-label={`打开 ${task.identifier}: ${task.title}`} onClick={() => onEdit(task)} />
      <div className="card-topline">
        <span className="card-reference"><span className="task-identifier">ID: {task.identifier}</span></span>
        {pendingInteraction && <span className="task-unread-dot" aria-label="有待处理交互" />}
        {task.status === "in_review" && <button className="task-card-complete" type="button" onClick={(event) => { event.stopPropagation(); onComplete(task); }}><img src={completeIcon} alt="" /><span>完成</span></button>}
      </div>
      <h3 id={`task-${task.id}-title`}>{task.title}</h3>
      {excerpt && <p className="task-card-description">{excerpt}</p>}
      <div className="card-properties" aria-label="任务属性">
        <span className={`property-control priority-chip priority-chip-${task.priority}`} title={`优先级：${PRIORITY_LABELS[task.priority]}`}>
          <PriorityIcon priority={task.priority} size={13} />
          <span>{PRIORITY_LABELS[task.priority]}</span>
        </span>
        {blocked && <span className="property-control task-blocked-chip" title={`阻塞于 ${task.blockedBy.map((item) => item.identifier).join(", ") || "未完成前置任务"}`}><span aria-hidden="true">⌑</span><span>阻塞中</span></span>}
        {task.threadId && <span className="task-thread-chip" title="已关联 Codex thread">⌁</span>}
      </div>
      {task.blockedBy.length > 0 && <div className="task-card-dependencies"><span className="task-card-dependency-lock" aria-hidden="true">⌑</span><span>阻塞于 {task.blockedBy.map((item) => item.identifier).join(", ")}</span></div>}
      {task.model && <div className="task-card-execution" title="在任务详情中修改模型与推理强度">{task.model}{task.reasoningEffort ? ` · ${task.reasoningEffort}` : ""}</div>}
      {processing && (
        <div className={`task-processing-row${task.runState === "running" ? " is-running" : " is-paused"}`}>
          {task.runState === "running" && <img className="task-processing-glyph" src={processingAnimation} alt="" aria-hidden="true" />}
          <span className="task-processing-label">{processingLabel}{task.runState === "running" && task.updatedAt ? ` · ${elapsedTime(task.updatedAt)}` : ""}</span>
          <span className="task-processing-spacer" aria-hidden="true" />
        </div>
      )}
    </article>
  );
}
