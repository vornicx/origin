import { useCallback, useState, useRef } from "react";

interface FileDropZoneProps {
  onFileSelected: (file: File) => void;
  currentFile?: File | null;
  onClear?: () => void;
}

const EXT_CATEGORIES: Record<string, { icon: string; label: string }> = {
  jpg: { icon: "img", label: "IMAGE" }, jpeg: { icon: "img", label: "IMAGE" },
  png: { icon: "img", label: "IMAGE" }, gif: { icon: "img", label: "IMAGE" },
  webp: { icon: "img", label: "IMAGE" }, svg: { icon: "img", label: "IMAGE" },
  pdf: { icon: "doc", label: "PDF" },
  doc: { icon: "doc", label: "WORD" }, docx: { icon: "doc", label: "WORD" },
  py: { icon: "code", label: "CODE" }, js: { icon: "code", label: "CODE" },
  ts: { icon: "code", label: "CODE" }, tsx: { icon: "code", label: "CODE" },
  jsx: { icon: "code", label: "CODE" }, html: { icon: "code", label: "CODE" },
  css: { icon: "code", label: "CODE" }, json: { icon: "data", label: "DATA" },
  csv: { icon: "data", label: "DATA" }, xml: { icon: "data", label: "DATA" },
  mp3: { icon: "audio", label: "AUDIO" }, wav: { icon: "audio", label: "AUDIO" },
  mp4: { icon: "video", label: "VIDEO" }, mov: { icon: "video", label: "VIDEO" },
  zip: { icon: "arch", label: "ARCHIVE" }, rar: { icon: "arch", label: "ARCHIVE" },
  txt: { icon: "text", label: "TEXT" }, md: { icon: "text", label: "TEXT" },
};

function categorize(name: string) {
  const ext = name.split(".").pop()?.toLowerCase() ?? "";
  return EXT_CATEGORIES[ext] ?? { icon: "file", label: "FILE" };
}

function formatSize(bytes: number) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 ** 2) return `${(bytes / 1024).toFixed(1)} KB`;
  if (bytes < 1024 ** 3) return `${(bytes / 1024 ** 2).toFixed(1)} MB`;
  return `${(bytes / 1024 ** 3).toFixed(1)} GB`;
}

export function FileDropZone({ onFileSelected, currentFile, onClear }: FileDropZoneProps) {
  const [dragOver, setDragOver] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  const onDragOver = useCallback((e: React.DragEvent) => { e.preventDefault(); setDragOver(true); }, []);
  const onDragLeave = useCallback(() => setDragOver(false), []);
  const onDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    setDragOver(false);
    const file = e.dataTransfer.files[0];
    if (file) onFileSelected(file);
  }, [onFileSelected]);

  const onClick = useCallback(() => { inputRef.current?.click(); }, []);
  const onInputChange = useCallback((e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (file) onFileSelected(file);
    if (inputRef.current) inputRef.current.value = "";
  }, [onFileSelected]);

  if (currentFile) {
    const cat = categorize(currentFile.name);
    return (
      <div className="file-drop file-drop--loaded">
        <div className="file-drop__info">
          <span className="file-drop__icon">{cat.icon}</span>
          <div className="file-drop__meta">
            <span className="file-drop__name">{currentFile.name}</span>
            <span className="file-drop__detail">{cat.label} · {formatSize(currentFile.size)}</span>
          </div>
        </div>
        {onClear && (
          <button className="file-drop__clear" onClick={onClear} title="Remove file">×</button>
        )}
      </div>
    );
  }

  return (
    <div
      className={`file-drop${dragOver ? " file-drop--drag" : ""}`}
      onDragOver={onDragOver}
      onDragLeave={onDragLeave}
      onDrop={onDrop}
      onClick={onClick}
    >
      <input ref={inputRef} type="file" className="file-drop__input" onChange={onInputChange} />
      <div className="file-drop__placeholder">
        <span className="file-drop__arrow">↑</span>
        <span className="file-drop__text">{dragOver ? "Release to upload" : "Drop file or click to browse"}</span>
      </div>
    </div>
  );
}
