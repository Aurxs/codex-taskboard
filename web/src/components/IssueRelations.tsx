import { t } from "../i18n";
import type { Task, TaskSummary } from "../types";
import { DependencyPicker } from "./DependencyPicker";
import { BlockingRelationIcon } from "./SemanticIcons";

export function IssueRelations({
  task,
  candidates,
  onChange,
}: {
  task: Task;
  candidates: Task[];
  onChange: (ids: string[]) => void;
}) {
  const candidateTasks = candidates.filter((candidate) => candidate.id !== task.id && candidate.status !== "canceled");
  return (
    <div className="issue-relation-sidebar">
      <h2>{t("依赖关系")}</h2>
      <DependencyPicker candidates={candidateTasks} value={task.blockedBy.map(item => item.id)} onChange={onChange} />
      <div className="issue-relation-group is-blocks">
        <header><span><BlockingRelationIcon type="blocks" size={14} />{t("阻塞了")}</span><span>{task.blocks.length}</span></header>
        <div className="issue-sub-issue-list">
          {task.blocks.map((item: TaskSummary) => <div className="issue-relation-row" key={item.id}><span className="issue-relation-target"><span className="issue-relation-id">{item.identifier}</span><span className="issue-relation-title">{item.title}</span></span></div>)}
          {task.blocks.length === 0 && <p className="issue-relation-empty">{t("没有后继任务。")}</p>}
        </div>
      </div>
    </div>
  );
}
