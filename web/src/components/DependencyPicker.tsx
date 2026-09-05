import { useRef, useState } from "react";
import { FloatingPopover } from "./FloatingPopover";
import { LinearIcon } from "./LinearIcon";
import type { Task } from "../types";

export function DependencyPicker({ candidates, value, onChange }: { candidates: Task[]; value: string[]; onChange: (ids: string[]) => void }) {
  const [open, setOpen] = useState(false);
  const trigger = useRef<HTMLButtonElement>(null);
  const [search, setSearch] = useState("");
  const available = candidates.filter(task => `${task.identifier} ${task.title}`.toLowerCase().includes(search.toLowerCase()));
  return <div className="dependency-picker">
    <button ref={trigger} type="button" className="property-control dependency-trigger" aria-label="阻塞于" aria-haspopup="dialog" aria-expanded={open} onClick={() => setOpen(!open)}>阻塞于 <span>{value.length || "无"}</span><LinearIcon name="chevronDown" className="picker-chevron" /></button>
    <FloatingPopover open={open} anchor={trigger} onClose={() => setOpen(false)} label="选择前置任务" width={340}>
    <div className="dependency-picker-panel" role="dialog" aria-label="选择前置任务">
      <input className="dependency-search" aria-label="搜索前置任务" placeholder="搜索任务…" value={search} onChange={event => setSearch(event.target.value)} />
      <div className="dependency-picker-list" aria-label="可选前置任务">
        {available.map(task => <label className="dependency-option" key={task.id}>
          <input data-popover-item type="checkbox" checked={value.includes(task.id)} onChange={event => onChange(event.target.checked ? [...value, task.id] : value.filter(id => id !== task.id))} />
          <span><small>{task.identifier}</small><span>{task.title}</span></span>
        </label>)}
        {!available.length && <p className="issue-relation-empty">{candidates.length ? "没有匹配的任务。" : "没有可选的前置任务。"}</p>}
      </div>
    </div>
    </FloatingPopover>
  </div>;
}
