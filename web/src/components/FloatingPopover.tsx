import { useLayoutEffect, useRef, useState, type CSSProperties, type ReactNode, type RefObject } from "react";
import { createPortal } from "react-dom";

/** Portal to the frame body so composer overflow never clips a picker. */
export function FloatingPopover({ open, anchor, onClose, children, label, width = 300 }: {
  open: boolean;
  anchor: RefObject<HTMLElement | null>;
  onClose: () => void;
  children: ReactNode;
  label: string;
  width?: number;
}) {
  const panel = useRef<HTMLDivElement>(null);
  const close = useRef(onClose);
  close.current = onClose;
  const [position, setPosition] = useState<CSSProperties>({ visibility: "hidden" });
  useLayoutEffect(() => {
    if (!open || !anchor.current || !panel.current) return;
    const trigger = anchor.current;
    const popup = panel.current;
    const place = () => {
      if (!trigger.isConnected) { close.current(); return; }
      const bounds = trigger.getBoundingClientRect();
      const viewport = window.visualViewport;
      const edge = 8;
      const leftEdge = (viewport?.offsetLeft ?? 0) + edge;
      const topEdge = (viewport?.offsetTop ?? 0) + edge;
      const rightEdge = leftEdge + (viewport?.width ?? innerWidth) - edge * 2;
      const bottomEdge = topEdge + (viewport?.height ?? innerHeight) - edge * 2;
      const popupWidth = Math.min(Math.max(width, bounds.width), rightEdge - leftEdge);
      const below = Math.max(0, bottomEdge - bounds.bottom - 6);
      const above = Math.max(0, bounds.top - topEdge - 6);
      const desiredHeight = Math.min(popup.scrollHeight, 320);
      const useBelow = below >= desiredHeight || below >= above;
      const maxHeight = Math.max(0, Math.min(320, useBelow ? below : above));
      const height = Math.min(desiredHeight, maxHeight);
      setPosition({ left: Math.max(leftEdge, Math.min(bounds.left, rightEdge - popupWidth)),
        top: useBelow ? bounds.bottom + 6 : bounds.top - height - 6,
        width: popupWidth, maxHeight, visibility: "visible" });
    };
    place();
    const observer = new ResizeObserver(place);
    observer.observe(trigger);
    observer.observe(popup);
    const outside = (event: Event) => {
      if (!popup.contains(event.target as Node) && !trigger.contains(event.target as Node)) close.current();
    };
    const keyboard = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        event.stopImmediatePropagation();
        close.current();
        trigger.focus();
      }
    };
    document.addEventListener("pointerdown", outside, true);
    document.addEventListener("focusin", outside);
    document.addEventListener("keydown", keyboard, true);
    window.addEventListener("resize", place);
    window.addEventListener("scroll", place, true);
    window.visualViewport?.addEventListener("resize", place);
    popup.querySelector<HTMLElement>('input:not([type="checkbox"]), [aria-selected="true"], [data-popover-item]')?.focus({ preventScroll: true });
    return () => {
      observer.disconnect();
      document.removeEventListener("pointerdown", outside, true);
      document.removeEventListener("focusin", outside);
      document.removeEventListener("keydown", keyboard, true);
      window.removeEventListener("resize", place);
      window.removeEventListener("scroll", place, true);
      window.visualViewport?.removeEventListener("resize", place);
    };
  }, [open, anchor, width]);
  if (!open) return null;
  return createPortal(<div ref={panel} className="taskboard-popover" style={position} aria-label={label} onKeyDown={event => {
    if (!["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key)) return;
    if (event.target instanceof HTMLInputElement && event.target.type !== "checkbox" && ["Home", "End"].includes(event.key)) return;
    const items = Array.from(panel.current?.querySelectorAll<HTMLElement>("[data-popover-item]:not(:disabled)") ?? []);
    if (!items.length) return;
    event.preventDefault();
    const index = items.indexOf(document.activeElement as HTMLElement);
    const next = event.key === "Home" ? 0 : event.key === "End" ? items.length - 1 : (index + (event.key === "ArrowDown" ? 1 : -1) + items.length) % items.length;
    items[next].focus();
  }}>{children}</div>, document.body);
}
