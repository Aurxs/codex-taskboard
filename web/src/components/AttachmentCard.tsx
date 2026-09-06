import { useEffect, useState } from "react";
import { previewAttachment } from "../api";
import { t } from "../i18n";
import type { TaskAttachment } from "../types";
import { LinearIcon } from "./LinearIcon";

export function AttachmentCard({ file, disabled, onRemove, onPreview }: {
  file: File | TaskAttachment;
  disabled: boolean;
  onRemove: () => void;
  onPreview?: () => void;
}) {
  const isImage = /\.(png|jpe?g|gif|webp)$/i.test(file.name);
  const fileType = file.name.split(".").pop()?.toUpperCase() || "FILE";
  const [thumbnail, setThumbnail] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);
  // Depend on the stored ID, since task refreshes replace attachment objects.
  const source = "id" in file ? file.id : file;
  useEffect(() => {
    setThumbnail(null);
    setFailed(false);
    if (!isImage) return;
    let active = true;
    let objectUrl: string | undefined;
    if (typeof source === "string") {
      void previewAttachment(source).then(preview => {
        if (active && preview.kind === "image") setThumbnail(preview.content);
      }).catch(() => { if (active) setFailed(true); });
    } else {
      objectUrl = URL.createObjectURL(source);
      setThumbnail(objectUrl);
    }
    return () => {
      active = false;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [source, isImage]);

  const content = thumbnail && !failed
    ? <img src={thumbnail} alt={file.name} onError={() => setFailed(true)} />
    : <><span className="attachment-card-icon"><LinearIcon name="fileChange" /></span><span className="attachment-card-copy"><strong>{file.name}</strong><small>{fileType}</small></span></>;
  return <li className={`attachment-card${isImage ? " is-image" : ""}`} title={file.name}>
    {onPreview
      ? <button type="button" className="attachment-card-content" aria-label={file.name} onClick={onPreview}>{content}</button>
      : <div className="attachment-card-content">{content}</div>}
    <button type="button" className="attachment-card-remove" disabled={disabled} aria-label={t("移除 {0}", file.name)} title={t("移除 {0}", file.name)} onClick={onRemove}><LinearIcon name="close" /></button>
  </li>;
}
