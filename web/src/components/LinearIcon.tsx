import type { ReactNode, SVGProps } from "react";

/**
 * Small copy of the native Linear/Codex icon vocabulary used by the
 * taskboard shell.  Keeping the names and the data attribute from the
 * original UI makes the embedded surface look and behave like its source
 * without bringing the original product's feature modules back in.
 */
const ICONS = {
  plus: { content: <path d="M7.25 2.75a.75.75 0 0 1 1.5 0v4.5h4.5a.75.75 0 0 1 0 1.5h-4.5v4.5a.75.75 0 0 1-1.5 0v-4.5h-4.5a.75.75 0 0 1 0-1.5h4.5z" /> },
  alert: { content: <path fillRule="evenodd" d="M8 15A7 7 0 1 0 8 1a7 7 0 0 0 0 14M7.026 4.525a.5.5 0 0 1 .5-.525h.948a.5.5 0 0 1 .5.525l-.2 4a.5.5 0 0 1-.5.475h-.548a.5.5 0 0 1-.5-.475zM7 11a1 1 0 1 1 2 0 1 1 0 0 1-2 0" clipRule="evenodd" /> },
  check: { content: <path d="M6.336 13.6a1.049 1.049 0 0 1-.8-.376L2.632 9.736a.992.992 0 0 1 .152-1.424 1.056 1.056 0 0 1 1.456.152l2.008 2.4 5.448-8a1.048 1.048 0 0 1 1.432-.288A.992.992 0 0 1 13.424 4L7.2 13.144a1.04 1.04 0 0 1-.8.456h-.064Z" /> },
  chevronDown: { content: <path d="M4.53 5.47a.75.75 0 0 0-1.06 1.06l4 4a.75.75 0 0 0 1.054.007l4-3.903a.75.75 0 0 0-1.048-1.073l-3.47 3.385L4.53 5.47Z" /> },
  chevronLeft: { content: <path d="M10.7803 4.78033C11.0732 4.48744 11.0732 4.01256 10.7803 3.71967C10.4874 3.42678 10.0126 3.42678 9.71967 3.71967L5.71967 7.71967C5.42933 8.01001 5.42643 8.47986 5.71318 8.77376L9.61581 12.7738C9.90508 13.0702 10.3799 13.0761 10.6764 12.7868C10.9729 12.4976 10.9787 12.0227 10.6895 11.7262L7.30417 8.25649L10.7803 4.78033Z" /> },
  close: { content: <path d="m4 4 8 8M12 4l-8 8" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" /> },
  codexSidebarExpand: {
    viewBox: "0 0 20 20",
    content: <path d="M5 4h10a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2Zm1 2v8m7-8v8" />,
  },
  copy: { content: <><path fillRule="evenodd" d="M6.08 2.5v1.167h3.834V2.5H6.08Zm-1.5-.083C4.58 1.634 5.216 1 5.998 1h4c.783 0 1.417.634 1.417 1.417V3.75c0 .782-.634 1.417-1.417 1.417h-4A1.417 1.417 0 0 1 4.581 3.75V2.417Z" clipRule="evenodd" /><path fillRule="evenodd" d="M4.087 3.749a.583.583 0 0 0-.583.583l.001 8.583a.583.583 0 0 0 .584.584h7.82a.583.583 0 0 0 .583-.584V4.332a.583.583 0 0 0-.584-.583H11a.75.75 0 0 1 0-1.5h.909a2.083 2.083 0 0 1 2.083 2.083v8.583A2.083 2.083 0 0 1 11.908 15h-7.82a2.083 2.083 0 0 1-2.083-2.084l-.001-8.583A2.084 2.084 0 0 1 4.087 2.25H5a.75.75 0 1 1 0 1.5h-.913Z" clipRule="evenodd" /></> },
  displayOptions: { content: <><path fillRule="evenodd" clipRule="evenodd" d="M7 2.5C8.11933 2.5 9.06613 3.23584 9.38477 4.25H14.75C15.1642 4.25 15.5 4.58579 15.5 5C15.5 5.41421 15.1642 5.75 14.75 5.75H9.38477C9.06613 6.76416 8.11933 7.5 7 7.5C5.88067 7.5 4.93387 6.76416 4.61523 5.75H2.25C1.83579 5.75 1.5 5.41421 1.5 5C1.5 4.58579 1.83579 4.25 2.25 4.25H4.61523C4.93387 3.23584 5.88067 2.5 7 2.5ZM7 4C6.44772 4 6 4.44772 6 5C6 5.55228 6.44772 6 7 6C7.55228 6 8 5.55228 8 5C8 4.44772 7.55228 4 7 4Z" /><path fillRule="evenodd" clipRule="evenodd" d="M10 13.5C8.88067 13.5 7.93387 12.7642 7.61523 11.75H2.25C1.83579 11.75 1.5 11.4142 1.5 11C1.5 10.5858 1.83579 10.25 2.25 10.25H7.61523C7.93387 9.23584 8.88067 8.5 10 8.5C11.1193 8.5 12.0661 9.23584 12.3848 10.25H14.75C15.1642 10.25 15.5 10.5858 15.5 11C15.5 11.4142 15.1642 11.75 14.75 11.75H12.3848C12.0661 12.7642 11.1193 13.5 10 13.5ZM10 12C10.5523 12 11 11.5523 11 11C11 10.4477 10.5523 10 10 10C9.44772 10 9 10.4477 10 12Z" /></> },
  home: { content: <path fillRule="evenodd" d="M2.323 5.68A1 1 0 0 0 2 6.415v8.083a.5.5 0 0 0 .5.5h3a.5.5 0 0 0 .5-.5v-3.515a2 2 0 0 1 4 0v3.515a.5.5 0 0 0 .5.5h3a.5.5 0 0 0 .5-.5V6.416a1 1 0 0 0-.323-.737L9.015 1.396a1.5 1.5 0 0 0-2.03 0z" clipRule="evenodd" /> },
  more: { content: <path d="M3 6.5a1.5 1.5 0 1 1 0 3 1.5 1.5 0 0 1 0-3Zm5 0a1.5 1.5 0 1 1 0 3 1.5 1.5 0 0 1 0-3Zm5 0a1.5 1.5 0 1 1 0 3 1.5 1.5 0 0 1 0-3Z" /> },
  pause: { content: <path d="M3.5 3.5a1 1 0 0 1 1-1H6a1 1 0 0 1 1 1v9a1 1 0 0 1-1 1H4.5a1 1 0 0 1-1-1v-9ZM9 3.5a1 1 0 0 1 1-1h1.5a1 1 0 0 1 1 1v9a1 1 0 0 1-1 1H10a1 1 0 0 1-1-1v-9Z" /> },
  play: { content: <path d="m5.604 2.41 7.23 4.502a1.375 1.375 0 0 1-.02 2.345L5.585 13.6a1.375 1.375 0 0 1-2.083-1.18V3.576A1.375 1.375 0 0 1 5.604 2.41Z" /> },
  search: { content: <path fillRule="evenodd" clipRule="evenodd" d="M7 2C9.76142 2 12 4.23858 12 7C12 8.11012 11.6375 9.13519 11.0254 9.96484L13.7803 12.7197L13.832 12.7764C14.0723 13.0709 14.0549 13.5057 13.7803 13.7803C13.5057 14.0549 13.0709 14.0723 12.7764 13.832L12.7197 13.7803L9.96484 11.0254C9.13519 11.6375 8.11012 12 7 12C4.23858 12 2 9.76142 2 7C2 4.23858 4.23858 2 7 2ZM7 3.5C5.067 3.5 3.5 5.067 3.5 7C3.5 8.933 5.067 10.5 7 10.5C8.933 10.5 10.5 8.933 10.5 7C10.5 5.067 8.933 3.5 7 3.5Z" /> },
  terminal: { viewBox: "0 0 24 24", content: <g fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><rect width="18" height="18" x="3" y="3" rx="3" /><path d="m7 8 3 3-3 3" /><path d="M12 16h5" /></g> },
} satisfies Record<string, { viewBox?: string; content: ReactNode }>;

export type LinearIconName = keyof typeof ICONS;

export function LinearIcon({ name, title, style, ...props }: Omit<SVGProps<SVGSVGElement>, "children"> & { name: LinearIconName; title?: string }) {
  const icon: { viewBox?: string; content: ReactNode } = ICONS[name];
  return (
    <svg
      {...props}
      data-linear-icon={name}
      viewBox={icon.viewBox ?? "0 0 16 16"}
      width="1em"
      height="1em"
      fill="currentColor"
      focusable="false"
      role={title ? "img" : undefined}
      aria-hidden={title ? undefined : true}
      style={{ fill: "currentColor", stroke: "none", ...style }}
    >
      {title && <title>{title}</title>}
      {icon.content}
    </svg>
  );
}
