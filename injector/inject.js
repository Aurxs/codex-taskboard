(() => {
  "use strict";

  // The only renderer-owned objects are a cloned sidebar entry and a page
  // mounted in Codex's main content surface.  This script deliberately does
  // not patch fetch/React, create conversations, install a Skill, or add
  // hidden instructions to a Codex turn.
  const VERSION = "0.2.0";
  const SOURCE_HASH = window.__CODEX_TASKBOARD_SOURCE_HASH__ || "";
  const SENTINEL_KEY = "__codexTaskboardInjection__";
  const DEFAULT_TASKBOARD_URL = "http://127.0.0.1:47823/?host=codex&embedded=1";
  const ENTRY_ID = "codex-taskboard-entry";
  const PAGE_ID = "codex-taskboard-page";
  const FRAME_ID = "codex-taskboard-frame";
  const DRAG_REGION_ID = "codex-taskboard-drag-region";
  const NO_DRAG_LEFT_ID = "codex-taskboard-no-drag-left";
  const NO_DRAG_RIGHT_ID = "codex-taskboard-no-drag-right";
  const STATUS_ID = "codex-taskboard-status";
  const STYLE_ID = "codex-taskboard-inject-style";
  const OWNED_ATTRIBUTE = "data-codex-taskboard-owned";
  const HIDDEN_ATTRIBUTE = "data-codex-taskboard-native-hidden";
  const HOST_ATTRIBUTE = "data-codex-taskboard-page-host";
  const NATIVE_SELECTED_ATTRIBUTE = "data-codex-taskboard-native-selected";
  const HOST_REQUEST_MESSAGE = "__codexTaskboardHostRequestV1";
  const HOST_RESPONSE_MESSAGE = "__codexTaskboardHostResponseV1";
  const HOST_CAPABILITY = window.__CODEX_TASKBOARD_HOST_CAPABILITY__ || "";
  const FRAME_REFRESH_PARAM = "__codex_taskboard_refresh";
  const FRAME_READY_TIMEOUT_MS = 12_000;
  const HOST_REQUEST_TIMEOUT_MS = 12_000;
  const REATTACH_DELAY_MS = 160;
  const PLUGIN_LABELS = ["插件", "plugins"];
  const NATIVE_PAGE_LABELS = [
    "新建任务", "新聊天", "新对话", "new task", "new chat",
    "拉取请求", "pull requests", "站点", "sites", "已安排", "scheduled",
    "插件", "plugins",
  ];
  const PROJECT_SECTION_LABELS = ["projects", "项目"];
  const TASK_SECTION_LABELS = ["tasks", "任务", "chats", "对话"];

  const previous = window[SENTINEL_KEY];
  if (previous?.sourceHash === SOURCE_HASH && typeof previous.refresh === "function") {
    previous.refresh();
    return;
  }
  try { previous?.destroy?.(); } catch (_) {}

  let entry = null;
  let entryLabel = null;
  let page = null;
  let frame = null;
  let dragRegion = null;
  let noDragLeft = null;
  let noDragRight = null;
  let status = null;
  let active = false;
  let destroyed = false;
  let frameReady = false;
  let frameCapability = "";
  let frameChallenge = "";
  let frameTaskboardUrl = "";
  let statusView = "idle";
  let loadError = null;
  let openGeneration = 0;
  let preparePromise = null;
  let observer = null;
  let reattachTimer = null;
  let hostContextTimer = null;
  let hostRequests = new Map();
  let hostRequestSequence = 0;
  let frameReadyWaiters = new Set();
  let mutedNativeSelections = new Map();
  let hostContextSnapshot = null;
  let codexProjectMetadata = new Map();
  let lastNativeProjectId = "";
  let lastNativeThreadId = "";

  function normalizedLabel(value) {
    return String(value || "").replace(/\s+/g, " ").trim().toLowerCase();
  }

  function hostLanguage() {
    return document.documentElement.lang || navigator.language || "en";
  }

  function resolvedHostLanguage() {
    const language = hostLanguage().trim().replaceAll("_", "-").toLowerCase();
    return language === "zh" || language.startsWith("zh-") ? "zh" : "en";
  }

  function hostText(chinese, english) {
    return resolvedHostLanguage() === "zh" ? chinese : english;
  }

  function resolveTaskboardUrl(cacheBust = false) {
    const configured = typeof window.__CODEX_TASKBOARD_URL__ === "string"
      ? window.__CODEX_TASKBOARD_URL__.trim()
      : "";
    try {
      const url = new URL(configured || DEFAULT_TASKBOARD_URL);
      if (!["http:", "https:"].includes(url.protocol)) throw new Error("protocol");
      if (!["127.0.0.1", "localhost"].includes(url.hostname)) throw new Error("host");
      if (!url.searchParams.has("host")) url.searchParams.set("host", "codex");
      url.searchParams.set("embedded", "1");
      if (cacheBust) url.searchParams.set(FRAME_REFRESH_PARAM, Date.now().toString(36));
      return url;
    } catch (_) {
      return new URL(DEFAULT_TASKBOARD_URL);
    }
  }

  function installStyles() {
    if (document.getElementById(STYLE_ID)) return;
    const style = document.createElement("style");
    style.id = STYLE_ID;
    style.setAttribute(OWNED_ATTRIBUTE, "true");
    style.textContent = `
      #${ENTRY_ID}[aria-current="page"] {
        background: var(--color-token-list-hover-background, color-mix(in srgb, currentColor 8%, transparent));
        color: var(--color-token-foreground, inherit);
      }
      #${ENTRY_ID}:focus-visible { outline: 2px solid var(--color-token-border, Highlight); outline-offset: 2px; }
      [${HOST_ATTRIBUTE}="true"] { position: relative !important; z-index: 31 !important; pointer-events: none !important; }
      [${HIDDEN_ATTRIBUTE}="true"] { visibility: hidden !important; pointer-events: none !important; }
      [${NATIVE_SELECTED_ATTRIBUTE}="true"] { background-color: transparent !important; }
      [${NATIVE_SELECTED_ATTRIBUTE}="true"] [class*="text-token-list-active-selection"] { color: var(--color-token-foreground, inherit) !important; }
      #${PAGE_ID} { position: absolute; inset: 0; z-index: 1; min-width: 0; min-height: 0; overflow: hidden; background: Canvas; color: CanvasText; pointer-events: auto; }
      #${PAGE_ID}[hidden] { display: none !important; }
      #${FRAME_ID} { display: block; width: 100%; height: 100%; border: 0; background: Canvas; }
      #${FRAME_ID}[hidden] { display: none !important; }
      #${DRAG_REGION_ID} { position: absolute; z-index: 2; background: transparent; pointer-events: none; -webkit-app-region: drag; }
      #${NO_DRAG_LEFT_ID}, #${NO_DRAG_RIGHT_ID} { position: absolute; z-index: 2; background: transparent; pointer-events: none; -webkit-app-region: no-drag; }
      #${DRAG_REGION_ID}[hidden], #${NO_DRAG_LEFT_ID}[hidden], #${NO_DRAG_RIGHT_ID}[hidden] { display: none !important; }
      #${STATUS_ID} { position: absolute; inset: 0; display: grid; place-items: center; padding: 24px; color: var(--color-token-text-secondary, color-mix(in srgb, CanvasText 60%, transparent)); font: 13px/1.5 system-ui, sans-serif; text-align: center; }
      #${STATUS_ID}[hidden] { display: none !important; }
      #${STATUS_ID} button { margin-top: 10px; border: 1px solid var(--color-token-border, color-mix(in srgb, CanvasText 16%, transparent)); border-radius: 7px; padding: 5px 10px; background: var(--color-token-main-surface-secondary, Canvas); color: var(--color-token-foreground, CanvasText); cursor: pointer; }
    `;
    (document.head || document.documentElement).appendChild(style);
  }

  function buttonMatches(button, labels) {
    if (!button) return false;
    const text = normalizedLabel(button.textContent || button.getAttribute("aria-label"));
    return labels.includes(text);
  }

  // Reusing the native button preserves Codex's spacing, icon sizing,
  // tooltip and collapsed-sidebar behavior.
  function findReferenceButton() {
    const scroll = document.querySelector("[data-app-action-sidebar-scroll]");
    if (!scroll) return null;
    const buttons = Array.from(scroll.querySelectorAll("button"));
    const plugin = buttons.find((button) => buttonMatches(button, PLUGIN_LABELS));
    if (plugin?.parentElement) return plugin;
    const firstSection = scroll.querySelector("[data-app-action-sidebar-section]");
    const sectionTop = firstSection?.getBoundingClientRect().top ?? Number.POSITIVE_INFINITY;
    const groups = Array.from(scroll.querySelectorAll("div")).filter((element) => {
      const directButtons = Array.from(element.children).filter((child) => child.tagName === "BUTTON");
      return directButtons.length >= 3 && element.getBoundingClientRect().top < sectionTop;
    });
    const group = groups.sort((left, right) => right.children.length - left.children.length)[0];
    return Array.from(group?.children || []).filter((child) => child.tagName === "BUTTON").at(-1) || null;
  }

  function replaceEntryIcon(button) {
    const icon = button.querySelector("svg");
    if (!icon) return;
    icon.setAttribute("viewBox", "0 0 24 24");
    icon.setAttribute("fill", "none");
    icon.setAttribute("stroke", "currentColor");
    icon.setAttribute("stroke-width", "1.8");
    icon.setAttribute("stroke-linecap", "round");
    icon.setAttribute("stroke-linejoin", "round");
    icon.innerHTML = '<rect x="3.5" y="4" width="17" height="16" rx="2.5"></rect><path d="M9 4v16M14.5 8h2.5M14.5 12h2.5M14.5 16h2.5"></path>';
  }

  function syncEntryText(button = entry) {
    if (!button) return;
    button.setAttribute("aria-label", hostText("打开任务面板", "Open Taskboard"));
    button.setAttribute("title", hostText("任务面板", "Taskboard"));
    if (entryLabel) entryLabel.textContent = hostText("任务面板", "Taskboard");
    else if (!button.querySelector("svg")) button.textContent = hostText("任务面板", "Taskboard");
  }

  function createEntry(reference) {
    const button = reference.cloneNode(true);
    button.id = ENTRY_ID;
    button.type = "button";
    button.removeAttribute("disabled");
    ["aria-expanded", "aria-controls", "aria-describedby", "data-state", "aria-current"].forEach((name) => button.removeAttribute(name));
    button.setAttribute(OWNED_ATTRIBUTE, "true");
    button.querySelectorAll("[id]").forEach((node) => node.removeAttribute("id"));
    entryLabel = button.querySelector(".text-fade-truncate")
      || Array.from(button.querySelectorAll("span")).find((node) => buttonMatches(node, PLUGIN_LABELS));
    replaceEntryIcon(button);
    syncEntryText(button);
    button.addEventListener("click", (event) => {
      event.preventDefault();
      event.stopPropagation();
      if (active) closeTaskboard();
      else openTaskboard();
    });
    return button;
  }

  function ensureEntry() {
    if (destroyed || !document.body) return;
    installStyles();
    const reference = findReferenceButton();
    if (!reference?.parentElement) return;
    if (!entry || !entry.isConnected || entry.parentElement !== reference.parentElement) {
      entry?.remove();
      entry = createEntry(reference);
    }
    if (entry.previousElementSibling !== reference) reference.after(entry);
    syncEntryText();
    syncEntryState();
  }

  // This is the page host used by the original Taskboard: the injected page
  // is a sibling of Codex's native content frame inside <main>, never a fixed
  // body-level panel or a second Taskboard window.
  function findPageHost() {
    const direct = document.querySelector(".app-shell-main-content-frame");
    if (direct?.closest?.("[data-app-shell-main-content-layout]")) return direct;
    const viewport = document.querySelector("[data-app-shell-main-content-layout]");
    if (!viewport) return null;
    const viewportRect = viewport.getBoundingClientRect();
    return Array.from(viewport.children).find((candidate) => {
      const rect = candidate.getBoundingClientRect();
      return rect.width >= viewportRect.width * 0.8 && rect.height >= viewportRect.height * 0.7;
    }) || null;
  }

  function findPageMount() {
    const frameHost = findPageHost();
    const viewport = frameHost?.closest?.("[data-app-shell-main-content-layout]");
    const surface = viewport?.parentElement;
    if (!frameHost || !viewport || !surface || !surface.closest("main")) return null;
    return { frameHost, surface };
  }

  function syncEntryState() {
    if (!entry) return;
    if (active) entry.setAttribute("aria-current", "page");
    else entry.removeAttribute("aria-current");
  }

  function muteNativeSelection() {
    if (!active) return;
    document.querySelectorAll('aside nav[role="navigation"] [aria-current]').forEach((node) => {
      if (node === entry || node.closest(`#${ENTRY_ID}`)) return;
      if (!mutedNativeSelections.has(node)) mutedNativeSelections.set(node, node.getAttribute("aria-current"));
      node.removeAttribute("aria-current");
      node.setAttribute(NATIVE_SELECTED_ATTRIBUTE, "true");
    });
  }

  function restoreNativeSelection() {
    mutedNativeSelections.forEach((value, node) => {
      if (node.isConnected) node.setAttribute("aria-current", value);
      node.removeAttribute(NATIVE_SELECTED_ATTRIBUTE);
    });
    mutedNativeSelections.clear();
    document.querySelectorAll(`[${NATIVE_SELECTED_ATTRIBUTE}="true"]`).forEach((node) => node.removeAttribute(NATIVE_SELECTED_ATTRIBUTE));
  }

  function hideNativeHeader() {
    document.querySelectorAll('[data-testid="app-shell-header-context-menu-surface"]').forEach((surface) => {
      Array.from(surface.children).forEach((child) => {
        if (child.getAttribute(OWNED_ATTRIBUTE) !== "true") child.setAttribute(HIDDEN_ATTRIBUTE, "true");
      });
    });
  }

  function currentTheme() {
    const root = document.documentElement;
    const explicit = String(root.dataset.theme || root.getAttribute("data-color-theme") || "").toLowerCase();
    if (explicit.includes("dark") || root.classList.contains("dark")) return "dark";
    if (explicit.includes("light") || root.classList.contains("light")) return "light";
    try { return window.getComputedStyle(root).colorScheme.includes("dark") ? "dark" : "light"; } catch (_) { return "light"; }
  }

  function nativeSidebarTrigger() {
    const triggers = Array.from(document.querySelectorAll('[data-app-shell-sidebar-trigger="true"]'));
    return triggers.find((trigger) => getComputedStyle(trigger).visibility !== "hidden") || triggers[0] || null;
  }

  function nativeSidebarCollapsed() {
    const label = normalizedLabel(nativeSidebarTrigger()?.getAttribute("aria-label"));
    return label.startsWith("显示") || label.startsWith("show ");
  }

  function titlebarLeftInset() {
    if (!/Macintosh|Mac OS X/.test(navigator.userAgent)) return 0;
    if (nativeSidebarCollapsed()) return 80;
    const left = findPageMount()?.surface.getBoundingClientRect().left;
    return Number.isFinite(left) ? Math.max(0, Math.ceil(80 - left)) : 0;
  }

  function userIdFromName(name) {
    const slug = String(name || "").normalize("NFKD").toLowerCase()
      .replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 96);
    if (slug) return slug;
    let hash = 2166136261;
    for (const character of String(name || "")) {
      hash ^= character.codePointAt(0);
      hash = Math.imul(hash, 16777619);
    }
    return `codex-user-${(hash >>> 0).toString(36)}`;
  }

  function readCodexUser() {
    const avatar = Array.from(document.querySelectorAll("img"))
      .find((image) => image.src.includes("cdn.auth0.com/avatars/"));
    const profileButton = avatar?.closest("button")
      || Array.from(document.querySelectorAll('button[aria-haspopup="menu"]')).find((button) => {
        const label = normalizedLabel(button.getAttribute("aria-label"));
        return label.includes("profile") || label.includes("个人资料");
      });
    const name = profileButton?.textContent?.replace(/\s+/g, " ").trim();
    if (!name) return undefined;
    return { type: "user", id: userIdFromName(name), name, avatarUrl: avatar?.currentSrc || avatar?.src || null };
  }

  function normalizeNativeRootPath(value) {
    const path = String(value || "").trim();
    if (!path) return "";
    const windowsPath = /^[A-Za-z]:[\\/]/.test(path) || path.includes("\\");
    const slashes = windowsPath ? path.replaceAll("\\", "/") : path;
    const withoutTrailing = slashes.replace(/\/+$/, "") || (slashes.startsWith("/") ? "/" : slashes);
    if (!windowsPath || !/^[A-Za-z]:/.test(withoutTrailing)) return withoutTrailing;
    return `${withoutTrailing[0].toLowerCase()}${withoutTrailing.slice(1)}`;
  }

  function requestNativeFetch(path, body) {
    const bridge = window.electronBridge;
    if (!bridge || typeof bridge.sendMessageFromView !== "function") return Promise.resolve(null);
    return new Promise((resolve) => {
      const requestId = `taskboard-native-fetch-${crypto.randomUUID()}`;
      let settled = false;
      const finish = (value = null) => {
        if (settled) return;
        settled = true;
        window.clearTimeout(timeout);
        window.removeEventListener("message", onMessage);
        resolve(value);
      };
      const onMessage = (event) => {
        const message = event.data;
        if (!message || typeof message !== "object" || message.type !== "fetch-response" || message.requestId !== requestId) return;
        try { finish(JSON.parse(message.bodyJsonString || "null")); } catch (_) { finish(); }
      };
      const timeout = window.setTimeout(finish, 1_000);
      window.addEventListener("message", onMessage);
      try {
        bridge.sendMessageFromView({ type: "fetch", requestId, method: "POST", url: `vscode://codex/${path}`, body: JSON.stringify(body) });
      } catch (_) { finish(); }
    });
  }

  async function selectedNativeProjectId() {
    const selectedProject = (await requestNativeFetch("get-global-state", { key: "selected-project" }))?.value;
    return typeof selectedProject?.projectId === "string" ? selectedProject.projectId.trim() : "";
  }

  async function activeNativeWorkspaceRoots() {
    const roots = (await requestNativeFetch("active-workspace-roots", {}))?.roots;
    return Array.isArray(roots)
      ? roots.filter((root) => typeof root === "string" && root.trim()).map(normalizeNativeRootPath)
      : [];
  }

  // electronBridge is the installed Codex renderer's bootstrap surface. DOM
  // rows provide the labels visible in the sidebar, while local-projects
  // global state provides real workspace roots.
  async function readCodexProjectMetadata() {
    let bootstrap = null;
    try { bootstrap = await window.electronBridge?.getInitialSidebarBootstrap?.(); } catch (_) {}
    const entries = new Map((Array.isArray(bootstrap?.globalStateEntries) ? bootstrap.globalStateEntries : []).map((item) => [item?.key, item?.value]));
    const metadata = new Map();
    const localProjects = entries.get("local-projects");
    if (localProjects && typeof localProjects === "object" && !Array.isArray(localProjects)) {
      Object.entries(localProjects).forEach(([projectId, project]) => {
        const id = projectId.trim();
        const workspacePath = Array.isArray(project?.rootPaths)
          ? project.rootPaths.find((root) => typeof root === "string" && root.trim())?.trim() : "";
        if (id) metadata.set(id, { projectKind: "local", hostId: "local", ...(workspacePath ? { workspacePath } : {}) });
      });
    }
    const remoteProjects = entries.get("remote-projects");
    if (Array.isArray(remoteProjects)) {
      remoteProjects.forEach((project) => {
        const id = typeof project?.id === "string" ? project.id.trim() : "";
        const workspacePath = typeof project?.remotePath === "string" ? project.remotePath.trim() : "";
        const hostId = typeof project?.hostId === "string" ? project.hostId.trim() : "";
        if (id && workspacePath && hostId) metadata.set(id, { projectKind: "remote", workspacePath, hostId });
      });
    }
    return metadata;
  }

  function readCodexProjects(metadata = codexProjectMetadata) {
    const seen = new Set();
    return Array.from(document.querySelectorAll("[data-app-action-sidebar-project-row]")).flatMap((row) => {
      const id = row.getAttribute("data-app-action-sidebar-project-id")?.trim();
      const name = (row.getAttribute("data-app-action-sidebar-project-label") || row.getAttribute("aria-label") || "").trim();
      if (!id || !name || seen.has(id)) return [];
      seen.add(id);
      return [{ id, name, ...metadata.get(id) }];
    });
  }

  function findProjectsSection() {
    return Array.from(document.querySelectorAll("[data-app-action-sidebar-section-heading]"))
      .find((node) => PROJECT_SECTION_LABELS.includes(normalizedLabel(node.getAttribute("data-app-action-sidebar-section-heading") || node.textContent)))
      ?.closest("[data-app-action-sidebar-section]") || null;
  }

  function findTasksSection() {
    return Array.from(document.querySelectorAll("[data-app-action-sidebar-section]"))
      .find((section) => {
        const heading = section.querySelector("[data-app-action-sidebar-section-heading]");
        return TASK_SECTION_LABELS.includes(normalizedLabel(heading?.getAttribute("data-app-action-sidebar-section-heading") || heading?.textContent || section.textContent));
      }) || null;
  }

  function activeThreadRow() {
    const rows = Array.from(document.querySelectorAll("[data-app-action-sidebar-thread-id]"));
    return rows.find((row) => row.getAttribute("data-app-action-sidebar-thread-active") === "true")
      || rows.find((row) => ["page", "true"].includes(row.getAttribute("aria-current"))) || null;
  }

  function threadIdFromLocation() {
    const source = `${window.location.pathname || ""}${window.location.search || ""}${window.location.hash || ""}`;
    const match = source.match(/(?:session|conversation|thread)(?:\/|=|:|-)([A-Za-z0-9_.-]+)/i)
      || source.match(/\/([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})(?:[/?#]|$)/);
    return match ? decodeURIComponent(match[1]) : "";
  }

  function workspaceFromLocation() {
    try {
      const url = new URL(window.location.href);
      return url.searchParams.get("workspace") || url.searchParams.get("cwd") || "";
    } catch (_) { return ""; }
  }

  async function captureHostContext() {
    const [selectedProjectId, projectMetadata, activeRoots] = await Promise.all([
      selectedNativeProjectId(), readCodexProjectMetadata(), activeNativeWorkspaceRoots(),
    ]);
    codexProjectMetadata = projectMetadata;
    if (selectedProjectId) lastNativeProjectId = selectedProjectId;
    let projects = readCodexProjects(projectMetadata);
    const sections = [findProjectsSection(), findTasksSection()].filter((section) => section?.getAttribute("data-app-action-sidebar-section-collapsed") === "true");
    sections.forEach((section) => section.querySelector("[data-app-action-sidebar-section-toggle]")?.click());
    if (sections.length) {
      const deadline = Date.now() + 1_200;
      while (projects.length === 0 && Date.now() < deadline) {
        await new Promise((resolve) => window.setTimeout(resolve, 40));
        projects = readCodexProjects(projectMetadata);
      }
    }
    const row = activeThreadRow();
    const activeThreadId = row?.getAttribute("data-app-action-sidebar-thread-id")?.trim() || lastNativeThreadId || threadIdFromLocation();
    if (activeThreadId) lastNativeThreadId = activeThreadId;
    const projectRow = row?.closest?.("[data-app-action-sidebar-project-row]")
      || document.querySelector('[data-app-action-sidebar-project-row][aria-current="page"]');
    const projectId = row?.closest?.("[data-app-action-sidebar-project-list-id]")?.getAttribute("data-app-action-sidebar-project-list-id")
      || projectRow?.getAttribute("data-app-action-sidebar-project-id") || lastNativeProjectId || "";
    const workspacePath = workspaceFromLocation()
      || projects.find((project) => project.id === projectId)?.workspacePath
      || activeRoots[0]
      || "";
    const context = {
      language: hostLanguage(),
      theme: currentTheme(),
      projects,
      activeWorkspaceRoots: activeRoots,
      user: readCodexUser(),
      titlebarLeftInset: titlebarLeftInset(),
      sidebarCollapsed: nativeSidebarCollapsed(),
      ...(workspacePath ? { workspacePath } : {}),
      ...(projectId ? { projectId } : {}),
      ...(activeThreadId ? { threadId: activeThreadId } : {}),
    };
    sections.forEach((section) => {
      if (section.isConnected && section.getAttribute("data-app-action-sidebar-section-collapsed") === "false") section.querySelector("[data-app-action-sidebar-section-toggle]")?.click();
    });
    return context;
  }

  function postToFrame(message, allowUnready = false) {
    if (!frame?.contentWindow || (!allowUnready && !frameReady)) return;
    frame.contentWindow.postMessage(message, "*");
  }

  function postFrameChallenge() {
    if (frameChallenge) postToFrame({ type: "taskboard:frame-challenge", payload: { challenge: frameChallenge } }, true);
  }

  function postHostContext() {
    if (!frame) return;
    const liveContext = { language: hostLanguage(), theme: currentTheme(), projects: readCodexProjects(), ...(lastNativeProjectId ? { projectId: lastNativeProjectId } : {}) };
    const payload = hostContextSnapshot
      ? { ...hostContextSnapshot, ...liveContext, projects: liveContext.projects.length ? liveContext.projects : hostContextSnapshot.projects }
      : liveContext;
    postToFrame({ type: "taskboard:host-context", payload });
    postToFrame({ type: "taskboard:theme", theme: payload.theme });
  }

  function createPage() {
    const section = document.createElement("section");
    section.id = PAGE_ID;
    section.hidden = true;
    section.setAttribute(OWNED_ATTRIBUTE, "true");
    section.setAttribute("role", "region");
    section.setAttribute("aria-label", hostText("任务面板", "Taskboard"));
    status = document.createElement("div");
    status.id = STATUS_ID;
    status.setAttribute("role", "status");
    status.setAttribute("aria-live", "polite");
    section.appendChild(status);

    dragRegion = document.createElement("div");
    dragRegion.id = DRAG_REGION_ID;
    dragRegion.hidden = true;
    dragRegion.setAttribute(OWNED_ATTRIBUTE, "true");
    dragRegion.setAttribute("aria-hidden", "true");
    section.appendChild(dragRegion);

    noDragLeft = document.createElement("div");
    noDragLeft.id = NO_DRAG_LEFT_ID;
    noDragLeft.hidden = true;
    noDragLeft.setAttribute(OWNED_ATTRIBUTE, "true");
    noDragLeft.setAttribute("aria-hidden", "true");
    section.appendChild(noDragLeft);

    noDragRight = document.createElement("div");
    noDragRight.id = NO_DRAG_RIGHT_ID;
    noDragRight.hidden = true;
    noDragRight.setAttribute(OWNED_ATTRIBUTE, "true");
    noDragRight.setAttribute("aria-hidden", "true");
    section.appendChild(noDragRight);
    return section;
  }

  function renderLoading() {
    if (!status) return;
    status.replaceChildren(document.createTextNode(hostText("正在启动任务面板…", "Starting Taskboard…")));
    status.hidden = false;
    if (frame) frame.hidden = true;
  }

  function showLoading() { statusView = "loading"; loadError = null; renderLoading(); }

  function showFrame() {
    statusView = "frame";
    loadError = null;
    if (status) status.hidden = true;
    if (frame) { frame.hidden = false; frame.focus?.(); }
  }

  function showLoadError(error) {
    statusView = "error";
    loadError = error;
    if (!status) return;
    const content = document.createElement("div");
    const text = document.createElement("div");
    text.textContent = error instanceof Error ? error.message : String(error || hostText("加载失败", "Loading failed"));
    const retry = document.createElement("button");
    retry.type = "button";
    retry.textContent = hostText("重新加载面板", "Reload panel");
    retry.addEventListener("click", openTaskboard, { once: true });
    content.append(text, retry);
    status.replaceChildren(content);
    status.hidden = false;
    if (frame) frame.hidden = true;
  }

  function cancelFrameReadyWaiters(error) {
    frameReadyWaiters.forEach(({ reject, timer }) => { window.clearTimeout(timer); reject(error); });
    frameReadyWaiters.clear();
  }

  function waitForFrameReady() {
    if (frameReady) return Promise.resolve();
    return new Promise((resolve, reject) => {
      const waiter = { resolve, reject, timer: window.setTimeout(() => {
        frameReadyWaiters.delete(waiter);
        reject(new Error(hostText("任务面板页面加载超时", "Taskboard page load timed out")));
      }, FRAME_READY_TIMEOUT_MS) };
      frameReadyWaiters.add(waiter);
    });
  }

  function loadTaskboardFrame(cacheBust = false) {
    cancelFrameReadyWaiters(new Error(hostText("任务面板正在重新加载", "Taskboard is reloading")));
    frame?.remove();
    frame = null;
    frameReady = false;
    frameChallenge = crypto.randomUUID();
    if (dragRegion) dragRegion.hidden = true;
    if (noDragLeft) noDragLeft.hidden = true;
    if (noDragRight) noDragRight.hidden = true;
    frameCapability = crypto.randomUUID();
    const taskboardUrl = resolveTaskboardUrl(cacheBust);
    frameTaskboardUrl = taskboardUrl.href;
    const nextFrame = document.createElement("iframe");
    nextFrame.id = FRAME_ID;
    nextFrame.name = `codex-taskboard-${crypto.randomUUID()}`;
    nextFrame.hidden = true;
    nextFrame.setAttribute("sandbox", "allow-scripts allow-forms allow-modals allow-downloads");
    nextFrame.src = "about:blank";
    nextFrame.title = hostText("任务面板", "Taskboard");
    nextFrame.referrerPolicy = "no-referrer";
    nextFrame.setAttribute("allow", "clipboard-read; clipboard-write");
    nextFrame.addEventListener("load", () => {
      if (frame !== nextFrame) return;
      // Module execution and ready can precede the iframe load event.
      // A late load must never hide an already authenticated frame.
      postFrameChallenge();
    });
    frame = nextFrame;
    page.appendChild(nextFrame);
    return { frameName: nextFrame.name, frameCapability };
  }

  function onFrameMessage(event) {
    if (!frame || event.source !== frame.contentWindow || event.origin !== "null") return;
    const message = event.data;
    if (!message || typeof message !== "object" || message.capability !== frameCapability) return;
    if (message.type === "taskboard:frame-awaiting-challenge") { postFrameChallenge(); return; }
    if (!frameChallenge || message.challenge !== frameChallenge) return;
    if (message.type === "taskboard:drag-region") {
      updateDragRegion(message.payload);
      return;
    }
    if (message.type === "taskboard:expand-sidebar") {
      expandNativeSidebar();
      return;
    }
    if (message.type !== "taskboard:ready" || frameReady) return;
    frameReady = true;
    frameReadyWaiters.forEach(({ resolve, timer }) => { window.clearTimeout(timer); resolve(); });
    frameReadyWaiters.clear();
    if (active) showFrame();
    postHostContext();
  }

  function expandNativeSidebar() {
    const trigger = nativeSidebarTrigger();
    if (!trigger || !nativeSidebarCollapsed()) return;
    trigger.click();
    window.setTimeout(postHostContext, REATTACH_DELAY_MS);
  }

  function updateDragRegion(payload) {
    if (!dragRegion || !noDragLeft || !noDragRight || !page) return;
    const x = Number(payload?.x);
    const y = Number(payload?.y);
    const width = Number(payload?.width);
    const height = Number(payload?.height);
    if (![x, y, width, height].every(Number.isFinite) || width <= 0 || height <= 0) {
      dragRegion.hidden = true;
      noDragLeft.hidden = true;
      noDragRight.hidden = true;
      return;
    }
    const left = Math.max(0, x);
    const right = left + width;
    dragRegion.style.left = `${left}px`;
    dragRegion.style.top = `${Math.max(0, y)}px`;
    dragRegion.style.width = `${width}px`;
    dragRegion.style.height = `${height}px`;
    noDragLeft.style.left = "0";
    noDragLeft.style.top = `${Math.max(0, y)}px`;
    noDragLeft.style.width = `${left}px`;
    noDragLeft.style.height = `${height}px`;
    noDragRight.style.left = `${right}px`;
    noDragRight.style.top = `${Math.max(0, y)}px`;
    noDragRight.style.right = "0";
    noDragRight.style.height = `${height}px`;
    dragRegion.hidden = false;
    noDragLeft.hidden = left <= 0;
    noDragRight.hidden = right >= page.clientWidth;
  }

  function requestHost(action, payload = {}, timeoutMs = HOST_REQUEST_TIMEOUT_MS) {
    if (!HOST_CAPABILITY) return Promise.reject(new Error(hostText("任务面板启动器未运行", "Taskboard launcher is unavailable")));
    const id = `${Date.now().toString(36)}-${(++hostRequestSequence).toString(36)}`;
    return new Promise((resolve, reject) => {
      const timer = window.setTimeout(() => {
        hostRequests.delete(id);
        reject(new Error(hostText("任务面板启动器没有响应", "Taskboard launcher did not respond")));
      }, timeoutMs);
      hostRequests.set(id, { resolve, reject, timer });
      try {
        window.postMessage({ type: HOST_REQUEST_MESSAGE, capability: HOST_CAPABILITY, payload: { ...payload, id, action } }, window.location.origin);
      } catch (error) {
        window.clearTimeout(timer);
        hostRequests.delete(id);
        reject(error);
      }
    });
  }

  function requestHostLoadFrame(request) {
    return requestHost("load-frame", request);
  }

  function onHostBridgeMessage(event) {
    if (event.source !== window || event.origin !== window.location.origin) return;
    const message = event.data;
    if (!message || typeof message !== "object" || message.type !== HOST_RESPONSE_MESSAGE || message.capability !== HOST_CAPABILITY) return;
    const response = message.response;
    const pending = response && hostRequests.get(response.id);
    if (!pending) return;
    window.clearTimeout(pending.timer);
    hostRequests.delete(response.id);
    if (response.ok) pending.resolve(response);
    else pending.reject(new Error(typeof response.error === "string" ? response.error : hostText("任务面板启动失败", "Taskboard failed to start")));
  }

  async function prepareTaskboard(generation) {
    if (preparePromise) return preparePromise;
    showLoading();
    preparePromise = (async () => {
      try {
        const context = await captureHostContext();
        if (!active || generation !== openGeneration) return;
        hostContextSnapshot = context;
        const frameRequest = loadTaskboardFrame();
        await requestHostLoadFrame(frameRequest);
        if (!active || generation !== openGeneration) return;
        await waitForFrameReady();
        if (!active || generation !== openGeneration) return;
        showFrame();
        postHostContext();
      } catch (error) {
        if (active && generation === openGeneration) showLoadError(error);
      } finally {
        preparePromise = null;
        if (active && !destroyed && generation !== openGeneration) scheduleRefresh();
      }
    })();
    return preparePromise;
  }

  function mountActivePage() {
    if (!active) return false;
    if (!page) page = createPage();
    const mount = findPageMount();
    if (!mount) return false;
    const { surface } = mount;
    const remounted = page.parentElement !== surface;
    if (remounted) {
      // Moving an iframe with appendChild destroys its browsing context.
      // Invalidate only an actual old frame, then reload after mounting.
      if (frame) {
        openGeneration += 1;
        cancelFrameReadyWaiters(new Error("Taskboard surface changed"));
        frame.remove();
        frame = null;
        frameReady = false;
        statusView = "idle";
      }
      restoreNativeContent();
      surface.appendChild(page);
    }
    surface.setAttribute(HOST_ATTRIBUTE, "true");
    Array.from(surface.children).forEach((child) => {
      if (child !== page && child.getAttribute(OWNED_ATTRIBUTE) !== "true") child.setAttribute(HIDDEN_ATTRIBUTE, "true");
    });
    hideNativeHeader();
    muteNativeSelection();
    page.hidden = false;
    document.documentElement.setAttribute("data-codex-taskboard-open", "true");
    return remounted;
  }

  function restoreNativeContent() {
    document.querySelectorAll(`[${HIDDEN_ATTRIBUTE}="true"]`).forEach((node) => node.removeAttribute(HIDDEN_ATTRIBUTE));
    document.querySelectorAll(`[${HOST_ATTRIBUTE}="true"]`).forEach((node) => node.removeAttribute(HOST_ATTRIBUTE));
  }

  function closeTaskboard(restoreFocus = true) {
    if (!active && page?.hidden !== false) return;
    openGeneration += 1;
    active = false;
    if (page) page.hidden = true;
    if (dragRegion) dragRegion.hidden = true;
    if (noDragLeft) noDragLeft.hidden = true;
    if (noDragRight) noDragRight.hidden = true;
    restoreNativeContent();
    restoreNativeSelection();
    document.documentElement.removeAttribute("data-codex-taskboard-open");
    syncEntryState();
    if (restoreFocus) entry?.focus?.();
    hostContextSnapshot = null;
  }

  function isNativePageNavigation(target) {
    const clickable = target?.closest?.("button,a,[role='button'],[data-app-action-sidebar-thread-id]");
    if (!clickable || clickable === entry || clickable.closest(`#${ENTRY_ID}`)) return false;
    if (!clickable.closest("aside nav[role='navigation']")) return false;
    if (clickable.hasAttribute("data-app-action-sidebar-section-toggle")) return false;
    if (buttonMatches(clickable, NATIVE_PAGE_LABELS)) return true;
    return Boolean(clickable.closest("[data-app-action-sidebar-thread-id],[data-app-action-sidebar-project-row],[data-app-action-sidebar-project-id]"));
  }

  function onDocumentClick(event) {
    const threadRow = event.target?.closest?.("[data-app-action-sidebar-thread-id]");
    const clickedThreadId = threadRow?.getAttribute("data-app-action-sidebar-thread-id")?.trim();
    if (clickedThreadId) lastNativeThreadId = clickedThreadId;
    if (active && isNativePageNavigation(event.target)) closeTaskboard(false);
  }

  function onNativeRouteChange() {
    if (active) closeTaskboard(false);
  }

  function openTaskboard() {
    if (destroyed) return;
    if (!active) hostContextSnapshot = null;
    active = true;
    ensureEntry();
    mountActivePage();
    syncEntryState();
    if (preparePromise) return;
    if (frameReady && frame?.isConnected) {
      showFrame();
      postHostContext();
      return;
    }
    if (page?.isConnected) void prepareTaskboard(++openGeneration);
  }

  function scheduleRefresh() {
    if (destroyed || reattachTimer !== null) return;
    reattachTimer = window.setTimeout(() => {
      reattachTimer = null;
      refresh();
    }, REATTACH_DELAY_MS);
  }

  function refresh() {
    if (destroyed) return;
    ensureEntry();
    mountActivePage();
    if (active && page?.isConnected && !frame?.isConnected && !preparePromise && statusView !== "error") {
      void prepareTaskboard(++openGeneration);
    }
    postHostContext();
  }

  function mount() {
    if (destroyed || observer || !document.documentElement) return;
    ensureEntry();
    observer = new MutationObserver(scheduleRefresh);
    observer.observe(document.documentElement, { childList: true, subtree: true, attributes: true, attributeFilter: ["class", "data-theme", "data-color-theme", "data-app-action-sidebar-thread-active", "aria-label", "aria-current"] });
    hostContextTimer = window.setInterval(postHostContext, 1_000);
    document.addEventListener("click", onDocumentClick, true);
    window.addEventListener("message", onFrameMessage);
    window.addEventListener("message", onHostBridgeMessage);
    window.addEventListener("popstate", onNativeRouteChange);
    window.addEventListener("hashchange", onNativeRouteChange);
  }

  function destroy() {
    if (destroyed) return;
    destroyed = true;
    if (reattachTimer !== null) window.clearTimeout(reattachTimer);
    if (hostContextTimer !== null) window.clearInterval(hostContextTimer);
    observer?.disconnect();
    cancelFrameReadyWaiters(new Error(hostText("任务面板已关闭", "Taskboard was closed")));
    hostRequests.forEach(({ reject, timer }) => { window.clearTimeout(timer); reject(new Error(hostText("任务面板已关闭", "Taskboard was closed"))); });
    hostRequests.clear();
    document.removeEventListener("click", onDocumentClick, true);
    window.removeEventListener("message", onFrameMessage);
    window.removeEventListener("message", onHostBridgeMessage);
    window.removeEventListener("popstate", onNativeRouteChange);
    window.removeEventListener("hashchange", onNativeRouteChange);
    closeTaskboard(false);
    document.querySelectorAll(`[${OWNED_ATTRIBUTE}="true"]`).forEach((node) => node.remove());
    document.getElementById(STYLE_ID)?.remove();
    if (window[SENTINEL_KEY] === api) delete window[SENTINEL_KEY];
  }

  const api = {
    version: VERSION,
    sourceHash: SOURCE_HASH,
    get ready() { return frameReady; },
    get state() {
      const rect = frame?.getBoundingClientRect();
      return { active, frameReady, statusView, generation: openGeneration,
        preparing: Boolean(preparePromise), pageConnected: Boolean(page?.isConnected),
        pageHidden: page?.hidden, frameConnected: Boolean(frame?.isConnected),
        frameHidden: frame?.hidden, width: rect?.width, height: rect?.height };
    },
    refresh,
    open: openTaskboard,
    close: closeTaskboard,
    destroy,
  };
  window[SENTINEL_KEY] = api;
  if (document.documentElement) mount();
  else document.addEventListener("DOMContentLoaded", mount, { once: true });
})();
