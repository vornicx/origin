import { useState, useEffect, useCallback } from "react";
import { apiBase, isTauri } from "../config/api";

const API = apiBase();

interface Props {
  onClose: () => void;
}

export function ClipboardAnalyzer({ onClose }: Props) {
  const [content, setContent] = useState("");
  const [answer, setAnswer] = useState("");
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [onClose]);

  useEffect(() => {
    (async () => {
      try {
        const text = await navigator.clipboard.readText();
        if (!text.trim()) {
          setContent("[Clipboard vacio]");
          setAnswer("No hay texto en el clipboard para analizar.");
          setLoading(false);
          return;
        }
        setContent(text.length > 500 ? text.slice(0, 500) + "..." : text);
        const res = await fetch(`${API}/chat`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            message: `Analiza el siguiente contenido del clipboard del usuario y da una respuesta util:\n\n${text.slice(0, 4000)}`,
            context_tags: ["clipboard"],
          }),
        });
        const data = await res.json();
        setAnswer(data.reply || data.error || "Sin respuesta");
      } catch (e) {
        setAnswer("No se pudo leer el clipboard o conectar con Origin.");
      } finally {
        setLoading(false);
      }
    })();
  }, []);

  const notify = useCallback(async () => {
    if (!isTauri() || !answer) return;
    try {
      const { invoke } = await import("@tauri-apps/api/core");
      await invoke("send_notification", {
        title: "Origin - Clipboard",
        body: answer.slice(0, 200),
      });
    } catch {}
  }, [answer]);

  useEffect(() => {
    if (answer && !loading) notify();
  }, [answer, loading, notify]);

  return (
    <div className="quick-query" onClick={onClose}>
      <div className="quick-query__box quick-query__box--wide" onClick={(e) => e.stopPropagation()}>
        <div className="quick-query__header">
          <span className="quick-query__icon">V</span>
          <span className="quick-query__title">CLIPBOARD ANALYSIS</span>
          <kbd className="quick-query__hint">ESC</kbd>
        </div>
        <div className="quick-query__clipboard-preview">
          {content || "Leyendo clipboard..."}
        </div>
        {loading && <div className="quick-query__loading">Analizando...</div>}
        {answer && <div className="quick-query__answer">{answer}</div>}
      </div>
    </div>
  );
}
