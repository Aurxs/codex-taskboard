import { t, localizeError } from "../i18n";
import { useEffect, useState } from "react";
import { TaskPropertyPicker } from "./TaskPropertyPicker";
import { listModels } from "../api";
import type { CodexModel, ExecutionOptions } from "../types";

const LABELS: Record<string, string> = { get none() { return t("无"); }, get minimal() { return t("最低"); }, get low() { return t("低"); }, get medium() { return t("中"); }, get high() { return t("高"); }, get xhigh() { return t("很高"); }, get max() { return t("最大"); }, get ultra() { return t("极高"); } };

export function ExecutionSettings({ value, onChange, disabled = false, workspaceLocked = false, saveBranchOnBlur = true }: { value: ExecutionOptions; onChange: (value: ExecutionOptions) => void; disabled?: boolean; workspaceLocked?: boolean; saveBranchOnBlur?: boolean }) {
  const options: ExecutionOptions = { model: value.model, reasoningEffort: value.reasoningEffort, executionMode: value.executionMode, branch: value.branch };
  const [branchDraft, setBranchDraft] = useState(value.branch ?? "");
  useEffect(() => { setBranchDraft(value.branch ?? ""); }, [value.branch]);
  const [openPicker, setOpenPicker] = useState<"model" | "effort" | "workspace" | null>(null);
  const [models, setModels] = useState<CodexModel[]>([]);
  const [error, setError] = useState("");
  const [attempt, setAttempt] = useState(0);
  const [loading, setLoading] = useState(true);
  useEffect(() => {
    let active = true;
    setLoading(true);
    setError("");
    void listModels().then(items => { if (active) setModels(items); }).catch(cause => { if (active) setError(cause instanceof Error ? cause.message : t("无法读取模型")); }).finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [attempt]);
  const selected = models.find(item => item.model === value.model);
  return <div className="execution-settings">
    <div className="execution-setting-field"><span>{t("执行位置")}</span>
      <TaskPropertyPicker ariaLabel={t("执行位置")} value={value.executionMode} disabled={disabled || workspaceLocked}
        open={openPicker === "workspace"} onOpenChange={open => setOpenPicker(open ? "workspace" : null)}
        options={[{ value: "local", label: t("当前项目目录") }, { value: "worktree", label: t("新工作树") }]}
        onChange={mode => onChange({ ...options, executionMode: mode === "worktree" ? "worktree" : "local", branch: null })} />
    </div>
    {value.executionMode === "worktree" && <label className="execution-setting-field"><span>{t("起始分支")}</span>
      <input aria-label={t("起始分支")} value={branchDraft} disabled={disabled || workspaceLocked}
        placeholder={t("留空使用当前工作区状态")} onChange={event => { setBranchDraft(event.target.value); if (!saveBranchOnBlur) onChange({ ...options, branch: event.target.value.trim() || null }); }}
        onBlur={() => { const branch = branchDraft.trim() || null; if (branch !== value.branch) onChange({ ...options, branch }); }} />
    </label>}
    {value.executionMode === "worktree" && <small>{t("由 Codex 创建并管理工作树；可填写已有本地或远程分支，任务在该分支的独立工作树中执行。")}</small>}
    {workspaceLocked && <small>{t("执行位置和分支已锁定，重试将沿用原会话。")}</small>}
    <div className="execution-setting-field"><span>{t("模型")}</span>
      <TaskPropertyPicker ariaLabel={t("模型")} value={value.model ?? ""} disabled={disabled || loading}
        open={openPicker === "model"} onOpenChange={open => setOpenPicker(open ? "model" : null)}
        options={[{ value: "", get label() { return t("沿用 Codex 会话"); } }, ...(value.model && !selected ? [{ value: value.model, label: value.model }] : []), ...models.map(item => ({ value: item.model, label: item.displayName || item.model }))]}
        onChange={model => { const next = models.find(item => item.model === model); onChange({ ...options, model: next?.model ?? null, reasoningEffort: next?.defaultReasoningEffort ?? null }); }} />
    </div>
    <div className="execution-setting-field"><span>{t("推理强度")}</span>
      <TaskPropertyPicker ariaLabel={t("推理强度")} value={value.reasoningEffort ?? ""} disabled={disabled || !selected || loading}
        open={openPicker === "effort"} onOpenChange={open => setOpenPicker(open ? "effort" : null)}
        options={[{ value: "", label: selected ? t("模型默认") : t("沿用 Codex 会话") }, ...(selected?.supportedReasoningEfforts.map(item => ({ value: item.reasoningEffort, label: `${LABELS[item.reasoningEffort] || item.reasoningEffort} · ${item.reasoningEffort}` })) ?? [])]}
        onChange={effort => onChange({ ...options, model: value.model, reasoningEffort: effort || null })} />
    </div>
    {loading && <small>{t("正在读取可用模型…")}</small>}
    {error && <div className="model-load-error" role="status">{localizeError(error)} <button type="button" onClick={() => setAttempt(value => value + 1)}>{t("重试")}</button></div>}
    {disabled && <small>{t("暂停任务后可修改；下次执行生效。")}</small>}
  </div>;
}
