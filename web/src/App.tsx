import { t, useLocale, setHostLanguage, localizeError } from "./i18n";
import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type CSSProperties,
  type DragEvent,
} from "react";
import {
  ApiError,
  createEventStream,
  createTask,
  getTask,
  listTasks,
  replaceDependencies,
  resolveInteraction,
  syncCodexProjects,
  taskAction,
  updateProject,
  updateTask,
  type TaskAction,
} from "./api";
import {
  installEmbeddedExternalLinkHandler,
  postEmbeddedHostMessage,
  setEmbeddedFrameChallenge,
} from "./embeddedHost.mjs";
import {
  PRIORITY_LABELS,
  STATUS_LABELS,
  TASK_PRIORITIES,
  type EventEnvelope,
  type HostContext,
  type Interaction,
  type Project,
  type Task,
  type TaskPriority,
  type TaskStatus,
} from "./types";
import { BoardColumn } from "./components/BoardColumn";
import { LinearIcon } from "./components/LinearIcon";
import { TaskDetail } from "./components/TaskDetail";
import { TaskEditor } from "./components/TaskEditor";
import { TaskCard } from "./components/TaskCard";
import { TaskboardIcon } from "./components/TaskboardIcon";

const COLUMN_ORDER: TaskStatus[] = ["todo", "in_progress", "in_review", "done"];

type Notice = { id: number; tone: "error" | "success" | "info"; message: string };

function compactError(error: unknown) {
  if (error instanceof ApiError || error instanceof Error) return localizeError(error.message);
  return typeof error === "string" ? localizeError(error) : t("请求失败，请重试。");
}

function isCodexEmbedded() {
  try {
    return new URL(document.baseURI || window.location.href).searchParams.get("host") === "codex";
  } catch {
    return false;
  }
}

function ProjectSwitcher({
  projects,
  selected,
  onSelect,
}: {
  projects: Project[];
  selected: Project | null;
  onSelect: (project: Project) => void;
}) {
  const [open, setOpen] = useState(false);
  const [needle, setNeedle] = useState("");
  const menuRef = useRef<HTMLDivElement>(null);
  const filtered = useMemo(() => {
    const value = needle.trim().toLocaleLowerCase();
    return value ? projects.filter((project) => `${project.name} ${project.key}`.toLocaleLowerCase().includes(value)) : projects;
  }, [needle, projects]);

  useEffect(() => {
    if (!open) return;
    const close = (event: PointerEvent) => {
      if (!menuRef.current?.contains(event.target as Node)) setOpen(false);
    };
    const escape = (event: KeyboardEvent) => { if (event.key === "Escape") setOpen(false); };
    document.addEventListener("pointerdown", close);
    document.addEventListener("keydown", escape);
    return () => {
      document.removeEventListener("pointerdown", close);
      document.removeEventListener("keydown", escape);
    };
  }, [open]);

  return (
    <div className="header-project-switcher" ref={menuRef} data-project-switcher>
      <button className="header-project-button" type="button" aria-label={t("切换项目")} aria-haspopup="menu" aria-expanded={open} onClick={() => { setNeedle(""); setOpen((current) => !current); }}>
        <span className="project-name">{selected?.name ?? t("等待 Codex 项目")}</span>
        <TaskboardIcon className="project-switcher-chevron" name="dropdown" />
      </button>
      {open && (
        <div className="header-project-menu" role="menu" aria-label={t("项目")}>
          <span>{t("切换项目")}</span>
          <div className="project-menu-search">
            <TaskboardIcon name="search" />
            <label className="sr-only" htmlFor="project-menu-search-input">{t("按名称筛选项目")}</label>
            <input id="project-menu-search-input" type="search" autoFocus value={needle} onChange={(event) => setNeedle(event.target.value)} placeholder={t("筛选项目…")} />
            {needle && <button className="search-clear" type="button" aria-label={t("清除项目筛选")} onClick={() => setNeedle("")}><LinearIcon name="close" /></button>}
          </div>
          <div className="project-menu-list">
            {filtered.map((project) => (
              <button type="button" role="menuitemradio" aria-checked={project.id === selected?.id} key={project.id} onClick={() => { onSelect(project); setOpen(false); }}>
                <TaskboardIcon className="project-avatar" name="projectFolder" />
                <span>{project.name}</span>
                {project.id === selected?.id && <span className="project-menu-check"><LinearIcon name="check" /></span>}
              </button>
            ))}
            {filtered.length === 0 && <div className="project-menu-empty">{t("没有匹配项目")}</div>}
          </div>
        </div>
      )}
    </div>
  );
}

function Switch({ checked, label, description, onChange }: { checked: boolean; label: string; description: string; onChange: (value: boolean) => void }) {
  return (
    <div className="project-automation-switch">
      <span><strong>{label}</strong><small>{description}</small></span>
      <button className={`board-setting-switch${checked ? " is-on" : ""}`} type="button" role="switch" aria-label={label} aria-checked={checked} onClick={() => onChange(!checked)}><span aria-hidden="true" /></button>
    </div>
  );
}

function ProjectSettingsMenu({
  project,
  onChange,
}: {
  project: Project;
  onChange: (field: "automationEnabled" | "reviewRequired" | "quotaAutoResumeEnabled", value: boolean) => void;
}) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const close = (event: PointerEvent) => { if (!rootRef.current?.contains(event.target as Node)) setOpen(false); };
    const escape = (event: KeyboardEvent) => { if (event.key === "Escape") setOpen(false); };
    document.addEventListener("pointerdown", close);
    document.addEventListener("keydown", escape);
    return () => {
      document.removeEventListener("pointerdown", close);
      document.removeEventListener("keydown", escape);
    };
  }, [open]);
  return (
    <div className="project-settings-anchor no-drag" ref={rootRef}>
      <button className={`project-automation-trigger no-drag ${project.automationEnabled ? "is-active" : "is-paused"}`} type="button" aria-haspopup="dialog" aria-expanded={open} onClick={() => setOpen((current) => !current)}>
        <TaskboardIcon name={project.automationEnabled ? "automationPause" : "automationPlay"} />
        <span>{project.automationEnabled ? t("自动认领中") : t("自动化")}</span>
      </button>
      {open && (
        <div className="project-automation-menu no-drag" role="dialog" aria-label={t("自动认领设置")}>
          <div className="project-automation-menu-heading"><strong>{t("自动认领待办")}</strong><span className={project.automationEnabled ? "is-active" : "is-paused"}>{project.automationEnabled ? t("运行中") : t("已暂停")}</span></div>
          <Switch checked={project.automationEnabled} label={t("自动认领开关")} description={t("就绪后自动执行")} onChange={(value) => onChange("automationEnabled", value)} />
          <Switch checked={project.reviewRequired} label={t("人工审阅")} description={t("完成后等你确认")} onChange={(value) => onChange("reviewRequired", value)} />
          <Switch checked={project.quotaAutoResumeEnabled} label={t("额度恢复自动续跑")} description={t("额度恢复后继续原 thread")} onChange={(value) => onChange("quotaAutoResumeEnabled", value)} />
          <p className="project-automation-note">{t("任务可单独选择模型和推理强度；未指定时沿用 Codex 会话设置。")}</p>
        </div>
      )}
    </div>
  );
}

function BoardToolbar({ search, onSearch, includeCanceled, onIncludeCanceled }: { search: string; onSearch: (value: string) => void; includeCanceled: boolean; onIncludeCanceled: (value: boolean) => void }) {
  return (
    <div className="board-toolbar">
      <div className="view-tabs" aria-label={t("看板视图")}><button className="view-tab active" type="button" aria-pressed="true">{t("议题看板")}</button></div>
      <div className="toolbar-tools">
        <div className={`search-field${search ? " has-value" : ""}`} title={t("搜索任务")}>
          <TaskboardIcon className="search-icon" name="search" />
          <input type="search" aria-label={t("搜索任务")} value={search} onChange={(event) => onSearch(event.target.value)} placeholder={t("搜索任务…")} />
          {!search && <kbd>/</kbd>}
          {search && <button className="search-clear" type="button" aria-label={t("清除搜索")} onClick={() => onSearch("")}><LinearIcon name="close" /></button>}
        </div>
        <button className={`other-tasks-trigger${includeCanceled ? " is-open" : ""}`} type="button" aria-controls="canceled-tasks-panel" aria-expanded={includeCanceled} aria-pressed={includeCanceled} onClick={() => onIncludeCanceled(!includeCanceled)} title={t("显示已取消任务")}><TaskboardIcon name="panel" /><span>{t("已取消")}</span></button>
      </div>
    </div>
  );
}

function ErrorBanner({ message, onRetry }: { message: string; onRetry: () => void }) {
  return <div className="error-banner" role="alert"><span className="error-mark"><LinearIcon name="alert" /></span><div><strong>{t("任务面板需要处理")}</strong><p>{localizeError(message)}</p></div><button type="button" onClick={onRetry}>{t("重试")}</button></div>;
}

function NonEmbeddedMessage() {
  return <div className="app-shell embedded-required"><main className="workspace"><div className="page-empty"><TaskboardIcon name="panel" /><h2>{t("请在 Codex 侧栏打开任务面板")}</h2><p>{t("Taskboard 只作为 Codex 内嵌面板运行，不提供独立软件页面。")}</p></div></main></div>;
}

function HostWaitingMessage({ hasProjects }: { hasProjects: boolean }) {
  return <div className="page-empty"><TaskboardIcon name="projectFolder" /><h2>{hasProjects ? t("正在同步 Codex 项目…") : t("等待 Codex 项目")}</h2><p>{hasProjects ? t("正在读取当前 Codex 项目。") : t("请先在 Codex 中打开一个本地项目。")}</p></div>;
}

export function App() {
  const locale = useLocale();
  useEffect(() => { document.documentElement.lang = locale; }, [locale]);
  const embedded = useMemo(isCodexEmbedded, []);
  if (!embedded) return <NonEmbeddedMessage />;
  return <EmbeddedTaskboard />;
}

function EmbeddedTaskboard() {
  const [hostContext, setHostContext] = useState<HostContext | null>(null);
  const [theme, setTheme] = useState<"light" | "dark">("light");
  const [projects, setProjects] = useState<Project[]>([]);
  const [selectedProjectId, setSelectedProjectId] = useState<string | null>(null);
  const [tasks, setTasks] = useState<Task[]>([]);
  const [selectedTaskId, setSelectedTaskId] = useState<string | null>(null);
  const [detail, setDetail] = useState<Task | null>(null);
  const [editorStatus, setEditorStatus] = useState<TaskStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [connectionError, setConnectionError] = useState<string | null>(null);
  const [syncError, setSyncError] = useState<string | null>(null);
  const [syncAttempt, setSyncAttempt] = useState(0);
  const [search, setSearch] = useState("");
  const [includeCanceled, setIncludeCanceled] = useState(false);
  const [draggedTaskId, setDraggedTaskId] = useState<string | null>(null);
  const [dropTarget, setDropTarget] = useState<TaskStatus | null>(null);
  const [notices, setNotices] = useState<Notice[]>([]);
  const noticeId = useRef(0);
  const selectedTaskIdRef = useRef(selectedTaskId);
  selectedTaskIdRef.current = selectedTaskId;
  const dragRegionRef = useRef<HTMLDivElement>(null);
  // Depend on project values, not the once-per-second host envelope identity.
  const projectContextKey = JSON.stringify({ projects: hostContext?.projects ?? [], projectId: hostContext?.projectId ?? null, workspacePath: hostContext?.workspacePath ?? null });
  const projectContext = useMemo(() => JSON.parse(projectContextKey) as HostContext, [projectContextKey]);
  const hasHostContext = hostContext !== null;
  const selectedProject = useMemo(() => projects.find((project) => project.id === selectedProjectId) ?? null, [projects, selectedProjectId]);
  const selectedTask = detail ?? (selectedTaskId ? tasks.find((task) => task.id === selectedTaskId) ?? null : null);

  const notify = useCallback((message: string, tone: Notice["tone"] = "info") => {
    const id = ++noticeId.current;
    setNotices((current) => [...current, { id, tone, message }]);
    window.setTimeout(() => setNotices((current) => current.filter((notice) => notice.id !== id)), 3800);
  }, []);

  useEffect(() => { document.documentElement.dataset.theme = theme; }, [theme]);

  useEffect(() => {
    const cleanExternal = installEmbeddedExternalLinkHandler();
    function receive(event: MessageEvent) {
      if (event.source !== window.parent || !event.data || typeof event.data !== "object") return;
      const message = event.data as { type?: string; payload?: unknown; challenge?: string; theme?: string };
      if (message.type === "taskboard:frame-challenge") {
        const payload = message.payload && typeof message.payload === "object" ? message.payload as Record<string, unknown> : {};
        const challenge = typeof payload.challenge === "string" ? payload.challenge : typeof message.challenge === "string" ? message.challenge : "";
        if (!challenge) return;
        setEmbeddedFrameChallenge(challenge);
        postEmbeddedHostMessage({ type: "taskboard:ready" });
        return;
      }
      if (message.type === "taskboard:host-context" && message.payload && typeof message.payload === "object") {
        const context = message.payload as HostContext;
        setHostLanguage(context.language);
        setHostContext(context);
        if (context.theme === "light" || context.theme === "dark") setTheme(context.theme);
        return;
      }
      if (message.type === "taskboard:theme" && (message.theme === "light" || message.theme === "dark")) setTheme(message.theme);
    }
    window.addEventListener("message", receive);
    postEmbeddedHostMessage({ type: "taskboard:frame-awaiting-challenge" });
    return () => { window.removeEventListener("message", receive); cleanExternal(); };
  }, []);

  useEffect(() => {
    const element = dragRegionRef.current;
    if (!element) return;
    const report = () => {
      const rect = element.getBoundingClientRect();
      postEmbeddedHostMessage({ type: "taskboard:drag-region", payload: { x: rect.x, y: rect.y, width: rect.width, height: rect.height } });
    };
    report();
    const observer = new ResizeObserver(report);
    observer.observe(element);
    window.addEventListener("resize", report);
    return () => { observer.disconnect(); window.removeEventListener("resize", report); };
  }, [hostContext?.titlebarLeftInset]);

  useEffect(() => {
    if (!hasHostContext) return;
    const hostContext = projectContext;
    let active = true;
    setLoading(true);
    setSyncError(null);
    void syncCodexProjects(hostContext).then((nextProjects) => {
      if (!active) return;
      const visibleCodexIds = new Set(
        (hostContext.projects ?? [])
          .filter((project) => project.projectKind === "local" && Boolean(project.workspacePath) && Boolean(project.id))
          .map((project) => project.id),
      );
      // Empty/incomplete snapshots occur while the native sidebar rebuilds.
      // Persisted Codex rows were accepted as local by the sync endpoint.
      const excludedIds = new Set((hostContext.projects ?? [])
        .filter(project => project.projectKind && project.projectKind !== "local")
        .map(project => project.id));
      const visibleProjects = nextProjects.filter((project) => (
        Boolean(project.codexProjectId) && Boolean(project.workspacePath)
        && !excludedIds.has(project.codexProjectId as string)
        && (visibleCodexIds.size === 0 || visibleCodexIds.has(project.codexProjectId as string))
      ));
      setProjects(visibleProjects);
      const hostProjectId = hostContext.projectId;
      const match = visibleProjects.find((project) => project.codexProjectId === hostProjectId || project.id === hostProjectId || (hostContext.workspacePath && project.workspacePath === hostContext.workspacePath));
      setSelectedProjectId(current => visibleProjects.some(project => project.id === current) ? current : match?.id ?? visibleProjects[0]?.id ?? null);
    }).catch((error) => {
      if (!active) return;
      setSyncError(compactError(error));
      notify(compactError(error), "error");
    }).finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [hasHostContext, projectContext, notify, syncAttempt]);

  const refreshTasks = useCallback(async (projectId = selectedProjectId) => {
    if (!projectId) { setTasks([]); return; }
    try {
      const loaded = await listTasks(projectId, true);
      setTasks(current => loaded.map(next => {
        const previous = current.find(task => task.id === next.id);
        return previous && previous.version > next.version ? previous : next;
      }));
      if (selectedTaskIdRef.current) {
        const next = await getTask(selectedTaskIdRef.current);
        setDetail(current => current?.id === next.id && next.version >= current.version ? next : current);
      }
      setConnectionError(null);
    } catch (error) {
      setConnectionError(compactError(error));
    }
  }, [selectedProjectId]);

  useEffect(() => {
    setSelectedTaskId(null);
    setDetail(null);
    void refreshTasks(selectedProjectId);
  }, [refreshTasks, selectedProjectId]);

  useEffect(() => {
    let timer: ReturnType<typeof setTimeout> | undefined;
    const close = createEventStream((event: EventEnvelope) => {
      if (event.projectId && selectedProjectId && event.projectId !== selectedProjectId) return;
      if (!timer) timer = setTimeout(() => {
        timer = undefined;
        void refreshTasks(selectedProjectId);
      }, 200);
    }, () => setConnectionError(t("无法连接后端，正在尝试重新连接…")), () => { void refreshTasks(selectedProjectId); });
    return () => { close(); clearTimeout(timer); };
  }, [refreshTasks, selectedProjectId]);

  const updateTaskInState = useCallback((next: Task) => {
    setTasks((current) => current.map((task) => task.id === next.id && next.version >= task.version ? next : task));
    setDetail((current) => current?.id === next.id && next.version >= current.version ? next : current);
  }, []);

  const updateTaskResource = useCallback(async (task: Task, changes: (Partial<Pick<Task, "title" | "description" | "priority" | "model" | "reasoningEffort">> & { attachments?: import("./types").AttachmentInput[] })) => {
    try {
      const next = await updateTask(task.id, task.version, changes);
      updateTaskInState(next);
      return next;
    } catch (error) {
      if (error instanceof ApiError && error.status === 409) await refreshTasks();
      notify(compactError(error), "error");
      return null;
    }
  }, [notify, refreshTasks, updateTaskInState]);

  const updateDependencies = useCallback(async (task: Task, ids: string[]) => {
    try {
      const next = await replaceDependencies(task.id, task.version, ids);
      updateTaskInState(next);
      return next;
    } catch (error) {
      if (error instanceof ApiError && error.status === 409) await refreshTasks();
      notify(compactError(error), "error");
      return null;
    }
  }, [notify, refreshTasks, updateTaskInState]);

  const performAction = useCallback(async (task: Task, action: TaskAction, feedback?: string, targetStatus?: "in_review" | "done") => {
    try {
      const next = await taskAction(task.id, action, task.version, feedback, targetStatus);
      updateTaskInState(next);
      notify(action === "complete" ? t("任务已完成") : action === "run" || action === "retry" ? t("任务已提交给 Codex") : t("操作已提交"), "success");
      return next;
    } catch (error) {
      if (error instanceof ApiError && error.status === 409) await refreshTasks();
      notify(compactError(error), "error");
      return null;
    }
  }, [notify, refreshTasks, updateTaskInState]);

  const updateProjectSetting = useCallback(async (field: "automationEnabled" | "reviewRequired" | "quotaAutoResumeEnabled", value: boolean) => {
    if (!selectedProject) return;
    try {
      const next = await updateProject(selectedProject.id, selectedProject.version, { [field]: value });
      setProjects((current) => current.map((project) => project.id === next.id ? next : project));
      notify(value ? t("{0}已开启", field === "automationEnabled" ? t("自动认领") : field === "reviewRequired" ? t("人工审阅") : t("额度恢复自动续跑")) : t("{0}已关闭", field === "automationEnabled" ? t("自动认领") : field === "reviewRequired" ? t("人工审阅") : t("额度恢复自动续跑")), "success");
    } catch (error) {
      notify(compactError(error), "error");
    }
  }, [notify, selectedProject]);

  async function handleCreateTask(input: { title: string; description: string; priority: TaskPriority; blockedByIds: string[]; model: string | null; reasoningEffort: string | null }) {
    if (!selectedProject) return;
    const created = await createTask(selectedProject.id, input);
    setTasks((current) => [...current, created]);
    notify(t("任务已创建"), "success");
    if (input.priority !== "draft" && editorStatus && editorStatus !== "todo") {
      const next = await performAction(created, editorStatus === "in_progress" ? "run" : "complete", undefined, editorStatus === "in_review" ? "in_review" : editorStatus === "done" ? "done" : undefined);
      if (next) updateTaskInState(next);
    }
  }

  async function resolveTaskInteraction(interaction: Interaction, response: unknown) {
    try {
      await resolveInteraction(interaction.id, interaction.version, response);
      notify(t("已提交给 Codex"), "success");
      await refreshTasks();
    } catch (error) {
      notify(compactError(error), "error");
    }
  }

  function dragOver(event: DragEvent<HTMLElement>, status: TaskStatus) {
    event.preventDefault();
    event.dataTransfer.dropEffect = "move";
    setDropTarget(status);
  }

  async function drop(status: TaskStatus, taskId: string) {
    setDropTarget(null);
    setDraggedTaskId(null);
    const task = tasks.find((candidate) => candidate.id === taskId);
    if (!task || task.status === status) return;
    if (task.status === "done") { notify(t("已完成任务不能拖回。"), "error"); return; }
    if (task.status === "todo" && status === "in_progress") {
      if (!task.ready) { notify(t("请先发布草稿并完成前置任务。"), "error"); return; }
      await performAction(task, "run");
      return;
    }
    if (status === "done" && (task.status === "todo" || task.status === "in_review" || task.status === "in_progress")) {
      if (window.confirm(t("确定将 {0} 标记为已完成吗？", task.identifier))) await performAction(task, "complete", undefined, task.status === "in_progress" ? "done" : undefined);
      return;
    }
    if (task.status === "in_progress" && status === "todo") {
      if (window.confirm(t("这会暂停当前执行并将任务退回草稿，保留原会话。继续吗？"))) await performAction(task, "interrupt_requeue");
      return;
    }
    if (task.status === "in_progress" && status === "in_review") {
      if (window.confirm(t("这会中断当前 turn，并将任务送入人工审阅。继续吗？"))) await performAction(task, "complete", undefined, "in_review");
      return;
    }
    if (task.status === "in_review" && status === "in_progress") {
      const feedback = window.prompt(t("请填写退回修改的反馈："), "");
      if (feedback?.trim()) await performAction(task, "submit_review_feedback", feedback.trim());
      return;
    }
    notify(t("不能从「{0}」移动到「{1}」。", STATUS_LABELS[task.status as TaskStatus] ?? task.status, STATUS_LABELS[status]), "error");
  }

  const visibleTasks = useMemo(() => {
    const needle = search.trim().toLocaleLowerCase();
    return tasks.filter((task) => {
      if (!includeCanceled && task.status === "canceled") return false;
      return !needle || `${task.identifier} ${task.title} ${task.description}`.toLocaleLowerCase().includes(needle);
    });
  }, [includeCanceled, search, tasks]);
  const grouped = useMemo(() => Object.fromEntries(COLUMN_ORDER.map((status) => [status, visibleTasks.filter((task) => task.status === status).sort((a, b) => status === "done" ? Date.parse(b.completedAt ?? b.updatedAt) - Date.parse(a.completedAt ?? a.updatedAt) : 0)])) as Record<TaskStatus, Task[]>, [visibleTasks]);
  const appShellStyle = { "--codex-titlebar-left-inset": `${hostContext?.titlebarLeftInset ?? 0}px`, "--main-column-count": 4, "--main-board-min-width": "1272px", "--main-board-max-width": "1672px" } as CSSProperties;

  return (
    <div className="app-shell embedded" style={appShellStyle}>
      <main className="workspace">
        <div className="home-window-drag-region" aria-hidden="true" />
        <header className="workspace-header">
          <div className="workspace-title"><div className="workspace-kicker">
            {selectedTask && <button className="detail-back-button" type="button" onClick={() => { setSelectedTaskId(null); setDetail(null); }} aria-label={t("返回议题看板")}><LinearIcon name="chevronLeft" /></button>}
            {hostContext?.sidebarCollapsed && <button className="detail-back-button codex-sidebar-expand-button" type="button" onClick={() => postEmbeddedHostMessage({ type: "taskboard:expand-sidebar" })} aria-label={t("展开 Codex 侧边栏")}><LinearIcon name="codexSidebarExpand" /></button>}
            <ProjectSwitcher projects={projects} selected={selectedProject} onSelect={(project) => setSelectedProjectId(project.id)} />
          </div></div>
          <div ref={dragRegionRef} className="workspace-drag-region" aria-hidden="true" />
          <div className="header-actions">
            {selectedProject && <ProjectSettingsMenu project={selectedProject} onChange={(field, value) => void updateProjectSetting(field, value)} />}
            {selectedProject && <button className="icon-button header-create-button no-drag" type="button" onClick={() => setEditorStatus("todo")} aria-label={t("新建任务")} title={t("新建任务")}><LinearIcon name="plus" /></button>}
          </div>
        </header>

        {!selectedTask && selectedProject && <BoardToolbar search={search} onSearch={setSearch} includeCanceled={includeCanceled} onIncludeCanceled={setIncludeCanceled} />}
        {syncError && <ErrorBanner message={syncError} onRetry={() => setSyncAttempt(value => value + 1)} />}
        {!hostContext || loading || projects.length === 0 || !selectedProject ? <HostWaitingMessage hasProjects={Boolean(hostContext?.projects?.length)} /> : selectedTask ? <TaskDetail key={selectedTask.id} task={selectedTask} tasks={tasks.filter((task) => task.status !== "canceled")} onBack={() => { setSelectedTaskId(null); setDetail(null); }} onUpdate={updateTaskResource} onDependencies={updateDependencies} onAction={performAction} onResolveInteraction={resolveTaskInteraction} /> : (
          <div className={`issue-board-layout${includeCanceled ? " has-other-tasks" : ""}`} data-main-columns={4}>
            <div className="board-scroll" aria-label={t("议题看板")}>
              <div className="board">
                {COLUMN_ORDER.map((status) => <BoardColumn key={status} status={status} tasks={grouped[status]} isDropTarget={dropTarget === status} draggedTaskId={draggedTaskId} onCreate={setEditorStatus} onEdit={(task) => { setSelectedTaskId(task.id); setDetail(task); void getTask(task.id).then(next => setDetail(current => current?.id === next.id && next.version >= current.version ? next : current)).catch((error) => notify(compactError(error), "error")); }} onComplete={(task) => { if (window.confirm(t("确定完成 {0} 吗？", task.identifier))) void performAction(task, "complete"); }} onDragStart={(task) => setDraggedTaskId(task.id)} onDragEnd={() => { setDraggedTaskId(null); setDropTarget(null); }} onDragEnter={setDropTarget} onDrop={(column, taskId) => void drop(column, taskId)} />)}
              </div>
            </div>
            {includeCanceled && <aside className="other-tasks-panel is-open" id="canceled-tasks-panel" aria-label={t("已取消任务")}><header className="other-tasks-header"><div className="other-tasks-heading"><TaskboardIcon name="panel" /><h2>{t("已取消")}</h2></div><button className="icon-button other-tasks-close" type="button" onClick={() => setIncludeCanceled(false)} aria-label={t("关闭已取消任务")}><LinearIcon name="close" /></button></header><div className="other-tasks-list">{visibleTasks.filter((task) => task.status === "canceled").map((task) => <TaskCard key={task.id} task={task} isDragging={false} onEdit={(item) => { setSelectedTaskId(item.id); setDetail(item); void getTask(item.id).then(next => setDetail(current => current?.id === next.id && next.version >= current.version ? next : current)).catch((error) => notify(compactError(error), "error")); }} onComplete={() => undefined} onDragStart={() => undefined} onDragEnd={() => undefined} />)}{visibleTasks.filter((task) => task.status === "canceled").length === 0 && <div className="other-tasks-empty"><strong>{t("没有已取消任务")}</strong><span>{t("被取消的任务会显示在这里。")}</span></div>}</div></aside>}
          </div>
        )}
      </main>
      {editorStatus && selectedProject && <TaskEditor initialStatus={editorStatus} task={null} candidates={tasks} onClose={() => setEditorStatus(null)} onCreate={async (input) => { await handleCreateTask(input); }} onUpdate={async () => undefined} />}
      <div className="notice-stack" aria-live="polite">{notices.map((notice) => <div className={`notice notice-${notice.tone}`} key={notice.id}>{localizeError(notice.message)}</div>)}</div>
      {connectionError && <div className="board-sync-indicator" role="alert">{localizeError(connectionError)}</div>}
    </div>
  );
}
