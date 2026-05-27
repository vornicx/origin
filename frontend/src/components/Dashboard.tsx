import { useState, useEffect, useCallback } from "react";
import { OSPanel } from "./OSPanel";
import { ArcMeter } from "./ArcMeter";
import { apiBase } from "../config/api";

const API_BASE = apiBase();

interface SkillInfo {
  name: string;
  description: string;
  execution_count: number;
  last_execution: string | null;
}

interface MemoryStats {
  total_memories: number;
  total_conversations: number;
  total_preferences: number;
  by_type: Record<string, number>;
}

interface MonitorData {
  running: boolean;
  current_metrics: Record<string, any> | null;
  last_alert: any | null;
  total_alerts: number;
}

interface DashboardData {
  skills: SkillInfo[];
  memory: MemoryStats | null;
  preferences: Record<string, any>;
  windows: string[];
  system: Record<string, any> | null;
  screenshotB64: string | null;
  monitor: MonitorData | null;
}

type Tab = "metrics" | "skills" | "memory" | "system" | "screen";

export function Dashboard() {
  const [data, setData] = useState<DashboardData>({
    skills: [], memory: null, preferences: {},
    windows: [], system: null, screenshotB64: null, monitor: null,
  });
  const [activeTab, setActiveTab] = useState<Tab>("metrics");
  const [loading, setLoading] = useState(false);

  const fetchData = useCallback(async () => {
    setLoading(true);
    try {
      const [skillsRes, memRes, sysRes, winRes, monRes] = await Promise.allSettled([
        fetch(`${API_BASE}/dashboard/skills`).then((r) => r.json()),
        fetch(`${API_BASE}/dashboard/memory`).then((r) => r.json()),
        fetch(`${API_BASE}/dashboard/system`).then((r) => r.json()),
        fetch(`${API_BASE}/dashboard/windows`).then((r) => r.json()),
        fetch(`${API_BASE}/dashboard/monitor`).then((r) => r.json()),
      ]);
      setData({
        skills: skillsRes.status === "fulfilled" ? skillsRes.value.skills ?? [] : [],
        memory: memRes.status === "fulfilled" ? memRes.value.stats ?? null : null,
        preferences: memRes.status === "fulfilled" ? memRes.value.preferences ?? {} : {},
        system: sysRes.status === "fulfilled" ? sysRes.value : null,
        windows: winRes.status === "fulfilled" ? winRes.value.windows ?? [] : [],
        monitor: monRes.status === "fulfilled" ? monRes.value : null,
        screenshotB64: null,
      });
    } catch {}
    setLoading(false);
  }, []);

  useEffect(() => {
    fetchData();
    const id = setInterval(fetchData, 15000);
    return () => clearInterval(id);
  }, [fetchData]);

  const takeScreenshot = async () => {
    try {
      const res = await fetch(`${API_BASE}/dashboard/screenshot`);
      const json = await res.json();
      if (json.thumbnail_b64) setData((p) => ({ ...p, screenshotB64: json.thumbnail_b64 }));
    } catch {}
  };

  const m = data.monitor?.current_metrics;
  const cpu = m?.cpu_percent ?? 0;
  const ram = m?.ram_percent ?? 0;
  const disk = m?.disk_percent ?? 0;

  const tabs: { id: Tab; label: string }[] = [
    { id: "metrics", label: "SYS" },
    { id: "skills", label: "SKL" },
    { id: "memory", label: "MEM" },
    { id: "system", label: "INF" },
    { id: "screen", label: "SCR" },
  ];

  return (
    <div className="dashboard hud-panel">
      {/* Tab bar */}
      <div className="dashboard__tabs">
        {tabs.map((t) => (
          <button
            key={t.id}
            className={`dashboard__tab ${activeTab === t.id ? "dashboard__tab--active" : ""}`}
            onClick={() => { setActiveTab(t.id); if (t.id === "screen") takeScreenshot(); }}
          >
            {t.label}
          </button>
        ))}
        <button className="dashboard__tab dashboard__tab--refresh" onClick={fetchData} disabled={loading}>
          {loading ? "…" : "↻"}
        </button>
      </div>

      <div className="dashboard__content">

        {/* ── METRICS TAB ── */}
        {activeTab === "metrics" && (
          <div className="dash-metrics">
            {/* Arc meters row */}
            <div className="dash-arcs">
              <ArcMeter value={cpu} label="CPU" warnAt={75} critAt={90} subtitle={m ? `${m.process_count ?? 0} proc` : undefined} />
              <ArcMeter value={ram} label="RAM" warnAt={80} critAt={92} subtitle={m ? `${m.ram_used_gb ?? 0}/${m.ram_total_gb ?? 0} GB` : undefined} />
              <ArcMeter value={disk} label="DISK" warnAt={85} critAt={95} subtitle={m ? `${m.disk_free_gb ?? 0} GB free` : undefined} />
            </div>

            {/* Network */}
            {m && (
              <div className="dash-net">
                <div className="dash-net__row">
                  <span className="dash-net__icon">↑</span>
                  <span className="dash-net__label">SENT</span>
                  <span className="dash-net__val">{m.net_bytes_sent_mb ?? 0} MB</span>
                </div>
                <div className="dash-net__row">
                  <span className="dash-net__icon">↓</span>
                  <span className="dash-net__label">RECV</span>
                  <span className="dash-net__val">{m.net_bytes_recv_mb ?? 0} MB</span>
                </div>
              </div>
            )}

            {/* Top processes */}
            {m?.top_processes?.length > 0 && (
              <div className="dash-procs">
                <div className="dash-procs__title">TOP PROCESOS</div>
                {m!.top_processes.slice(0, 5).map((p: any, i: number) => (
                  <div key={i} className="dash-proc">
                    <span className="dash-proc__name">{p.name}</span>
                    <div className="dash-proc__bars">
                      <div className="dash-proc__bar-wrap" title={`CPU ${p.cpu}%`}>
                        <div className="dash-proc__bar dash-proc__bar--cpu" style={{ width: `${Math.min(100, p.cpu * 2)}%` }} />
                      </div>
                      <div className="dash-proc__bar-wrap" title={`RAM ${p.ram}%`}>
                        <div className="dash-proc__bar dash-proc__bar--ram" style={{ width: `${Math.min(100, p.ram)}%` }} />
                      </div>
                    </div>
                    <span className="dash-proc__val">{p.cpu}%</span>
                  </div>
                ))}
              </div>
            )}

            {/* Monitor toggle */}
            <button
              className={`dash-btn ${data.monitor?.running ? "dash-btn--active" : ""}`}
              onClick={async () => {
                const action = data.monitor?.running ? "stop" : "start";
                await fetch(`${API_BASE}/dashboard/monitor/${action}`, { method: "POST" });
                fetchData();
              }}
            >
              {data.monitor?.running ? "■ STOP MONITOR" : "▶ START MONITOR"}
            </button>

            {/* Last alert */}
            {data.monitor?.last_alert && (
              <div className={`dash-alert dash-alert--${data.monitor.last_alert.severity}`}>
                <span className="dash-alert__title">{data.monitor.last_alert.title}</span>
                <span className="dash-alert__msg">{data.monitor.last_alert.message}</span>
              </div>
            )}

            <OSPanel />
          </div>
        )}

        {/* ── SKILLS TAB ── */}
        {activeTab === "skills" && (
          <div className="dashboard__section">
            <div className="dash-section-title">{data.skills.length} SKILLS ACTIVAS</div>
            <div className="skill-grid">
              {data.skills.map((s) => (
                <div key={s.name} className={`skill-card ${s.execution_count > 0 ? "skill-card--used" : ""}`}>
                  <div className="skill-card__header">
                    <span className="skill-card__dot" />
                    <span className="skill-card__name">{s.name}</span>
                    {s.execution_count > 0 && (
                      <span className="skill-card__count">{s.execution_count}×</span>
                    )}
                  </div>
                  <div className="skill-card__desc">{s.description}</div>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* ── MEMORY TAB ── */}
        {activeTab === "memory" && (
          <div className="dashboard__section">
            <div className="dash-section-title">ESTADO DE MEMORIA</div>
            {data.memory && (
              <>
                <div className="mem-stats">
                  <div className="mem-stat">
                    <span className="mem-stat__num">{data.memory.total_memories.toLocaleString()}</span>
                    <span className="mem-stat__lbl">RECUERDOS</span>
                  </div>
                  <div className="mem-stat">
                    <span className="mem-stat__num">{data.memory.total_conversations}</span>
                    <span className="mem-stat__lbl">CONVERSACIONES</span>
                  </div>
                  <div className="mem-stat">
                    <span className="mem-stat__num">{data.memory.total_preferences}</span>
                    <span className="mem-stat__lbl">PREFERENCIAS</span>
                  </div>
                </div>
                {Object.entries(data.memory.by_type ?? {}).map(([type, count]) => (
                  <div key={type} className="stat-row">
                    <span>{type}</span>
                    <span className="stat-value">{count as number}</span>
                  </div>
                ))}
                {Object.keys(data.preferences).length > 0 && (
                  <>
                    <div className="dash-section-title" style={{ marginTop: 16 }}>PREFERENCIAS APRENDIDAS</div>
                    {Object.entries(data.preferences).map(([k, v]) => (
                      <div key={k} className="pref-item">
                        <span className="pref-key">{k}</span>
                        <span className="pref-val">{String(v)}</span>
                      </div>
                    ))}
                  </>
                )}
              </>
            )}
          </div>
        )}

        {/* ── SYSTEM TAB ── */}
        {activeTab === "system" && (
          <div className="dashboard__section">
            <div className="dash-section-title">SISTEMA</div>
            {data.system && Object.entries(data.system).map(([k, v]) => (
              <div key={k} className="stat-row">
                <span>{k.replace(/_/g, " ")}</span>
                <span className="stat-value">{String(v)}</span>
              </div>
            ))}
            {data.windows.length > 0 && (
              <>
                <div className="dash-section-title" style={{ marginTop: 12 }}>VENTANAS ({data.windows.length})</div>
                <div className="window-list">
                  {data.windows.slice(0, 12).map((w, i) => (
                    <div key={i} className="window-item">{w}</div>
                  ))}
                </div>
              </>
            )}
          </div>
        )}

        {/* ── SCREEN TAB ── */}
        {activeTab === "screen" && (
          <div className="dashboard__section">
            <div className="dash-section-title">CAPTURA DE PANTALLA</div>
            <button className="dash-btn" onClick={takeScreenshot}>CAPTURAR AHORA</button>
            {data.screenshotB64 && (
              <img
                className="screenshot-preview"
                src={`data:image/jpeg;base64,${data.screenshotB64}`}
                alt="Captura"
              />
            )}
          </div>
        )}
      </div>
    </div>
  );
}
