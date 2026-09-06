import type { ActivityItem } from "../types";
import { t } from "../i18n";
import { LinearIcon } from "./LinearIcon";
import { activityPresentation, activityText } from "./activityPresentation";
import "./ActivityTool.css";

export function ActivityToolIcon({ item }: { item: ActivityItem }) {
  const { category } = activityPresentation(item);
  if (category === "command" || category === "files") return <LinearIcon name={category === "command" ? "terminal" : "fileChange"} />;
  return <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
    {category === "read" ? <path d="M10 5C7 3 4 3 2 4v12c3-1 5-1 8 1m0-12c3-2 6-2 8-1v12c-3-1-5-1-8 1V5Z" />
      : category === "browser" ? <><rect x="2" y="3" width="16" height="14" rx="2" /><path d="M2 7h16M5 5h.01M8 5h.01" /></>
        : <path d="M12 2a5 5 0 0 0-6 6l-4 6a2 2 0 0 0 3 3l6-5a5 5 0 0 0 6-6l-3 3-3-1-1-3 2-3Z" />}
  </svg>;
}

function DetailBlock({ label, value }: { label: string; value: unknown }) {
  if (value == null || value === "") return null;
  return <section className="activity-detail-block"><div>{label}</div><pre>{activityText(value)}</pre></section>;
}

export function ActivityTool({ item }: { item: ActivityItem }) {
  const view = activityPresentation(item);
  const labels = { command: t("已运行"), read: t("已读取"), files: t("已修改"), browser: t("浏览器调用"), tool: t("已调用") };
  const label = view.running ? t("进行中") : view.failed ? t("调用失败") : labels[view.category];
  return <details className="activity-tool-details" data-failed={view.failed || undefined}>
    <summary title={`${label} ${view.summary}`}>
      <span className="activity-tool-title"><span>{label} </span><span className={view.category === "read" ? "activity-read-path" : undefined}>{view.summary}</span></span>
      <LinearIcon name="chevronDown" className="activity-tool-chevron" />
    </summary>
    <div className="activity-tool-panel" tabIndex={0} role="region" aria-label={`${label} ${view.summary}`}>
      <DetailBlock label="Shell" value={view.command ? `$ ${view.command}` : undefined} />
      {view.changes.map((change, index) => <DetailBlock key={index} label={activityText(change.path) || t("文件修改")} value={change.diff ?? change} />)}
      <DetailBlock label={t("参数")} value={view.args} />
      <DetailBlock label={t("输出")} value={view.output} />
      <DetailBlock label={t("执行错误")} value={view.error} />
      {view.exitCode != null && <div className="activity-exit-code">{t("退出码：{0}", activityText(view.exitCode))}</div>}
      {!view.command && !view.changes.length && view.args == null && view.output == null && view.error == null && <DetailBlock label={t("详情")} value={item.data ?? (view.description || t("暂无输出"))} />}
    </div>
  </details>;
}
