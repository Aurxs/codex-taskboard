import { t, getLocale, localizeError } from "./i18n";
import type {
  ApiErrorShape,
  AttachmentInput,
  CodexModel,
  EventEnvelope,
  Interaction,
  HostContext,
  Project,
  Task,
  TaskPriority,
  TaskSummary,
} from "./types";

export class ApiError extends Error {
  readonly status: number;
  readonly code?: string;
  readonly body: ApiErrorShape | null;

  constructor(status: number, body: ApiErrorShape | null, fallback?: string) {
    const normalized = normalizeApiErrorBody(body);
    super(localizeError(normalized?.message ?? fallback ?? t("请求失败 ({0})", status)));
    this.name = "ApiError";
    this.status = status;
    this.code = normalized?.code;
    this.body = normalized;
  }
}

/**
 * FastAPI wraps Taskboard errors in `{ error: { code, message, details } }`.
 * Keep accepting the original top-level shape so older local servers remain
 * usable, but expose one consistent error to the UI.
 */
function normalizeApiErrorBody(value: ApiErrorShape | null): ApiErrorShape | null {
  if (!value || typeof value !== "object") return null;
  const nested = (value as Record<string, unknown>).error;
  if (nested && typeof nested === "object" && !Array.isArray(nested)) {
    const error = nested as Record<string, unknown>;
    return {
      code: typeof error.code === "string" ? error.code : undefined,
      message: typeof error.message === "string" ? error.message : undefined,
      detail: error.details ?? error.detail ?? value.detail,
    };
  }
  return value;
}

function camelKey(key: string): string {
  return key.replace(/[-_](.)/g, (_, character: string) => character.toUpperCase());
}

function camelize(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(camelize);
  if (value && typeof value === "object") {
    return Object.fromEntries(Object.entries(value).map(([key, item]) => [camelKey(key), camelize(item)]));
  }
  return value;
}

function asArray<T>(value: unknown, key: string): T[] {
  if (Array.isArray(value)) return value as T[];
  if (value && typeof value === "object" && Array.isArray((value as Record<string, unknown>)[key])) {
    return (value as Record<string, unknown>)[key] as T[];
  }
  return [];
}

function normalizeSummary(value: unknown): TaskSummary | null {
  if (!value || typeof value !== "object") return null;
  const item = value as Record<string, unknown>;
  const id = String(item.id ?? item.taskId ?? "");
  if (!id) return null;
  return {
    id,
    identifier: String(item.identifier ?? item.key ?? id),
    title: String(item.title ?? ""),
    status: typeof item.status === "string" ? item.status as TaskSummary["status"] : undefined,
  };
}

function normalizeDisplayError(value: unknown): string | null {
  if (value == null) return null;
  if (typeof value === "string") return value;
  if (typeof value === "object") {
    const item = value as Record<string, unknown>;
    const nested = item.error && typeof item.error === "object" && !Array.isArray(item.error)
      ? item.error as Record<string, unknown>
      : item;
    if (typeof nested.message === "string" && nested.message.trim()) return nested.message;
    if (typeof nested.code === "string" && nested.code.trim()) return nested.code;
    try {
      return JSON.stringify(value);
    } catch {
      return String(value);
    }
  }
  return String(value);
}

function normalizeTask(value: unknown): Task {
  const item = camelize(value) as Record<string, unknown>;
  const blockedBy = asArray<unknown>(item.blockedBy, "blockedBy")
    .map(normalizeSummary)
    .filter((summary): summary is TaskSummary => Boolean(summary));
  const blocks = asArray<unknown>(item.blocks, "blocks")
    .map(normalizeSummary)
    .filter((summary): summary is TaskSummary => Boolean(summary));
  return {
    id: String(item.id ?? ""),
    identifier: String(item.identifier ?? item.key ?? item.id ?? ""),
    projectId: String(item.projectId ?? ""),
    title: String(item.title ?? ""),
    description: String(item.description ?? ""),
    plan: item.plan as Task["plan"],
    attachments: (item.attachments ?? []) as Task["attachments"],
    kind: item.kind === "parallel_group" ? "parallel_group" : "task",
    schedulingMode: item.schedulingMode === "parallel" ? "parallel" : "exclusive",
    parentId: item.parentId == null ? null : String(item.parentId),
    writeScopes: Array.isArray(item.writeScopes) ? item.writeScopes as string[] : [],
    targetBranch: item.targetBranch == null ? null : String(item.targetBranch),
    groupPhase: item.groupPhase as Task["groupPhase"],
    mergeState: (item.mergeState ?? "none") as Task["mergeState"],
    parallel: (item.parallel ?? {}) as Task["parallel"],
    waitReason: item.waitReason == null ? null : String(item.waitReason),
    queued: Boolean(item.queued),
    progress: item.progress as Task["progress"],
    children: Array.isArray(item.children) ? item.children.map(normalizeTask) : undefined,
    operations: Array.isArray(item.operations) ? item.operations as Task["operations"] : undefined,
    model: item.model == null ? null : String(item.model),
    executionMode: item.executionMode === "worktree" ? "worktree" : "local",
    branch: item.branch == null ? null : String(item.branch),
    worktreePath: item.worktreePath == null ? null : String(item.worktreePath),
    reasoningEffort: item.reasoningEffort == null ? null : String(item.reasoningEffort),
    priority: (item.priority ?? "none") as TaskPriority,
    status: (item.status ?? "todo") as Task["status"],
    version: Number(item.version ?? 0),
    threadId: item.threadId == null ? null : String(item.threadId),
    runState: item.runState == null ? null : String(item.runState) as Task["runState"],
    lastMessage: item.lastMessage == null ? null : String(item.lastMessage),
    lastError: normalizeDisplayError(item.lastError),
    blockedBy,
    blocks,
    ready: Boolean(item.ready ?? blockedBy.every((task) => task.status === "done")),
    completedAt: item.completedAt == null ? null : String(item.completedAt),
    createdAt: String(item.createdAt ?? new Date().toISOString()),
    updatedAt: String(item.updatedAt ?? new Date().toISOString()),
    interactions: Array.isArray(item.interactions) ? item.interactions as Interaction[] : undefined,
    activityError: typeof item.activityError === "string" ? item.activityError : undefined,
    activity: Array.isArray(item.activity ?? item.runEvents ?? item.events)
      ? (item.activity ?? item.runEvents ?? item.events) as Task["activity"]
      : undefined,
    runs: Array.isArray(item.runs) ? item.runs as Task["runs"] : undefined,
  };
}

function normalizeProject(value: unknown): Project {
  const item = camelize(value) as Record<string, unknown>;
  return {
    id: String(item.id ?? ""),
    key: String(item.key ?? ""),
    name: String(item.name ?? item.key ?? t("未命名项目")),
    workspacePath: String(item.workspacePath ?? item.path ?? ""),
    codexProjectId: item.codexProjectId == null ? null : String(item.codexProjectId),
    automationEnabled: Boolean(item.automationEnabled ?? false),
    reviewRequired: item.reviewRequired !== false,
    quotaAutoResumeEnabled: item.quotaAutoResumeEnabled !== false,
    version: Number(item.version ?? 0),
    createdAt: String(item.createdAt ?? new Date().toISOString()),
    updatedAt: String(item.updatedAt ?? new Date().toISOString()),
  };
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    ...init,
    headers: {
      Accept: "application/json",
      "Accept-Language": getLocale(),
      ...(init?.body ? { "Content-Type": "application/json" } : {}),
      ...init?.headers,
    },
  });
  const text = await response.text();
  let body: unknown = null;
  if (text) {
    try {
      body = JSON.parse(text);
    } catch {
      body = { message: text };
    }
  }
  if (!response.ok) {
    throw new ApiError(response.status, body && typeof body === "object" ? body as ApiErrorShape : null);
  }
  return body as T;
}

export async function listProjects(): Promise<Project[]> {
  const value = await request<unknown>("/api/projects");
  return asArray<unknown>(value, "projects").map(normalizeProject);
}

export async function createProject(input: {
  key: string;
  name: string;
  workspacePath: string;
  codexProjectId?: string | null;
}): Promise<Project> {
  return normalizeProject(await request<unknown>("/api/projects", {
    method: "POST",
    body: JSON.stringify(input),
  }));
}

export async function updateProject(
  id: string,
  version: number,
  changes: Partial<Pick<Project, "key" | "name" | "workspacePath" | "automationEnabled" | "reviewRequired" | "quotaAutoResumeEnabled">>,
): Promise<Project> {
  return normalizeProject(await request<unknown>(`/api/projects/${encodeURIComponent(id)}`, {
    method: "PATCH",
    body: JSON.stringify({ ...changes, version }),
  }));
}

export async function deleteProject(id: string, version: number): Promise<void> {
  await request<unknown>(`/api/projects/${encodeURIComponent(id)}`, {
    method: "DELETE",
    body: JSON.stringify({ version }),
  });
}

export async function listTasks(projectId: string, includeCanceled = false): Promise<Task[]> {
  const query = includeCanceled ? "?includeCanceled=true" : "";
  const value = await request<unknown>(`/api/projects/${encodeURIComponent(projectId)}/tasks${query}`);
  return asArray<unknown>(value, "tasks").map(normalizeTask);
}

export async function getTask(id: string): Promise<Task> {
  return normalizeTask(await request<unknown>(`/api/tasks/${encodeURIComponent(id)}`));
}

export async function createTask(
  projectId: string,
  input: { title: string; description: string; priority: TaskPriority; blockedByIds?: string[]; attachments?: AttachmentInput[]; model?: string | null; reasoningEffort?: string | null; executionMode?: "local" | "worktree"; branch?: string | null; kind?: "task" | "parallel_group"; schedulingMode?: "exclusive" | "parallel"; writeScopes?: string[]; targetBranch?: string | null; requestId?: string },
): Promise<Task> {
  return normalizeTask(await request<unknown>(`/api/projects/${encodeURIComponent(projectId)}/tasks`, {
    method: "POST",
    body: JSON.stringify(input),
  }));
}

export async function updateTask(
  id: string,
  version: number,
  changes: (Partial<Pick<Task, "title" | "description" | "priority" | "model" | "reasoningEffort" | "executionMode" | "branch" | "kind" | "schedulingMode" | "writeScopes" | "targetBranch">> & { attachments?: import("./types").AttachmentInput[]; removeAttachmentIds?: string[] }),
): Promise<Task> {
  return normalizeTask(await request<unknown>(`/api/tasks/${encodeURIComponent(id)}`, {
    method: "PATCH",
    body: JSON.stringify({ ...changes, version }),
  }));
}

export async function replaceDependencies(id: string, version: number, blockedByIds: string[]): Promise<Task> {
  return normalizeTask(await request<unknown>(`/api/tasks/${encodeURIComponent(id)}/dependencies`, {
    method: "PUT",
    body: JSON.stringify({ blockedByIds, version }),
  }));
}

export type TaskAction =
  | "plan_start" | "plan_continue" | "plan_accept" | "plan_cancel"
  | "run"
  | "follow_up"
  | "interrupt_requeue"
  | "retry"
  | "submit_review_feedback"
  | "complete"
  | "cancel"
  | "pause" | "resume" | "group_submit" | "group_pause" | "group_resume" | "merge_retry" | "attach_worktree" | "attach_thread";

export async function taskAction(
  id: string,
  action: TaskAction,
  version: number,
  feedback?: string,
  targetStatus?: "in_review" | "done",
): Promise<Task> {
  return normalizeTask(await request<unknown>(`/api/tasks/${encodeURIComponent(id)}/actions`, {
    method: "POST",
    body: JSON.stringify({ action, version, requestId: crypto.randomUUID(), ...(feedback ? { feedback } : {}), ...(targetStatus ? { targetStatus } : {}) }),
  }));
}

export async function resolveInteraction(id: string, version: number, response: unknown): Promise<Interaction> {
  return await request<Interaction>(`/api/interactions/${encodeURIComponent(id)}/resolve`, {
    method: "POST",
    body: JSON.stringify({ version, response, canceled: !!response && typeof response === "object" && "decision" in response && response.decision === "cancel" }),
  });
}

export function createEventStream(onEvent: (event: EventEnvelope) => void, onError?: () => void, onOpen?: () => void): () => void {
  const stream = new EventSource("/api/events");
  const handleMessage = (event: MessageEvent<string>) => {
    try {
      onEvent(JSON.parse(event.data) as EventEnvelope);
    } catch {
      // Keep the stream alive when a server emits a non-JSON heartbeat.
    }
  };
  stream.onopen = () => onOpen?.();
  stream.onmessage = handleMessage;
  stream.onerror = () => onError?.();
  return () => stream.close();
}

export async function listCodexProjects(): Promise<Array<{
  id?: string;
  name: string;
  workspacePath: string;
}>> {
  const value = await request<unknown>("/api/codex/projects");
  return asArray<unknown>(value, "projects").map((item) => {
    const normalized = camelize(item) as Record<string, unknown>;
    return {
      id: normalized.id == null ? undefined : String(normalized.id),
      name: String(normalized.name ?? normalized.key ?? t("未命名项目")),
      workspacePath: String(normalized.workspacePath ?? normalized.path ?? ""),
    };
  });
}

/**
 * Reconcile the projects currently visible in Codex with the local database.
 * The renderer is the source of truth for this request; the server keeps
 * Taskboard preferences and task history for each durable Codex project id.
 */
export async function syncCodexProjects(context: HostContext): Promise<Project[]> {
  const value = await request<unknown>("/api/codex/projects/sync", {
    method: "POST",
    body: JSON.stringify({
      projects: context.projects ?? [],
      selectedProjectId: context.projectId ?? null,
    }),
  });
  const projects = asArray<unknown>(value, "projects");
  if (projects.length > 0) return projects.map(normalizeProject);
  if (value && typeof value === "object" && "project" in value) {
    const project = (value as Record<string, unknown>).project;
    return project ? [normalizeProject(project)] : [];
  }
  return Array.isArray(value) ? value.map(normalizeProject) : [];
}

/** Ask the native macOS sidecar to choose a directory.  A 409 is the
 * expected response when the user presses Cancel, so callers can treat it as
 * a no-op while other errors remain visible. */
export async function pickDirectory(): Promise<string | null> {
  try {
    const value = await request<unknown>("/api/system/pick-directory", { method: "POST" });
    if (!value || typeof value !== "object") return null;
    const workspacePath = (value as Record<string, unknown>).workspacePath;
    return typeof workspacePath === "string" && workspacePath.trim() ? workspacePath : null;
  } catch (cause) {
    if (cause instanceof ApiError && cause.status === 409) return null;
    throw cause;
  }
}

export async function listModels(): Promise<CodexModel[]> {
  return asArray<CodexModel>(await request("/api/codex/models"), "models");
}

export function previewAttachment(id: string) {
  return request<{ kind: "image" | "markdown" | "text" | "external"; content: string }>(`/api/attachments/${encodeURIComponent(id)}/preview`);
}

export function openAttachment(id: string) {
  return request(`/api/attachments/${encodeURIComponent(id)}/open`, { method: "POST" });
}

export async function getGitContext(projectId: string): Promise<{ isGit: boolean; currentBranch: string | null; branches: string[] }> {
  return request(`/api/projects/${encodeURIComponent(projectId)}/git-context`);
}

export async function createChild(parent: Task, input: Parameters<typeof createTask>[1]): Promise<Task> {
  return normalizeTask(await request(`/api/tasks/${encodeURIComponent(parent.id)}/children`, {
    method: "POST", body: JSON.stringify({ ...input, version: parent.version }),
  }));
}

export async function generatePlan(parent: Task, requestId: string): Promise<import("./types").TaskOperation> {
  return request(`/api/tasks/${encodeURIComponent(parent.id)}/plans`, {
    method: "POST", body: JSON.stringify({ version: parent.version, requestId }),
  });
}

export async function confirmPlan(parent: Task, operationId: string, tasks: import("./types").ProposedTask[]): Promise<Task> {
  return normalizeTask(await request(`/api/tasks/${encodeURIComponent(parent.id)}/plans/confirm`, {
    method: "POST", body: JSON.stringify({ version: parent.version, operationId, proposal: { tasks } }),
  }));
}

export async function deleteTask(task: Task): Promise<void> {
  await request(`/api/tasks/${encodeURIComponent(task.id)}`, { method: "DELETE", body: JSON.stringify({ version: task.version }) });
}
