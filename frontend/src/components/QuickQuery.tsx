import { useState, useRef, useEffect, useCallback } from "react";
import { apiBase } from "../config/api";

const API = apiBase();

interface Props {
  onClose: () => void;
}

export function QuickQuery({ onClose }: Props) {
  const [query, setQuery] = useState("");
  const [answer, setAnswer] = useState("");
  const [loading, setLoading] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    inputRef.current?.focus();
  }, []);

  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [onClose]);

  const submit = useCallback(async () => {
    if (!query.trim() || loading) return;
    setLoading(true);
    setAnswer("");
    try {
      const res = await fetch(`${API}/chat`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message: query.trim(), context_tags: [] }),
      });
      const data = await res.json();
      setAnswer(data.reply || data.error || "Sin respuesta");
    } catch {
      setAnswer("Error conectando con Origin");
    } finally {
      setLoading(false);
    }
  }, [query, loading]);

  return (
    <div className="quick-query" onClick={onClose}>
      <div className="quick-query__box" onClick={(e) => e.stopPropagation()}>
        <div className="quick-query__header">
          <span className="quick-query__icon">J</span>
          <span className="quick-query__title">Origin</span>
          <kbd className="quick-query__hint">ESC</kbd>
        </div>
        <form
          className="quick-query__form"
          onSubmit={(e) => { e.preventDefault(); submit(); }}
        >
          <input
            ref={inputRef}
            className="quick-query__input"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Pregunta algo a Origin..."
            disabled={loading}
            autoComplete="off"
            spellCheck={false}
          />
        </form>
        {loading && <div className="quick-query__loading">Pensando...</div>}
        {answer && (
          <div className="quick-query__answer">{answer}</div>
        )}
      </div>
    </div>
  );
}
