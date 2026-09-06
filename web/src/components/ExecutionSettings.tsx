import { t, localizeError } from "../i18n";
import { useEffect, useRef, useState } from "react";
import { FloatingPopover } from "./FloatingPopover";
import { LinearIcon } from "./LinearIcon";
import { listModels } from "../api";
import type { CodexModel, ExecutionOptions } from "../types";

const LABELS: Record<string, string> = { get none() { return t("无"); }, get minimal() { return t("最低"); }, get low() { return t("低"); }, get medium() { return t("中"); }, get high() { return t("高"); }, get xhigh() { return t("很高"); }, get max() { return t("最大"); }, get ultra() { return t("极高"); } };
type Field = "model" | "reasoningEffort" | "executionMode" | "branch" | "targetBranch" | "writeScopes";

/** One bounded popover: search settings, edit in-place, and return without nested dialogs. */
export function ExecutionSettings({ value, onChange, disabled = false, workspaceLocked = false, isChild = false, errorMessage, defaultTarget = null, scopeSummary }: {
  value: ExecutionOptions; onChange: (value: ExecutionOptions) => void;
  disabled?: boolean; workspaceLocked?: boolean; saveBranchOnBlur?: boolean; isChild?: boolean; errorMessage?: string | null; defaultTarget?: string | null; scopeSummary?: string[];
}) {
  const anchor = useRef<HTMLButtonElement>(null);
  const [open, setOpen] = useState(false);
  const [page, setPage] = useState<Field | null>(null);
  const [query, setQuery] = useState("");
  const [draft, setDraft] = useState("");
  const [models, setModels] = useState<CodexModel[]>([]);
  const [loadError, setLoadError] = useState("");
  const [loading, setLoading] = useState(false);
  const [attempt, setAttempt] = useState(0);
  const loaded = useRef(false);
  const options: ExecutionOptions = { model: value.model, reasoningEffort: value.reasoningEffort,
    executionMode: value.executionMode, branch: value.branch, kind: value.kind ?? "task",
    schedulingMode: value.schedulingMode ?? "exclusive", writeScopes: value.writeScopes ?? [], targetBranch: value.targetBranch ?? null };
  const automaticWorktree = isChild || options.schedulingMode === "parallel" || options.kind === "parallel_group";
  const selected = models.find(model => model.model === value.model);
  useEffect(() => {
    if (!open || loaded.current) return;
    let active = true;
    setLoading(true);
    setLoadError("");
    void listModels().then(items => { if (active) { setModels(items); loaded.current = true; } })
      .catch(cause => { if (active) setLoadError(cause instanceof Error ? cause.message : t("无法读取模型")); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [open, attempt]);
  useEffect(() => {
    if (errorMessage && /model|branch|scope|模型|推理|分支|范围|目标/i.test(errorMessage)) {
      const field: Field = /scope|范围/i.test(errorMessage) ? "writeScopes" : /model|模型/i.test(errorMessage) ? "model" : /推理|reasoning/i.test(errorMessage) ? "reasoningEffort" : /目标|target/i.test(errorMessage) ? "targetBranch" : "branch";
      setOpen(true); setPage(field); setQuery("");
      const fieldValue = value[field];
      setDraft(Array.isArray(fieldValue) ? fieldValue.join("\n") : fieldValue || "");
    }
  }, [errorMessage]);
  const modelDefault = isChild ? t("沿用任务组") : t("沿用 Codex 会话");
  const rows: { key: Field; label: string; text: string; changed: boolean; locked: boolean; visible: boolean }[] = [
    { key: "model", label: t("模型"), text: value.model || modelDefault, changed: !!value.model, locked: false, visible: true },
    { key: "reasoningEffort", label: t("推理强度"), text: value.reasoningEffort ? LABELS[value.reasoningEffort] || value.reasoningEffort : value.model ? t("模型默认") : modelDefault, changed: !!value.reasoningEffort, locked: !value.model, visible: true },
    { key: "executionMode", label: t("执行位置"), text: automaticWorktree ? t("独立工作树（自动）") : value.executionMode === "worktree" ? t("新工作树") : t("当前项目目录"), changed: !automaticWorktree && value.executionMode === "worktree", locked: workspaceLocked || automaticWorktree, visible: true },
    { key: "branch", label: t("起始分支"), text: isChild ? t("任务组集成版本") : value.branch || (automaticWorktree ? t("合入目标的已提交版本") : t("当前工作区状态")), changed: !!value.branch, locked: workspaceLocked || isChild, visible: value.executionMode === "worktree" || automaticWorktree },
    { key: "targetBranch", label: t("合入目标"), text: value.targetBranch || t("请选择分支"), changed: !!value.targetBranch && value.targetBranch !== defaultTarget, locked: workspaceLocked || isChild, visible: automaticWorktree },
    { key: "writeScopes", label: t("修改范围"), text: (scopeSummary ?? value.writeScopes)?.length ? (scopeSummary ?? value.writeScopes ?? []).join(", ") : t("未指定"), changed: options.kind !== "parallel_group" && !!value.writeScopes?.length, locked: options.kind === "parallel_group", visible: true },
  ];
  const activeRow = rows.find(row => row.key === page);
  function enter(field: Field) {
    setPage(field);
    const fieldValue = options[field];
    setDraft(Array.isArray(fieldValue) ? fieldValue.join("\n") : fieldValue || "");
  }
  function choose(field: Field, next: string | null) {
    if (field === "model") {
      const model = models.find(item => item.model === next);
      onChange({ ...options, model: next, reasoningEffort: model?.defaultReasoningEffort ?? null });
    } else if (field === "executionMode") {
      onChange({ ...options, executionMode: next === "worktree" ? "worktree" : "local", branch: null });
    } else if (field === "writeScopes") {
      onChange({ ...options, writeScopes: [...new Set((next || "").split(/\n/).map(path => path.trim()).filter(Boolean))] });
    } else onChange({ ...options, [field]: next });
    setPage(null);
    setQuery("");
  }
  function reset() {
    const next = { ...options, model: null, reasoningEffort: null, writeScopes: options.kind === "parallel_group" ? options.writeScopes : [] };
    if (!workspaceLocked) { next.branch = null; if (!isChild) next.targetBranch = defaultTarget; if (!automaticWorktree) next.executionMode = "local"; }
    onChange(next);
    setPage(null);
  }
  const choices = page === "model" ? [{ value: "", label: modelDefault }, ...models.map(model => ({ value: model.model, label: model.displayName || model.model }))]
    : page === "reasoningEffort" ? [{ value: "", label: t("模型默认") }, ...(selected?.supportedReasoningEfforts ?? []).map(item => ({ value: item.reasoningEffort, label: `${LABELS[item.reasoningEffort] || item.reasoningEffort} · ${item.reasoningEffort}` }))]
    : page === "executionMode" ? [{ value: "local", label: t("当前项目目录") }, { value: "worktree", label: t("新工作树") }] : [];
  return <div className="execution-settings compact-settings">
    <button ref={anchor} type="button" className="property-control more-settings-trigger" aria-label={t("更多")} aria-haspopup="dialog" aria-expanded={open}
      onClick={() => { setOpen(!open); setPage(null); setQuery(""); }}><span>{t("更多")}</span>{errorMessage && <span className="settings-error-dot" aria-label={t("设置需要处理")}>!</span>}<LinearIcon name="chevronDown" /></button>
    <FloatingPopover open={open} anchor={anchor} onClose={() => setOpen(false)} label={t("更多设置")} width={350}
      onEscape={() => { if (page) { setPage(null); return true; } return false; }}>
      <div className="more-settings-panel" role="dialog" aria-label={t("更多设置")}>
        {page ? <>
          <div className="settings-page-heading"><button type="button" aria-label={t("返回设置列表")} onClick={() => setPage(null)}><LinearIcon name="chevronLeft" /></button><strong>{activeRow?.label}</strong></div>
          {choices.length > 0 ? <div className="settings-choices" role="listbox" aria-label={activeRow?.label}>
            {choices.map(choice => <button type="button" key={choice.value} data-popover-item role="option" aria-selected={(options[page] ?? "") === choice.value} disabled={disabled || activeRow?.locked}
              onClick={() => choose(page, choice.value || null)}><span>{choice.label}</span>{(options[page] ?? "") === choice.value && <LinearIcon name="check" />}</button>)}
          </div> : <div className="settings-value-editor">
            {page === "writeScopes" ? <><textarea aria-label={t("修改范围")} rows={4} value={draft} onChange={event => { setDraft(event.target.value); onChange({ ...options, writeScopes: event.target.value.split("\n").filter(Boolean) }); }} disabled={disabled} placeholder={t("每行一个文件或目录，例如 web/ 或 src/api.py")} /><small>{t("范围重叠的任务会排队；未声明范围的任务仍可能修改这些文件。")}</small></>
              : <input aria-label={activeRow?.label} value={draft} onChange={event => { setDraft(event.target.value); onChange({ ...options, [page]: event.target.value || null }); }} disabled={disabled || activeRow?.locked} placeholder={t("输入已有分支名称")} onKeyDown={event => { if (event.key === "Enter") { event.preventDefault(); choose(page, draft.trim() || null); } }} />}
            <button type="button" className="button primary" disabled={disabled || activeRow?.locked} onClick={() => choose(page, draft.trim() || null)}>{t("应用")}</button>
          </div>}
          {!disabled && !activeRow?.locked && (page !== "targetBranch" || !!defaultTarget) && <button className="settings-reset" type="button" onClick={() => choose(page, page === "executionMode" ? "local" : page === "targetBranch" ? defaultTarget : null)}>{t("恢复默认")}</button>}
        </> : <>
          <input className="settings-search" aria-label={t("搜索参数")} placeholder={t("搜索参数…")} value={query} onChange={event => setQuery(event.target.value)} />
          <div className="settings-rows">{rows.filter(row => row.visible && `${row.label} ${row.text}`.toLocaleLowerCase().includes(query.toLocaleLowerCase())).map(row => <button key={row.key} type="button" data-popover-item className="settings-row" disabled={disabled || row.locked} onClick={() => enter(row.key)} aria-label={`${row.label}：${row.text}`}>
            <span className="settings-row-label">{row.label}{row.changed && <i title={t("已自定义")} aria-label={t("已自定义")} />}</span><span className="settings-row-value" title={row.text}>{row.text}</span><LinearIcon name="chevronLeft" />
          </button>)}</div>
          <button className="settings-reset" type="button" disabled={disabled} onClick={reset}>{t("恢复默认")}</button>
        </>}
        {loading && <small className="settings-note">{t("正在读取可用模型…")}</small>}
        {loadError && <div className="settings-note" role="status">{localizeError(loadError)} <button type="button" onClick={() => { loaded.current = false; setAttempt(attempt + 1); }}>{t("重试")}</button></div>}
        {disabled && <small className="settings-note">{t("暂停任务后可修改；下次执行生效。")}</small>}
        {workspaceLocked && page && <small className="settings-note">{t("执行位置和分支已锁定，重试将沿用原会话。")}</small>}
        {errorMessage && <p className="form-error" role="alert">{localizeError(errorMessage)}</p>}
      </div>
    </FloatingPopover>
  </div>;
}
