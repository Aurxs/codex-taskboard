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

export const TASK_PRIORITIES = ["urgent", "high", "medium", "low", "none"] as const;
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
  model: string | null;
  reasoningEffort: string | null;
}

export interface CodexModel {
  model: string;
  displayName: string;
  defaultReasoningEffort: string;
  supportedReasoningEfforts: { reasoningEffort: string; description?: string }[];
}

export interface Task extends ExecutionOptions {
  id: string;
  identifier: string;
  projectId: string;
  title: string;
  description: string;
  priority: TaskPriority;
  status: AnyTaskStatus;
  version: number;
  threadId: string | null;
  runState: TaskRunState | null;
  lastMessage: string | null;
  lastError: string | null;
  blockedBy: TaskSummary[];
  blocks: TaskSummary[];
  ready: boolean;
  createdAt: string;
  updatedAt: string;
  interactions?: Interaction[];
  activity?: ActivityItem[];
  runs?: TaskRun[];
}

export interface TaskRun {
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
  sidebarCollapsed?: boolean;
  threadRunning?: boolean;
  threadTodoProgress?: {
    completed: number;
    total: number;
  };
}

export const PRIORITY_LABELS: Record<TaskPriority, string> = {
  urgent: "紧急",
  high: "高",
  medium: "中",
  low: "低",
  none: "无优先级",
};

export const STATUS_LABELS: Record<TaskStatus, string> = {
  todo: "待认领",
  in_progress: "处理中",
  in_review: "等你确认",
  done: "已完成",
};

export const RUN_STATE_LABELS: Record<TaskRunState, string> = {
  starting: "启动中",
  running: "执行中",
  waiting_quota: "等待额度",
  waiting_approval: "等待批准",
  waiting_input: "等待回答",
  failed: "执行失败",
};

export const RUN_STATE_TONES: Record<TaskRunState, string> = {
  starting: "neutral",
  running: "blue",
  waiting_quota: "amber",
  waiting_approval: "purple",
  waiting_input: "purple",
  failed: "red",
};
