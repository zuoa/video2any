import { useId, useRef, useState } from "react";
import type { RefObject } from "react";
import { FileVideo, Loader2, UploadCloud } from "lucide-react";
import { Button } from "@radix-ui/themes";

interface UploadFieldProps {
  label?: string;
  hint?: string;
  compact?: boolean;
  disabled?: boolean;
  busy?: boolean;
  inputRef?: RefObject<HTMLInputElement | null>;
  onSelect: (file: File) => void | Promise<void>;
}

export function UploadField({
  label = "选择视频",
  hint = "点击选择或拖入视频 · MP4、MOV、WebM 等格式",
  compact = false,
  disabled = false,
  busy = false,
  inputRef,
  onSelect
}: UploadFieldProps) {
  const internalRef = useRef<HTMLInputElement>(null);
  const ref = inputRef ?? internalRef;
  const hintId = useId();
  const [dragging, setDragging] = useState(false);
  const [filename, setFilename] = useState("");
  const unavailable = disabled || busy;

  function selectFile(file: File | undefined) {
    if (!file || unavailable) return;
    setDragging(false);
    setFilename(file.name);
    void onSelect(file);
  }

  return <div className={`upload-field${compact ? " upload-field-compact" : ""}${dragging ? " is-dragging" : ""}${unavailable ? " is-disabled" : ""}`}
    onDragOver={event => {
      event.preventDefault();
      if (!unavailable && event.dataTransfer.types.includes("Files")) setDragging(true);
    }}
    onDragLeave={event => {
      if (!event.currentTarget.contains(event.relatedTarget as Node | null)) setDragging(false);
    }}
    onDrop={event => {
      event.preventDefault();
      setDragging(false);
      selectFile(event.dataTransfer.files[0]);
    }}>
    <input ref={ref} type="file" accept="video/*" className="file-input" tabIndex={-1}
      disabled={unavailable} aria-label={label}
      onChange={event => {
        const file = event.currentTarget.files?.[0];
        event.currentTarget.value = "";
        selectFile(file);
      }} />
    <Button variant="soft" className="upload-field-trigger" type="button" disabled={unavailable}
      aria-label={label} aria-describedby={hintId} aria-busy={busy}
      onClick={() => ref.current?.click()}>
      <span className="upload-field-icon">{busy ? <Loader2 className="spin" size={compact ? 21 : 27} /> : <UploadCloud size={compact ? 21 : 27} strokeWidth={1.6} />}</span>
      <span className="upload-field-copy"><strong>{busy ? "正在导入视频…" : dragging ? "松开鼠标，导入视频" : label}</strong>
        <span id={hintId}>{hint}</span></span>
      {!compact ? <span className="upload-field-action">选择文件</span> : null}
    </Button>
    {filename ? <div className="upload-field-selected" role="status"><FileVideo size={14} /><span title={filename}>已选择：{filename}</span></div> : null}
  </div>;
}
