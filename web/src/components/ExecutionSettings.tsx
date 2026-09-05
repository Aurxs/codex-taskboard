import { useEffect, useState } from "react";
import { TaskPropertyPicker } from "./TaskPropertyPicker";
import { listModels } from "../api";
import type { CodexModel, ExecutionOptions } from "../types";

const LABELS: Record<string, string> = { none: "无", minimal: "最低", low: "低", medium: "中", high: "高", xhigh: "很高", max: "最大", ultra: "极高" };

export function ExecutionSettings({ value, onChange, disabled = false }: { value: ExecutionOptions; onChange: (value: ExecutionOptions) => void; disabled?: boolean }) {
  const [openPicker, setOpenPicker] = useState<"model" | "effort" | null>(null);
  const [models, setModels] = useState<CodexModel[]>([]);
  const [error, setError] = useState("");
  const [attempt, setAttempt] = useState(0);
  const [loading, setLoading] = useState(true);
  useEffect(() => {
    let active = true;
    setLoading(true);
    setError("");
    void listModels().then(items => { if (active) setModels(items); }).catch(cause => { if (active) setError(cause instanceof Error ? cause.message : "无法读取模型"); }).finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [attempt]);
  const selected = models.find(item => item.model === value.model);
  return <div className="execution-settings">
    <div className="execution-setting-field"><span>模型</span>
      <TaskPropertyPicker ariaLabel="模型" value={value.model ?? ""} disabled={disabled || loading}
        open={openPicker === "model"} onOpenChange={open => setOpenPicker(open ? "model" : null)}
        options={[{ value: "", label: "沿用 Codex 会话" }, ...(value.model && !selected ? [{ value: value.model, label: value.model }] : []), ...models.map(item => ({ value: item.model, label: item.displayName || item.model }))]}
        onChange={model => { const next = models.find(item => item.model === model); onChange({ model: next?.model ?? null, reasoningEffort: next?.defaultReasoningEffort ?? null }); }} />
    </div>
    <div className="execution-setting-field"><span>推理强度</span>
      <TaskPropertyPicker ariaLabel="推理强度" value={value.reasoningEffort ?? ""} disabled={disabled || !selected || loading}
        open={openPicker === "effort"} onOpenChange={open => setOpenPicker(open ? "effort" : null)}
        options={[{ value: "", label: selected ? "模型默认" : "沿用 Codex 会话" }, ...(selected?.supportedReasoningEfforts.map(item => ({ value: item.reasoningEffort, label: `${LABELS[item.reasoningEffort] || item.reasoningEffort} · ${item.reasoningEffort}` })) ?? [])]}
        onChange={effort => onChange({ ...value, reasoningEffort: effort || null })} />
    </div>
    {loading && <small>正在读取可用模型…</small>}
    {error && <div className="model-load-error" role="status">{error} <button type="button" onClick={() => setAttempt(value => value + 1)}>重试</button></div>}
    {disabled && <small>暂停任务后可修改；下次执行生效。</small>}
  </div>;
}
