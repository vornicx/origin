import { useState, useEffect } from "react";
import { apiBase } from "../config/api";

const API = apiBase();

interface MemStats {
  total_memories: number;
  total_conversations: number;
  total_preferences: number;
}

interface ProactiveEvent {
  id: string;
  kind: string;
  title: string;
  message: string;
  timestamp: string;
  severity: string;
}

interface ContextFile {
  filename: string;
  chars: number;
}

interface LLMHealth {
  [provider: string]: {
    ok: number;
    fail: number;
    score: number;
    in_cooldown: boolean;
  };
}

export function LeftPanel() {
  const [mem, setMem] = useState<MemStats | null>(null);
  const [events, setEvents] = useState<ProactiveEvent[]>([]);
  const [contexts, setContexts] = useState<ContextFile[]>([]);
  const [llmHealth, setLlmHealth] = useState<LLMHealth>({});

  useEffect(() => {
    const fetch_ = async () => {
      try {
        const [mRes, eRes, cRes, lRes] = await Promise.allSettled([
          fetch(`${API}/dashboard/memory`).then((r) => r.json()),
          fetch(`${API}/proactive/events?n=5`).then((r) => r.json()),
          fetch(`${API}/context/list`).then((r) => r.json()),
          fetch(`${API}/llm/health`).then((r) => r.json()),
        ]);
        if (mRes.status === "fulfilled") setMem(mRes.value?.stats ?? null);
        if (eRes.status === "fulfilled") setEvents(eRes.value?.events?.slice(0, 5) ?? []);
        if (cRes.status === "fulfilled") setContexts(cRes.value?.contexts ?? []);
        if (lRes.status === "fulfilled") setLlmHealth(lRes.value ?? {});
      } catch {}
    };
    fetch_();
    const id = setInterval(fetch_, 30000);
    return () => clearInterval(id);
  }, []);

  const scoreBar = (score: number) => Math.round(score * 100);

  return (
    <div className="left-panel">
      {/* Memory */}
      <div className="lp-section">
        <div className="lp-section__title">
          <span className="lp-dot" />
          MEMORIA
        </div>
        {mem ? (
          <div className="lp-stats">
            <div className="lp-stat">
              <span className="lp-stat__k">RECUERDOS</span>
              <span className="lp-stat__v">{mem.total_memories.toLocaleString()}</span>
            </div>
            <div className="lp-stat">
              <span className="lp-stat__k">CONVERSACIONES</span>
              <span className="lp-stat__v">{mem.total_conversations}</span>
            </div>
            <div className="lp-stat">
              <span className="lp-stat__k">PREFERENCIAS</span>
              <span className="lp-stat__v">{mem.total_preferences}</span>
            </div>
          </div>
        ) : (
          <div className="lp-empty">Sin datos</div>
        )}
      </div>

      {/* LLM Health */}
      {Object.keys(llmHealth).length > 0 && (
        <div className="lp-section">
          <div className="lp-section__title">
            <span className="lp-dot lp-dot--blue" />
            PROVEEDORES LLM
          </div>
          <div className="lp-providers">
            {Object.entries(llmHealth)
              .sort(([, a], [, b]) => b.score - a.score)
              .slice(0, 5)
              .map(([name, h]) => (
                <div key={name} className="lp-provider">
                  <span className="lp-provider__name">{name}</span>
                  <div className="lp-provider__bar-wrap">
                    <div
                      className="lp-provider__bar"
                      style={{
                        width: `${scoreBar(h.score)}%`,
                        backgroundColor: h.in_cooldown
                          ? "var(--red)"
                          : h.score > 0.7
                          ? "var(--green)"
                          : "var(--amber)",
                      }}
                    />
                  </div>
                  <span className={`lp-provider__dot ${h.in_cooldown ? "lp-provider__dot--down" : "lp-provider__dot--up"}`} />
                </div>
              ))}
          </div>
        </div>
      )}

      {/* Context files */}
      {contexts.length > 0 && (
        <div className="lp-section">
          <div className="lp-section__title">
            <span className="lp-dot lp-dot--amber" />
            CONTEXTO ACTIVO
          </div>
          <div className="lp-contexts">
            {contexts.map((c) => (
              <div key={c.filename} className="lp-ctx">
                <span className="lp-ctx__name">{c.filename}</span>
                <span className="lp-ctx__size">{(c.chars / 1000).toFixed(1)}k</span>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Proactive events */}
      {events.length > 0 && (
        <div className="lp-section">
          <div className="lp-section__title">
            <span className="lp-dot lp-dot--green" />
            SUGERENCIAS
          </div>
          <div className="lp-events">
            {events.map((e) => (
              <div key={e.id} className={`lp-event lp-event--${e.severity}`}>
                <div className="lp-event__title">{e.title}</div>
                <div className="lp-event__msg">{e.message}</div>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
