import { t } from "../i18n";
import { useRef, type ClipboardEvent } from "react";
import type { AttachmentInput } from "../types";

export function pastedFiles(event: ClipboardEvent): File[] {
  const files = Array.from(event.clipboardData.files);
  if (files.length) event.preventDefault();
  return files;
}

export function validateAttachments(existing: { size: number }[], added: File[]) {
  const all = [...existing, ...added];
  if (all.length > 10 || all.some(file => file.size > 10 * 1024 * 1024) || all.reduce((sum, file) => sum + file.size, 0) > 20 * 1024 * 1024) {
    throw new Error(t("最多 10 个附件，单个最多 10 MB，合计最多 20 MB。"));
  }
  if (added.some(file => !/\.(png|jpe?g|gif|webp|md|markdown|txt|pdf|pptx?|docx?|xlsx?)$/i.test(file.name))) throw new Error(t("仅支持图片、Markdown、文本、PDF 和 Office 文档。"));
}

export function readAttachments(files: File[]): Promise<AttachmentInput[]> {
  return Promise.all(files.map(file => new Promise<AttachmentInput>((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve({ name: file.name, content: String(reader.result).split(",")[1] });
    reader.onerror = () => reject(new Error(t("无法读取 {0}", file.name)));
    reader.readAsDataURL(file);
  })));
}

export function AttachmentButton({ count, disabled, onAdd }: { count: number; disabled: boolean; onAdd: (files: File[]) => void }) {
  const input = useRef<HTMLInputElement>(null);
  return <>
    <button className="property-control attachment-trigger" type="button" disabled={disabled} onClick={() => input.current?.click()} title={t("添加图片、Markdown、PDF 或 Office 文档；也可在内容区域直接粘贴")}>{t("添加附件")} <span aria-live="polite">{count}</span></button>
    <input ref={input} type="file" hidden multiple accept=".png,.jpg,.jpeg,.gif,.webp,.md,.markdown,.txt,.pdf,.ppt,.pptx,.doc,.docx,.xls,.xlsx" disabled={disabled} onChange={event => {
      const files = Array.from(event.target.files ?? []);
      event.target.value = "";
      if (files.length) onAdd(files);
    }} />
  </>;
}
