import { t, localizeError } from "../i18n";
import { useEffect, useRef, useState } from "react";
import "./DetailExecutionSettings.css";
import { FloatingPopover } from "./FloatingPopover";
import { LinearIcon } from "./LinearIcon";
import { listModels } from "../api";
import type { CodexModel, ExecutionOptions } from "../types";

const LABELS: Record<string, string> = { get none() { return t("无"); }, get minimal() { return t("最低"); }, get low() { return t("低"); }, get medium() { return t("中"); }, get high() { return t("高"); }, get xhigh() { return t("很高"); }, get max() { return t("最大"); }, get ultra() { return t("极高"); } };
type Field = "schedulingMode" | "model" | "reasoningEffort" | "executionMode" | "branch" | "targetBranch" | "writeScopes";

/** Commit one property at a time; only server-confirmed values appear in the sidebar. */
export function DetailExecutionSettings({ value, onChange, disabled = false, workspaceLocked = false, isChild = false, defaultTarget = null, scopeSummary }: {
  value: ExecutionOptions; onChange: (changes: Partial<ExecutionOptions>) => Promise<boolean>;
  disabled?: boolean; workspaceLocked?: boolean; isChild?: boolean; defaultTarget?: string | null; scopeSummary?: string[];
}) {
  const anchor = useRef<HTMLButtonElement | null>(null);
  const [page, setPage] = useState<Field | null>(null);
  const [draft, setDraft] = useState("");
  const [models, setModels] = useState<CodexModel[]>([]);
  const [loadError, setLoadError] = useState("");
  const [saveError, setSaveError] = useState("");
  const [saving, setSaving] = useState(false);
  const pending = useRef(false);
  const [loading, setLoading] = useState(false);
  const [attempt, setAttempt] = useState(0);
  const loaded = useRef(false);
  const automaticWorktree = isChild || value.schedulingMode === "parallel" || value.kind === "parallel_group";
  const selected = models.find(model => model.model === value.model);
  useEffect(() => {
    if (loaded.current) return;
    let active = true;
    setLoading(true);
    setLoadError("");
    void listModels().then(items => { if (active) { setModels(items); loaded.current = true; } })
      .catch(cause => { if (active) setLoadError(cause instanceof Error ? cause.message : t("无法读取模型")); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [attempt]);
  const modelDefault = isChild ? t("沿用任务组") : t("沿用 Codex 会话");
  const rows: { key: Field; label: string; text: string; changed: boolean; locked: boolean; visible: boolean }[] = [
    { key: "schedulingMode", label: t("执行方式"), text: value.schedulingMode === "parallel" ? t("允许并行") : t("独占执行"), changed: value.schedulingMode === "parallel", locked: workspaceLocked, visible: !isChild },
    { key: "model", label: t("模型"), text: value.model || modelDefault, changed: !!value.model, locked: false, visible: true },
    { key: "reasoningEffort", label: t("推理强度"), text: value.reasoningEffort ? LABELS[value.reasoningEffort] || value.reasoningEffort : value.model ? t("模型默认") : modelDefault, changed: !!value.reasoningEffort, locked: !value.model, visible: true },
    { key: "executionMode", label: t("执行位置"), text: automaticWorktree ? t("独立工作树（自动）") : value.executionMode === "worktree" ? t("新工作树") : t("当前项目目录"), changed: !automaticWorktree && value.executionMode === "worktree", locked: workspaceLocked || automaticWorktree, visible: true },
    { key: "branch", label: t("起始分支"), text: isChild ? t("任务组集成版本") : value.branch || (automaticWorktree ? t("合入目标的已提交版本") : t("当前工作区状态")), changed: !!value.branch, locked: workspaceLocked || isChild, visible: value.executionMode === "worktree" || automaticWorktree },
    { key: "targetBranch", label: t("合入目标"), text: value.targetBranch || t("请选择分支"), changed: !!value.targetBranch && value.targetBranch !== defaultTarget, locked: workspaceLocked || isChild, visible: automaticWorktree },
    { key: "writeScopes", label: t("修改范围"), text: (scopeSummary ?? value.writeScopes)?.length ? (scopeSummary ?? value.writeScopes ?? []).join(", ") : t("未指定"), changed: value.kind !== "parallel_group" && !!value.writeScopes?.length, locked: value.kind === "parallel_group", visible: true },
  ];
  const activeRow = rows.find(row => row.key === page);
  const blocked = disabled || saving || !!activeRow?.locked;
  const modelPage = page === "model" || page === "reasoningEffort";
  function enter(field: Field, trigger: HTMLButtonElement) {
    anchor.current = trigger;
    setPage(field);
    setSaveError("");
    const fieldValue = value[field];
    setDraft(Array.isArray(fieldValue) ? fieldValue.join("\n") : fieldValue || "");
  }
  function close() {
    if (!pending.current) setPage(null);
  }
  async function choose(field: Field, next: string | null) {
    if (blocked || pending.current) return;
    let changes: Partial<ExecutionOptions>;
    if (field === "model") {
      const model = models.find(item => item.model === next);
      changes = { model: next, reasoningEffort: model?.defaultReasoningEffort ?? null };
    } else if (field === "executionMode") {
      changes = { executionMode: next === "worktree" ? "worktree" : "local", branch: null };
    } else if (field === "schedulingMode") {
      changes = { schedulingMode: next === "parallel" ? "parallel" : "exclusive" };
    } else if (field === "writeScopes") {
      changes = { writeScopes: [...new Set((next || "").split(/\n/).map(path => path.trim()).filter(Boolean))] };
    } else changes = { [field]: next };
    pending.current = true;
    setSaving(true);
    setSaveError("");
    try {
      if (await onChange(changes)) {
        setPage(null);
        requestAnimationFrame(() => anchor.current?.focus({ preventScroll: true }));
      } else setSaveError(t("设置未保存，请重试。"));
    } catch (cause) {
      setSaveError(cause instanceof Error ? cause.message : t("设置未保存，请重试。"));
    } finally {
      pending.current = false;
      setSaving(false);
    }
  }
  const choices = page === "model" ? [{ value: "", label: modelDefault }, ...models.map(model => ({ value: model.model, label: model.displayName || model.model }))]
    : page === "reasoningEffort" ? [{ value: "", label: t("模型默认") }, ...(selected?.supportedReasoningEfforts ?? []).map(item => ({ value: item.reasoningEffort, label: `${LABELS[item.reasoningEffort] || item.reasoningEffort} · ${item.reasoningEffort}` }))]
    : page === "executionMode" ? [{ value: "local", label: t("当前项目目录") }, { value: "worktree", label: t("新工作树") }]
    : page === "schedulingMode" ? [{ value: "exclusive", label: t("独占执行") }, { value: "parallel", label: t("允许并行") }] : [];
  const selectedValue = page === "schedulingMode" ? value.schedulingMode ?? "exclusive" : page ? value[page] ?? "" : "";
  return <section className="detail-execution-settings" aria-label={t("更多设置")}>
    <div className="settings-rows">{rows.filter(row => row.visible).map(row => <button key={row.key} type="button" className="settings-row" disabled={disabled || saving || row.locked} onClick={event => enter(row.key, event.currentTarget)} aria-label={`${row.label}：${row.text}`} aria-haspopup="dialog" aria-expanded={page === row.key}>
      <span className="settings-row-label">{row.label}{row.changed && <i title={t("已自定义")} aria-label={t("已自定义")} />}</span><span className="settings-row-value" title={row.text}>{row.text}</span><LinearIcon name="chevronDown" />
    </button>)}</div>
    <FloatingPopover key={page ?? "closed"} open={!!page} anchor={anchor} onClose={close} label={activeRow?.label ?? t("更多设置")} width={350}>
      {page && <div className="detail-settings-popover" role="dialog" aria-label={activeRow?.label} aria-busy={saving}>
        <div className="settings-page-heading"><strong>{activeRow?.label}</strong><button type="button" aria-label={t("关闭")} disabled={saving} onClick={() => { close(); anchor.current?.focus(); }}><LinearIcon name="close" /></button></div>
        {choices.length > 0 ? <div className="settings-choices" role="listbox" aria-label={activeRow?.label}>
          {choices.map(choice => <button type="button" key={choice.value} data-popover-item role="option" aria-selected={selectedValue === choice.value} disabled={blocked}
            onClick={() => void choose(page, choice.value || null)}><span>{choice.label}</span>{selectedValue === choice.value && <LinearIcon name="check" />}</button>)}
        </div> : <form className="settings-value-editor" onSubmit={event => { event.preventDefault(); void choose(page, draft.trim() || null); }}>
          {page === "writeScopes" ? <><textarea aria-label={t("修改范围")} rows={4} value={draft} onChange={event => setDraft(event.target.value)} disabled={blocked} placeholder={t("每行一个文件或目录，例如 web/ 或 src/api.py")} /><small>{t("范围重叠的任务会排队；未声明范围的任务仍可能修改这些文件。")}</small></>
            : <input aria-label={activeRow?.label} value={draft} onChange={event => setDraft(event.target.value)} disabled={blocked} placeholder={t("输入已有分支名称")} onKeyDown={event => { if (event.key === "Enter" && event.nativeEvent.isComposing) event.preventDefault(); }} />}
          <button type="submit" className="button primary" disabled={blocked}>{saving ? t("正在保存…") : t("应用")}</button>
        </form>}
        {(page !== "targetBranch" || !!defaultTarget) && <button className="settings-reset" type="button" disabled={blocked} onClick={() => void choose(page, page === "executionMode" ? "local" : page === "schedulingMode" ? "exclusive" : page === "targetBranch" ? defaultTarget : null)}>{t("恢复默认")}</button>}
        {modelPage && loading && <small className="settings-note">{t("正在读取可用模型…")}</small>}
        {modelPage && loadError && <div className="settings-note" role="status">{localizeError(loadError)} <button type="button" onClick={() => setAttempt(attempt + 1)}>{t("重试")}</button></div>}
        {saveError && <p className="form-error" role="alert">{localizeError(saveError)}</p>}
      </div>}
    </FloatingPopover>
  </section>;
}
