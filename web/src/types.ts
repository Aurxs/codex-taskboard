import { t } from "./i18n";
export const TASK_STATUSES = ["todo", "in_progress", "in_review", "done"] as const;
export type TaskStatus = (typeof TASK_STATUSES)[number];
export type AnyTaskStatus = TaskStatus | "canceled";

export type ActorType = "user" | "agent";

export interface ActorIdentity {
  type: ActorType;
  id: string;
  name: string;
  avatarUrl: string | null;
}

export const TASK_PRIORITIES = ["urgent", "high", "medium", "low", "none", "draft"] as const;
export interface AttachmentInput { name: string; content: string }
export interface TaskAttachment { id: string; name: string; size: number; url: string }

export type TaskPriority = (typeof TASK_PRIORITIES)[number];

export const TASK_RUN_STATES = [
  "starting",
  "running",
  "waiting_quota",
  "waiting_approval",
  "waiting_input",
  "failed",
] as const;
export type TaskRunState = (typeof TASK_RUN_STATES)[number];

export interface Project {
  id: string;
  key: string;
  name: string;
  workspacePath: string;
  codexProjectId: string | null;
  automationEnabled: boolean;
  reviewRequired: boolean;
  quotaAutoResumeEnabled: boolean;
  version: number;
  createdAt: string;
  updatedAt: string;
}

export interface TaskSummary {
  id: string;
  identifier: string;
  title: string;
  status?: AnyTaskStatus;
}

export type InteractionKind = "command_approval" | "file_approval" | "permission_request" | "permissions" | "user_input" | string;
export type InteractionStatus = "pending" | "resolved" | "canceled" | "failed" | string;

export interface InteractionQuestion {
  id?: string;
  header?: string;
  question: string;
  options?: Array<{ label: string; description?: string }>;
  [key: string]: unknown;
}

export interface Interaction {
  id: string;
  taskId: string;
  kind: InteractionKind;
  status: InteractionStatus;
  version: number;
  title?: string;
  message?: string;
  command?: string;
  cwd?: string;
  permissionScope?: string[];
  questions?: InteractionQuestion[];
  payload?: Record<string, unknown>;
  createdAt?: string;
  resolvedAt?: string | null;
}

export interface ExecutionOptions {
  executionMode: "local" | "worktree";
  branch: string | null;
  model: string | null;
  reasoningEffort: string | null;
  kind?: "task" | "parallel_group";
  schedulingMode?: "exclusive" | "parallel";
  writeScopes?: string[];
  targetBranch?: string | null;
}

export interface TaskOperation {
  id: string;
  kind: string;
  state: string;
  payload: { threadId?: string; error?: unknown; proposal?: { tasks: ProposedTask[] }; [key: string]: unknown };
}

export interface ProposedTask { key: string; title: string; description: string; blockedByKeys: string[]; writeScopes: string[] }

export interface CodexModel {
  model: string;
  displayName: string;
  defaultReasoningEffort: string;
  supportedReasoningEfforts: { reasoningEffort: string; description?: string }[];
}

export interface Task extends ExecutionOptions {
  plan?: { hold: boolean; operationId?: string | null; state?: string | null; threadId?: string; text?: string | null; acceptedText?: string | null; error?: string | null };
  parentId?: string | null;
  groupPhase?: "preparing" | "submitted" | "pausing" | "paused" | null;
  mergeState?: "none" | "pending_review" | "queued" | "merging" | "blocked" | "merged";
  queued?: boolean;
  waitReason?: string | null;
  parallel?: { managed?: boolean; paused?: boolean; uncertainWorktree?: string; needsValidation?: boolean; nativeConflict?: boolean; [key: string]: unknown };
  progress?: { total: number; integrated: number; running: number; attention: number };
  children?: Task[];
  operations?: TaskOperation[];
  worktreePath: string | null;
  id: string;
  identifier: string;
  projectId: string;
  title: string;
  description: string;
  priority: TaskPriority;
  attachments?: TaskAttachment[];
  status: AnyTaskStatus;
  version: number;
  threadId: string | null;
  runState: TaskRunState | null;
  lastMessage: string | null;
  lastError: string | null;
  blockedBy: TaskSummary[];
  blocks: TaskSummary[];
  ready: boolean;
  completedAt?: string | null;
  createdAt: string;
  updatedAt: string;
  interactions?: Interaction[];
  activity?: ActivityItem[];
  activityError?: string;
  runs?: TaskRun[];
}

export interface TaskRun {
  runState?: string;
  lastOutputSummary?: string | null;
  id?: string;
  taskId?: string;
  threadId?: string | null;
  turnId?: string | null;
  state?: string | null;
  phase?: string | null;
  error?: unknown;
  summary?: string | null;
  createdAt?: string;
  updatedAt?: string;
}

export interface ActivityItem {
  status?: string;
  id?: string;
  kind: string;
  message?: string;
  summary?: string;
  detail?: string;
  createdAt?: string;
  data?: Record<string, unknown>;
}

export interface ApiErrorShape {
  code?: string;
  message?: string;
  detail?: unknown;
}

export interface EventEnvelope {
  type: string;
  projectId?: string;
  taskId?: string;
  payload?: unknown;
}

/** Context sent by the native Codex renderer to the embedded Taskboard. */
export interface HostContext {
  user?: ActorIdentity;
  language?: string;
  workspacePath?: string;
  threadId?: string;
  projectId?: string;
  theme?: "light" | "dark";
  projects?: Array<{
    id: string;
    name: string;
    projectKind?: "local" | "remote";
    workspacePath?: string;
    hostId?: string;
  }>;
  titlebarLeftInset?: number;
  titlebarRightInset?: number;
  sidebarCollapsed?: boolean;
  threadRunning?: boolean;
  threadTodoProgress?: {
    completed: number;
    total: number;
  };
}

export const PRIORITY_LABELS: Record<TaskPriority, string> = {
  get urgent() { return t("紧急"); },
  get high() { return t("高"); },
  get medium() { return t("中"); },
  get low() { return t("低"); },
  get none() { return t("无优先级"); },
  get draft() { return t("草稿"); },
};

export const STATUS_LABELS: Record<TaskStatus, string> = {
  get todo() { return t("待认领"); },
  get in_progress() { return t("处理中"); },
  get in_review() { return t("等你确认"); },
  get done() { return t("已完成"); },
};

export const RUN_STATE_LABELS: Record<TaskRunState, string> = {
  get starting() { return t("启动中"); },
  get running() { return t("执行中"); },
  get waiting_quota() { return t("等待额度"); },
  get waiting_approval() { return t("等待批准"); },
  get waiting_input() { return t("等待回答"); },
  get failed() { return t("执行失败"); },
};

export const RUN_STATE_TONES: Record<TaskRunState, string> = {
  starting: "neutral",
  running: "blue",
  waiting_quota: "amber",
  waiting_approval: "purple",
  waiting_input: "purple",
  failed: "red",
};
