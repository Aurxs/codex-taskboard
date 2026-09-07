import { planStatus } from "./TaskPlan";
import { t, localizeError } from "../i18n";
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
  const blocked = task.status === "todo" && task.blockedBy.some((item) => item.status !== "done");
  const pendingInteraction = task.interactions?.some((interaction) => interaction.status === "pending");
  const planning = !!task.plan?.hold;
  const processing = task.status === "in_progress" || planning;
  const running = planning ? ["pending", "starting", "agent_running"].includes(task.plan?.state ?? "") && !pendingInteraction : task.runState === "running";
  const excerpt = markdownExcerpt(task.description);
  const processingLabel = planning ? planStatus(task) : task.runState === "waiting_quota"
    ? t("等待额度")
    : task.runState === "waiting_approval"
      ? t("等待批准")
      : task.runState === "waiting_input"
        ? t("等待回答")
        : task.runState === "failed"
          ? t("执行失败")
          : task.runState === "starting"
            ? t("启动中")
            : t("执行中");
  return (
    <article
      className={`task-card task-card-main status-${task.status}${processing ? " is-processing-card" : ""}${processing && running ? " is-running-card" : ""}${isDragging ? " is-dragging" : ""}${pendingInteraction ? " is-unread" : ""}`}
      data-task-id={task.id}
      draggable={!planning && task.status !== "done" && task.status !== "canceled"}
      aria-labelledby={`task-${task.id}-title`}
      onDragStart={(event) => {
        event.dataTransfer.effectAllowed = "move";
        event.dataTransfer.setData("text/plain", task.id);
        event.dataTransfer.setData("application/x-taskboard-task", task.id);
        onDragStart(task, event.currentTarget.offsetHeight);
      }}
      onDragEnd={onDragEnd}
    >
      <button className="task-card-open" type="button" aria-label={t("打开 {0}: {1}", task.identifier, task.title)} onClick={() => onEdit(task)} />
      <div className="card-topline">
        <span className="card-reference"><span className="task-identifier">ID: {task.identifier}</span></span>
        {pendingInteraction && <span className="task-unread-dot" aria-label={t("有待处理交互")} />}
        {task.status === "in_review" && <button className="task-card-complete" type="button" onClick={(event) => { event.stopPropagation(); onComplete(task); }}><img src={completeIcon} alt="" /><span>{task.parallel?.managed ? t("确认并合入") : t("完成")}</span></button>}
      </div>
      <h3 id={`task-${task.id}-title`}>{task.title}</h3>
      {excerpt && <p className="task-card-description">{excerpt}</p>}
      <div className="card-properties" aria-label={t("任务属性")}>
        <span className={`property-control priority-chip priority-chip-${task.priority}`} title={t("优先级：{0}", PRIORITY_LABELS[task.priority])}>
          <PriorityIcon priority={task.priority} size={13} />
          <span>{PRIORITY_LABELS[task.priority]}</span>
        </span>
        {blocked && <span className="property-control task-blocked-chip" title={t("阻塞于 {0}", task.blockedBy.map((item) => item.identifier).join(", ") || t("未完成前置任务"))}><span aria-hidden="true">⌑</span><span>{t("阻塞中")}</span></span>}
        {task.threadId && <span className="task-thread-chip" title={t("已关联 Codex thread")}>⌁</span>}
      </div>
      {task.blockedBy.length > 0 && <div className="task-card-dependencies"><span className="task-card-dependency-lock" aria-hidden="true">⌑</span><span>{t("阻塞于")} {task.blockedBy.map((item) => item.identifier).join(", ")}</span></div>}
      {task.kind === "parallel_group" && <div className="task-card-execution">{t("并行任务组")} · {t("已集成 {0} / {1}", task.progress?.integrated ?? 0, task.progress?.total ?? 0)}{Boolean(task.progress?.running) && <> · {t("运行中 {0}", task.progress?.running ?? 0)}</>}{Boolean(task.progress?.attention) && <> · {t("需处理 {0}", task.progress?.attention ?? 0)}</>}</div>}
      {task.schedulingMode === "parallel" && <span className="task-parallel-badge">{t("允许并行")}</span>}
      {task.waitReason && <div className="task-card-execution">{localizeError(task.waitReason)}</div>}
      {processing && (
        <div className={`task-processing-row${running ? " is-running" : " is-paused"}`}>
          {running && <img className="task-processing-glyph" src={processingAnimation} alt="" aria-hidden="true" />}
          <span className="task-processing-label">{processingLabel}{running && task.updatedAt ? ` · ${elapsedTime(task.updatedAt)}` : ""}</span>
          <span className="task-processing-spacer" aria-hidden="true" />
        </div>
      )}
    </article>
  );
}
