import { useEffect, useRef, useState, useCallback } from "react";

interface LogEntry {
  id: number;
  text: string;
  tag: "sys" | "user" | "ai" | "err" | "file";
  displayedText: string;
  complete: boolean;
}

const TAG_COLORS: Record<LogEntry["tag"], string> = {
  sys: "var(--amber)",
  user: "var(--text-bright)",
  ai: "var(--cyan)",
  err: "var(--red)",
  file: "var(--green)",
};

function classifyTag(text: string): LogEntry["tag"] {
  const lower = text.toLowerCase();
  if (lower.startsWith("you:") || lower.startsWith("user:")) return "user";
  if (lower.startsWith("origin:") || lower.startsWith("ai:")) return "ai";
  if (lower.startsWith("file:")) return "file";
  if (lower.includes("error") || lower.includes("err")) return "err";
  return "sys";
}

let nextId = 0;

export function ActivityLog() {
  const [entries, setEntries] = useState<LogEntry[]>([]);
  const scrollRef = useRef<HTMLDivElement>(null);
  const typewriterRef = useRef<number | null>(null);

  const appendLog = useCallback((text: string) => {
    const id = nextId++;
    const tag = classifyTag(text);
    setEntries((prev) => [
      ...prev.slice(-80),
      { id, text, tag, displayedText: "", complete: false },
    ]);
  }, []);

  useEffect(() => {
    const tick = () => {
      setEntries((prev) => {
        const idx = prev.findIndex((e) => !e.complete);
        if (idx === -1) return prev;
        const entry = prev[idx];
        if (entry.displayedText.length >= entry.text.length) {
          const updated = [...prev];
          updated[idx] = { ...entry, complete: true };
          return updated;
        }
        const updated = [...prev];
        const charsToAdd = Math.min(3, entry.text.length - entry.displayedText.length);
        updated[idx] = {
          ...entry,
          displayedText: entry.text.slice(0, entry.displayedText.length + charsToAdd),
        };
        return updated;
      });
    };
    typewriterRef.current = window.setInterval(tick, 12);
    return () => { if (typewriterRef.current) clearInterval(typewriterRef.current); };
  }, []);

  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [entries]);

  useEffect(() => {
    appendLog("SYS: Origin activity monitor online");
    const handler = (e: CustomEvent<string>) => appendLog(e.detail);
    window.addEventListener("origin:log" as any, handler);
    return () => window.removeEventListener("origin:log" as any, handler);
  }, [appendLog]);

  return (
    <div className="activity-log">
      <div className="activity-log__header">
        <span className="activity-log__title">ACTIVITY LOG</span>
        <span className="activity-log__status">LIVE</span>
      </div>
      <div className="activity-log__body" ref={scrollRef}>
        {entries.map((e) => (
          <div key={e.id} className="activity-log__line" style={{ color: TAG_COLORS[e.tag] }}>
            {e.displayedText}
            {!e.complete && <span className="activity-log__cursor">|</span>}
          </div>
        ))}
      </div>
    </div>
  );
}

export function dispatchLog(text: string) {
  window.dispatchEvent(new CustomEvent("origin:log", { detail: text }));
}
