import { useEffect, useState, type DragEvent } from "react";
import type { Task, TaskStatus } from "../types";
import { PlusIcon, StatusIcon } from "./SemanticIcons";
import { TaskCard } from "./TaskCard";

export const STATUS_DETAILS: Record<TaskStatus, { label: string; tone: string }> = {
  todo: { label: "等待认领", tone: "todo" },
  in_progress: { label: "处理中", tone: "progress" },
  in_review: { label: "等你确认", tone: "review" },
  done: { label: "完成", tone: "done" },
};

export function BoardColumn({
  status,
  tasks,
  isDropTarget,
  draggedTaskId,
  onCreate,
  onEdit,
  onComplete,
  onDragStart,
  onDragEnd,
  onDragEnter,
  onDrop,
}: {
  status: TaskStatus;
  tasks: Task[];
  isDropTarget: boolean;
  draggedTaskId: string | null;
  onCreate: (status: TaskStatus) => void;
  onEdit: (task: Task) => void;
  onComplete: (task: Task) => void;
  onDragStart: (task: Task, height: number) => void;
  onDragEnd: () => void;
  onDragEnter: (status: TaskStatus) => void;
  onDrop: (status: TaskStatus, taskId: string, beforeTaskId: string | null) => void;
}) {
  const details = STATUS_DETAILS[status];
  const [dropBeforeTaskId, setDropBeforeTaskId] = useState<string | null | undefined>();
  const remainingTasks = tasks.filter((task) => task.id !== draggedTaskId);

  useEffect(() => {
    if (!isDropTarget || !draggedTaskId) setDropBeforeTaskId(undefined);
  }, [draggedTaskId, isDropTarget]);

  function findDropBefore(container: HTMLElement, clientY: number) {
    const cards = Array.from(container.querySelectorAll<HTMLElement>("[data-task-id]"))
      .filter((card) => card.dataset.taskId !== draggedTaskId);
    return cards.find((card) => clientY < card.getBoundingClientRect().top + card.offsetHeight / 2)?.dataset.taskId ?? null;
  }

  function handleDrop(event: DragEvent<HTMLElement>) {
    event.preventDefault();
    const taskId = event.dataTransfer.getData("application/x-taskboard-task") || event.dataTransfer.getData("text/plain");
    if (taskId) onDrop(status, taskId, findDropBefore(event.currentTarget, event.clientY));
    setDropBeforeTaskId(undefined);
  }

  return (
    <section
      className={`board-column status-${status}${isDropTarget ? " is-drop-target" : ""}`}
      aria-labelledby={`column-${status}`}
      onDragEnter={() => onDragEnter(status)}
      onDragOver={(event) => {
        event.preventDefault();
        event.dataTransfer.dropEffect = "move";
        onDragEnter(status);
        setDropBeforeTaskId(findDropBefore(event.currentTarget, event.clientY));
      }}
      onDragLeave={(event) => {
        if (!(event.relatedTarget instanceof Node) || !event.currentTarget.contains(event.relatedTarget)) setDropBeforeTaskId(undefined);
      }}
      onDrop={handleDrop}
    >
      <header className="column-header">
        <div className="column-heading">
          <span className={`column-status-icon status-icon-${details.tone}`}><StatusIcon status={status} color="var(--column-status-color)" size={14} /></span>
          <h2 id={`column-${status}`}>{details.label}{tasks.length > 0 ? ` ${tasks.length}` : ""}</h2>
        </div>
        <div className="column-actions">
          <button type="button" className="icon-button add-task-button" onClick={() => onCreate(status)} aria-label={`在${details.label}中新建任务`} title={`添加到${details.label}`}><PlusIcon color="var(--column-status-color)" size={12} /></button>
        </div>
      </header>
      <div className="column-list">
        {tasks.map((task) => <TaskCard key={task.id} task={task} isDragging={draggedTaskId === task.id} onEdit={onEdit} onComplete={onComplete} onDragStart={onDragStart} onDragEnd={onDragEnd} />)}
        {tasks.length === 0 && <div className="column-empty">{status === "todo" ? "暂无待认领任务" : status === "in_progress" ? "暂无处理中任务" : status === "in_review" ? "暂无待确认任务" : "完成的任务会出现在这里"}</div>}
        {isDropTarget && dropBeforeTaskId && remainingTasks.length === 0 && null}
      </div>
    </section>
  );
}
