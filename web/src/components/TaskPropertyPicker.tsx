import { useRef } from "react";
import { FloatingPopover } from "./FloatingPopover";
import { LinearIcon } from "./LinearIcon";

export interface TaskPropertyOption {
  value: string;
  label: string;
  icon?: React.ReactNode;
  className?: string;
}

export function TaskPropertyPicker({
  value,
  options,
  open,
  disabled = false,
  className = "",
  triggerClassName = "",
  triggerContent,
  ariaLabel,
  title,
  onOpenChange,
  onChange,
}: {
  value: string;
  options: TaskPropertyOption[];
  open: boolean;
  disabled?: boolean;
  className?: string;
  triggerClassName?: string;
  triggerContent?: React.ReactNode;
  ariaLabel: string;
  title?: string;
  onOpenChange: (open: boolean) => void;
  onChange: (value: string) => void;
}) {
  const triggerRef = useRef<HTMLButtonElement>(null);
  const selected = options.find((option) => option.value === value) ?? options[0];
  return (
    <div className={`task-property-picker ${className}`}>
      <button
        ref={triggerRef}
        className={`property-control task-property-trigger ${triggerClassName}`}
        type="button"
        disabled={disabled}
        aria-label={ariaLabel}
        title={title}
        aria-haspopup="listbox"
        aria-expanded={open}
        onClick={() => onOpenChange(!open)}
      >
        {triggerContent ?? selected?.icon ?? null}
        {!triggerContent && selected && <span className="task-property-trigger-label">{selected.label}</span>}
        <LinearIcon name="chevronDown" className="picker-chevron" />
      </button>
      <FloatingPopover open={open} anchor={triggerRef} onClose={() => onOpenChange(false)} label={ariaLabel}>
        <div className="picker-listbox" role="listbox" aria-label={ariaLabel}>
          <div className="task-property-options">
            {options.map((option) => (
              <button
                className={`picker-option ${option.className ?? ""}`}
                data-popover-item
                key={option.value}
                type="button"
                role="option"
                aria-selected={option.value === value}
                onClick={() => {
                  onChange(option.value);
                  onOpenChange(false);
                  triggerRef.current?.focus();
                }}
              >
                <span className="task-property-option-icon">{option.icon}</span>
                <span className="task-property-option-label">{option.label}</span>
                {option.value === value && <span className="task-property-option-check"><LinearIcon name="check" /></span>}
              </button>
            ))}
          </div>
        </div>
      </FloatingPopover>
    </div>
  );
}
