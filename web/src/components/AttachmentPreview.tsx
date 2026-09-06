import { t, localizeError } from "../i18n";
import { useEffect, useRef, useState, type ReactNode } from "react";
import { openAttachment, previewAttachment } from "../api";
import type { TaskAttachment } from "../types";

// Render common Markdown as React nodes; attachment HTML never executes.
function inline(text: string): ReactNode[] {
  return text.split(/(`[^`]+`|\*\*[^*]+\*\*|\*[^*]+\*)/g).map((part, i) =>
    part.startsWith("`") ? <code key={i}>{part.slice(1, -1)}</code> :
    part.startsWith("**") ? <strong key={i}>{part.slice(2, -2)}</strong> :
    part.startsWith("*") ? <em key={i}>{part.slice(1, -1)}</em> : part);
}

function Markdown({ text }: { text: string }) {
  return <div className="attachment-markdown">{text.split(/(```[^\n]*\n[\s\S]*?(?:```|$))/g).map((block, index) => {
    if (block.startsWith("```")) return <pre key={index}><code>{block.replace(/^```[^\n]*\n/, "").replace(/```$/, "")}</code></pre>;
    return <div key={index}>{block.split(/\r?\n/).map((line, i) => {
      const heading = line.match(/^(#{1,6})\s+(.*)$/);
      if (heading) return <div role="heading" aria-level={heading[1].length} className={`markdown-heading level-${heading[1].length}`} key={i}>{inline(heading[2])}</div>;
      if (/^\s*([-*+] |\d+\. )/.test(line)) return <div className="markdown-list-item" key={i}>{inline(line)}</div>;
      if (line.startsWith("> ")) return <blockquote key={i}>{inline(line.slice(2))}</blockquote>;
      return <p key={i}>{inline(line) || "\u00a0"}</p>;
    })}</div>;
  })}</div>;
}

export function AttachmentPreview({ file, onClose }: { file: TaskAttachment; onClose: () => void }) {
  const dialog = useRef<HTMLDialogElement>(null);
  const [preview, setPreview] = useState<Awaited<ReturnType<typeof previewAttachment>> | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    dialog.current?.showModal();
    let active = true;
    previewAttachment(file.id).then(value => { if (active) setPreview(value); })
      .catch(cause => { if (active) setError(cause instanceof Error ? cause.message : t("附件读取失败")); });
    return () => { active = false; };
  }, [file.id]);
  async function open() {
    setBusy(true);
    setError("");
    try { await openAttachment(file.id); onClose(); }
    catch (cause) { setError(cause instanceof Error ? cause.message : t("附件打开失败")); }
    finally { setBusy(false); }
  }
  return <dialog ref={dialog} className="attachment-preview" aria-labelledby="attachment-preview-title" onCancel={onClose} onClick={event => { if (event.target === event.currentTarget) onClose(); }}>
    <header><h2 id="attachment-preview-title">{file.name}</h2><button type="button" className="quiet-button" aria-label={t("关闭附件预览")} onClick={onClose}>×</button></header>
    <div className="attachment-preview-content">
      {!preview && !error && <p role="status">{t("正在读取本地附件…")}</p>}
      {error && <p role="alert" className="form-error">{localizeError(error)}</p>}
      {preview?.kind === "image" && <img src={preview.content} alt={file.name} onError={() => setError(t("图片无法解码，附件可能已损坏。"))} />}
      {preview?.kind === "markdown" && <Markdown text={preview.content} />}
      {preview?.kind === "text" && <pre>{preview.content}</pre>}
      {preview?.kind === "external" && <><p>{t("此文件将在系统默认的外部应用中打开，是否继续？")}</p><p className="attachment-local-path">{preview.content}</p><div className="attachment-preview-actions"><button type="button" className="secondary-button" onClick={onClose}>{t("取消")}</button><button type="button" className="primary-button" disabled={busy} onClick={() => void open()}>{busy ? t("正在打开…") : t("在外部应用中打开")}</button></div></>}
    </div>
  </dialog>;
}
