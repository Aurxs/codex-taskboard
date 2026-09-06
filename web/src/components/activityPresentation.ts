import type { ActivityItem } from "../types";

const record = (value: unknown): Record<string, unknown> => value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : {};
const string = (value: unknown) => typeof value === "string" ? value : "";
export const activityText = (value: unknown): string => value == null ? "" : typeof value === "string" ? value : JSON.stringify(value, null, 2);

// App-server command actions contain a more useful description than the shell executable.
export function activityPresentation(item: ActivityItem) {
  const data = item.data ?? {};
  let args = record(data.arguments ?? data.input);
  if (typeof data.arguments === "string") {
    try { args = record(JSON.parse(data.arguments)); } catch { /* Preserve raw arguments in the detail panel. */ }
  }
  const command = string(data.command) || string(args.cmd) || string(args.command);
  const description = item.message || item.summary || item.detail || "";
  const tool = string(data.tool) || string(data.name) || string(data.toolName);
  const server = string(data.server);
  const identity = `${item.kind} ${server} ${tool}`.toLowerCase();
  const actions = Array.isArray(data.commandActions) ? data.commandActions.map(record) : [];
  const reads = actions.filter(action => action.type === "read");
  const changes = Array.isArray(data.changes) ? data.changes.map(record) : [];
  const path = string(args.path) || string(args.file_path) || string(data.path);
  let category: "command" | "read" | "files" | "browser" | "tool" = "tool";
  let summary = [server, tool].filter(Boolean).join(" · ") || description || item.kind;
  if (item.kind === "commandExecution") {
    category = "command";
    summary = command || description;
    if (reads.length && reads.length === actions.length) {
      category = "read";
      summary = reads.map(action => string(action.path) || string(action.name)).filter(Boolean).join(", ") || summary;
    }
  } else if (item.kind === "fileChange") {
    category = "files";
    summary = changes.map(change => string(change.path)).filter(Boolean).join(", ") || description;
  } else if (/playwright|browser/.test(identity)) {
    category = "browser";
    summary = [tool || "Playwright", string(args.url)].filter(Boolean).join(" · ");
  } else if (/read_?file|fileread|readfile/.test(identity)) {
    category = "read";
    summary = path || summary;
  }
  return { category, summary, command, description, changes, args: data.arguments ?? data.input,
    output: data.aggregatedOutput ?? data.output ?? data.result ?? data.content,
    error: data.error,
    failed: item.status === "failed" || data.status === "failed" || data.success === false || data.error != null || (typeof data.exitCode === "number" && data.exitCode !== 0),
    running: item.status === "running" || item.status === "inProgress" || data.status === "inProgress",
    exitCode: data.exitCode,
  };
}
