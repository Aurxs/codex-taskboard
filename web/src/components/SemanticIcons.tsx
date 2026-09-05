import type { CSSProperties, HTMLAttributes, ReactNode, SVGProps } from "react";
import conversationSource from "../assets/figma-taskboard/conversation.svg";
import prioritySource from "../assets/figma-taskboard/priority.svg";
import priorityHighSource from "../assets/figma-taskboard/priority-high.svg";
import priorityLowSource from "../assets/figma-taskboard/priority-low.svg";
import priorityMediumSource from "../assets/figma-taskboard/priority-medium.svg";
import priorityNoneSource from "../assets/figma-taskboard/priority-none.svg";
import priorityUrgentSource from "../assets/figma-taskboard/priority-urgent.svg";
import relationBlockedBySource from "../assets/figma-taskboard/relation-blocked-by.svg";
import relationBlocksSource from "../assets/figma-taskboard/relation-blocks.svg";
import statusDoneSource from "../assets/figma-taskboard/status-done.svg";
import statusProgressSource from "../assets/figma-taskboard/status-progress.svg";
import statusReviewSource from "../assets/figma-taskboard/status-review.svg";
import statusTodoSource from "../assets/figma-taskboard/status-todo.svg";
import type { TaskPriority, TaskStatus } from "../types";

type IconSize = number | string;

interface SvgIconProps extends Omit<SVGProps<SVGSVGElement>, "children" | "color" | "height" | "width"> {
  children: ReactNode;
  color?: CSSProperties["color"];
  size?: IconSize;
}

function SvgIcon({ children, color = "currentColor", size = 16, style, ...props }: SvgIconProps) {
  return <svg {...props} viewBox="0 0 16 16" width={size} height={size} fill="currentColor" color={color} focusable="false" aria-hidden="true" style={{ color, fill: "currentColor", stroke: "none", ...style }}>{children}</svg>;
}

interface MaskIconProps extends Omit<HTMLAttributes<HTMLSpanElement>, "color"> {
  color?: CSSProperties["color"];
  size?: IconSize;
  source: string;
}

function MaskIcon({ color = "currentColor", size = 16, source, style, ...props }: MaskIconProps) {
  return <span {...props} className={`taskboard-icon${props.className ? ` ${props.className}` : ""}`} aria-hidden="true" style={{ backgroundColor: "currentColor", color, display: "inline-block", flex: "0 0 auto", height: size, maskImage: `url("${source}")`, maskPosition: "center", maskRepeat: "no-repeat", maskSize: "contain", WebkitMaskImage: `url("${source}")`, WebkitMaskPosition: "center", WebkitMaskRepeat: "no-repeat", WebkitMaskSize: "contain", width: size, ...style }} />;
}

const STATUS_SOURCES: Record<TaskStatus, string> = {
  todo: statusTodoSource,
  in_progress: statusProgressSource,
  in_review: statusReviewSource,
  done: statusDoneSource,
};

const STATUS_COLORS: Record<TaskStatus, string> = {
  todo: "var(--status-todo)",
  in_progress: "var(--status-progress)",
  in_review: "var(--status-review)",
  done: "var(--status-done)",
};

export function StatusIcon({ status, color, ...props }: Omit<MaskIconProps, "source"> & { status: TaskStatus }) {
  return <MaskIcon {...props} color={color ?? STATUS_COLORS[status]} data-status-icon={status} source={STATUS_SOURCES[status]} />;
}

const PRIORITY_SOURCES: Record<TaskPriority, string> = {
  urgent: priorityUrgentSource,
  high: priorityHighSource,
  medium: priorityMediumSource,
  low: priorityLowSource,
  none: priorityNoneSource,
};

export function PriorityIcon({ priority, color, ...props }: Omit<MaskIconProps, "source"> & { priority?: TaskPriority }) {
  return <MaskIcon {...props} color={color ?? (priority ? `var(--priority-${priority})` : "currentColor")} data-priority-icon={priority ?? "generic"} source={priority ? PRIORITY_SOURCES[priority] : prioritySource} />;
}

type BasicIconProps = Omit<SvgIconProps, "children">;

export function PlusIcon(props: BasicIconProps) { return <SvgIcon {...props}><path d="M7.25 3h1.5v4.25H13v1.5H8.75V13h-1.5V8.75H3v-1.5h4.25z" /></SvgIcon>; }
export function ProjectIcon(props: BasicIconProps) { return <SvgIcon {...props}><path fillRule="evenodd" d="M7.331 1.07a3.2 3.2 0 0 1 1.338 0c.498.106.967.377 1.904.917l1.354.78c.937.541 1.406.812 1.747 1.19.301.334.53.728.669 1.156.157.484.157 1.025.157 2.107v1.56l-.003.718c-.007.63-.036 1.026-.154 1.389l-.057.158a3.2 3.2 0 0 1-.612.998l-.135.138c-.33.312-.792.578-1.612 1.051l-1.354.78-.623.357c-.55.309-.907.481-1.281.56l-.166.032a3.2 3.2 0 0 1-1.006 0l-.166-.031c-.374-.08-.73-.252-1.281-.561l-.623-.356-1.354-.78c-.82-.474-1.281-.74-1.612-1.052l-.135-.138a3.2 3.2 0 0 1-.612-.998l-.057-.158c-.118-.363-.147-.758-.154-1.39L1.5 8.78V7.22c0-.946 0-1.479.105-1.921l.052-.186c.122-.374.312-.723.56-1.028l.11-.128c.255-.284.583-.507 1.126-.83l.62-.36 1.354-.78c.82-.473 1.281-.739 1.718-.869zM3 7.22v1.56c0 1.183.018 1.439.084 1.643l.064.167q.11.246.292.449l.059.06c.151.143.427.318 1.323.835l1.354.78.632.36c.188.104.33.178.442.233V8.482l-4.247-1.93zm5.75 1.262v4.826c.212-.106.533-.282 1.074-.594l1.354-.78.628-.368c.499-.297.646-.407.754-.527l.113-.14q.158-.218.243-.476l.022-.081c.035-.144.051-.351.058-.835L13 8.78V7.22l-.004-.668zM7.82 2.51l-.177.027c-.159.034-.328.106-.835.39l-.632.359-1.354.78c-.896.517-1.172.692-1.323.834l-.059.06q-.046.051-.086.104l4.645 2.112 4.645-2.112-.084-.103c-.109-.12-.255-.23-.754-.528l-.628-.367-1.354-.78c-.897-.517-1.186-.668-1.386-.728l-.08-.021a1.7 1.7 0 0 0-.538-.027" clipRule="evenodd" /></SvgIcon>; }
export function ConversationIcon(props: Omit<MaskIconProps, "source">) { return <MaskIcon {...props} source={conversationSource} />; }
export function BlockingRelationIcon({ type, ...props }: Omit<MaskIconProps, "source"> & { type: "blocked_by" | "blocks" }) { return <MaskIcon {...props} source={type === "blocked_by" ? relationBlockedBySource : relationBlocksSource} />; }
export function DueDateIcon(props: BasicIconProps) { return <SvgIcon {...props}><path fillRule="evenodd" clipRule="evenodd" d="M15 5C15 2.79086 13.2091 1 11 1H5C2.79086 1 1 2.79086 1 5V11C1 13.2091 2.79086 15 5 15H6.25C6.66421 15 7 14.6642 7 14.25C7 13.8358 6.66421 13.5 6.25 13.5H5C3.61929 13.5 2.5 12.3807 2.5 11V6H13.5V6.25C13.5 6.66421 13.8358 7 14.25 7C14.6642 7 15 6.66421 15 6.25V5Z" /></SvgIcon>; }
export function RefreshIcon(props: BasicIconProps) { return <SvgIcon {...props}><path fillRule="evenodd" d="M13.5 8a5.5 5.5 0 1 1-1.61-3.89l.61.6V3h1.5v5h-5V6.5h2.44l-.61-.61A4 4 0 1 0 12 8h1.5Z" /></SvgIcon>; }
export function RelationIcon(props: BasicIconProps) { return <SvgIcon {...props}><path d="m12.25 9.4.7-.7a3.5 3.5 0 0 0-4.95-4.95l-.7.7M3.75 6.6l-.7.7A3.5 3.5 0 0 0 8 12.25l.7-.7M10.1 5.9 5.9 10.1" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" /></SvgIcon>; }
