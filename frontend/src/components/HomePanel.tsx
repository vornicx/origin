import { useEffect, useState, useMemo } from "react";
import { OriginCore } from "./OriginCore";
import { apiBase } from "../config/api";

const API_BASE = apiBase();

interface HealthData {
  status: string;
  skills?: { registered: number; failed: string[] };
  llm?: { providers_available: string[] };
  subsystems?: Record<string, string>;
  memory?: { memories: number; preferences: number; conversations: number };
}

interface MetricsData {
  cpu_percent?: number;
  memory_percent?: number;
  disk_percent?: number;
  gpu_percent?: number | null;
  cpu_temp?: number | null;
  net_speed_mbps?: number;
  process_count?: number;
  uptime_seconds?: number | null;
}

function meterColor(pct: number): string {
  if (pct > 85) return "var(--red)";
  if (pct > 65) return "var(--amber)";
  return "var(--cyan)";
}

function formatUptime(seconds: number): string {
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  return `${h.toString().padStart(2, "0")}:${m.toString().padStart(2, "0")}`;
}

function formatNetSpeed(mbps: number): string {
  if (mbps < 1) return `${(mbps * 1024).toFixed(0)} KB/s`;
  return `${mbps.toFixed(1)} MB/s`;
}

export function HomePanel() {
  const [health, setHealth] = useState<HealthData | null>(null);
  const [metrics, setMetrics] = useState<MetricsData | null>(null);
  const [now, setNow] = useState(() => new Date());

  useEffect(() => {
    const id = window.setInterval(() => setNow(new Date()), 1000);
    return () => clearInterval(id);
  }, []);

  useEffect(() => {
    const fetchHealth = async () => {
      try {
        const res = await fetch(`${API_BASE}/health`, { signal: AbortSignal.timeout(2000) });
        const data = await res.json();
        setHealth(data);
      } catch { /* offline */ }
    };
    const fetchMetrics = async () => {
      try {
        const res = await fetch(`${API_BASE}/dashboard/quick-metrics`, { signal: AbortSignal.timeout(2000) });
        const data = await res.json();
        if (data.cpu_percent != null) setMetrics(data);
      } catch { /* monitor skill may not be running */ }
    };
    const t = setTimeout(() => { fetchHealth(); fetchMetrics(); }, 300);
    const id = setInterval(() => { fetchHealth(); fetchMetrics(); }, 8000);
    return () => { clearTimeout(t); clearInterval(id); };
  }, []);

  const subsystemCount = health?.subsystems
    ? Object.values(health.subsystems).filter((v) => v === "running" || v === "active" || v === "available").length
    : 0;

  const metricBars = useMemo(() => {
    if (!metrics) return [];
    const bars: { label: string; pct: number; text: string }[] = [
      { label: "CPU", pct: metrics.cpu_percent ?? 0, text: `${metrics.cpu_percent?.toFixed(0) ?? "--"}%` },
      { label: "RAM", pct: metrics.memory_percent ?? 0, text: `${metrics.memory_percent?.toFixed(0) ?? "--"}%` },
      { label: "DISK", pct: metrics.disk_percent ?? 0, text: `${metrics.disk_percent?.toFixed(0) ?? "--"}%` },
    ];
    if (metrics.gpu_percent != null) {
      bars.push({ label: "GPU", pct: metrics.gpu_percent, text: `${metrics.gpu_percent.toFixed(0)}%` });
    }
    if (metrics.net_speed_mbps != null) {
      const netPct = Math.min(100, (metrics.net_speed_mbps / 10) * 100);
      bars.push({ label: "NET", pct: netPct, text: formatNetSpeed(metrics.net_speed_mbps) });
    }
    if (metrics.cpu_temp != null) {
      const tempPct = Math.min(100, (metrics.cpu_temp / 100) * 100);
      bars.push({ label: "TEMP", pct: tempPct, text: `${metrics.cpu_temp.toFixed(0)}°C` });
    }
    return bars;
  }, [metrics]);

  return (
    <div className="home-panel">
      <div className="home-panel__hero">
        <OriginCore size={200} showLatency={false} />
        <div className="home-panel__greeting">
          <h1 className="home-panel__time">
            {now.toLocaleTimeString("es-ES", { hour: "2-digit", minute: "2-digit", hour12: false })}
          </h1>
          <p className="home-panel__date">
            {now.toLocaleDateString("es-ES", {
              weekday: "long",
              day: "numeric",
              month: "long",
              year: "numeric",
            })}
          </p>
        </div>
      </div>

      <div className="home-panel__cards">
        <div className="home-panel__card">
          <div className="home-panel__card-header">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.5}>
              <circle cx={12} cy={12} r={10} />
              <path d="M12 6v6l3 3" />
            </svg>
            <span>Estado del Sistema</span>
          </div>
          <div className="home-panel__card-body">
            <div className="home-panel__stat">
              <span className="home-panel__stat-label">Backend</span>
              <span className={`home-panel__stat-value ${health?.status === "ok" ? "home-panel__stat-value--ok" : "home-panel__stat-value--off"}`}>
                {health?.status === "ok" ? "EN LINEA" : "OFFLINE"}
              </span>
            </div>
            <div className="home-panel__stat">
              <span className="home-panel__stat-label">Skills</span>
              <span className="home-panel__stat-value">{health?.skills?.registered ?? "--"}</span>
            </div>
            <div className="home-panel__stat">
              <span className="home-panel__stat-label">Subsistemas</span>
              <span className="home-panel__stat-value">{subsystemCount}/5</span>
            </div>
            <div className="home-panel__stat">
              <span className="home-panel__stat-label">LLM</span>
              <span className="home-panel__stat-value">{health?.llm?.providers_available?.length ?? 0} providers</span>
            </div>
            <div className="home-panel__stat">
              <span className="home-panel__stat-label">Memoria</span>
              <span className="home-panel__stat-value">{health?.memory?.memories ?? "--"} items</span>
            </div>
          </div>
        </div>

        <div className="home-panel__card">
          <div className="home-panel__card-header">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.5}>
              <rect x={2} y={3} width={20} height={14} rx={2} />
              <path d="M8 21h8M12 17v4" />
            </svg>
            <span>Recursos</span>
          </div>
          <div className="home-panel__card-body">
            {metricBars.length > 0 ? metricBars.map((bar) => (
              <MetricBar key={bar.label} label={bar.label} pct={bar.pct} text={bar.text} />
            )) : (
              <>
                <MetricBar label="CPU" pct={0} text="--%" />
                <MetricBar label="RAM" pct={0} text="--%" />
                <MetricBar label="DISK" pct={0} text="--%" />
              </>
            )}
            {metrics && (
              <div className="home-panel__info-row">
                {metrics.uptime_seconds != null && (
                  <span className="home-panel__info-tag home-panel__info-tag--green">
                    UP {formatUptime(metrics.uptime_seconds)}
                  </span>
                )}
                {metrics.process_count != null && (
                  <span className="home-panel__info-tag">
                    PROC {metrics.process_count}
                  </span>
                )}
              </div>
            )}
          </div>
        </div>

        <div className="home-panel__card">
          <div className="home-panel__card-header">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.5}>
              <circle cx={12} cy={12} r={10} />
              <path d="M12 2v20M2 12h20" />
              <circle cx={12} cy={12} r={4} />
            </svg>
            <span>Inteligencia OSINT</span>
          </div>
          <div className="home-panel__card-body">
            <div className="home-panel__stat">
              <span className="home-panel__stat-label">Crucix</span>
              <IntelServiceStatus port={3117} />
            </div>
            <div className="home-panel__stat">
              <span className="home-panel__stat-label">Osiris</span>
              <IntelServiceStatus port={3000} />
            </div>
          </div>
        </div>

        <div className="home-panel__card home-panel__card--shortcuts">
          <div className="home-panel__card-header">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.5}>
              <path d="M13 2L3 14h9l-1 8 10-12h-9l1-8z" />
            </svg>
            <span>Acciones Rapidas</span>
          </div>
          <div className="home-panel__quick-actions">
            <QuickAction label="Chat con Origin" hint="Ctrl+Alt+Space" />
            <QuickAction label="Analizar pantalla" hint="Vision" />
            <QuickAction label="Monitor del sistema" hint="Sistemas" />
            <QuickAction label="Panel de inteligencia" hint="Intel" />
          </div>
        </div>
      </div>
    </div>
  );
}

function MetricBar({ label, pct, text }: { label: string; pct: number; text: string }) {
  const color = meterColor(pct);
  return (
    <div className="home-panel__meter">
      <span className="home-panel__meter-label">{label}</span>
      <div className="home-panel__meter-bar">
        <div
          className="home-panel__meter-fill"
          style={{ width: `${pct}%`, background: `linear-gradient(90deg, ${color}, ${color})` }}
        />
      </div>
      <span className="home-panel__meter-value" style={{ color }}>{text}</span>
    </div>
  );
}

function QuickAction({ label, hint }: { label: string; hint: string }) {
  return (
    <div className="home-panel__action">
      <span>{label}</span>
      <span className="home-panel__action-hint">{hint}</span>
    </div>
  );
}

function IntelServiceStatus({ port }: { port: number }) {
  const [online, setOnline] = useState(false);

  useEffect(() => {
    const check = async () => {
      try {
        const host = window.location.hostname || "localhost";
        const r = await fetch(`http://${host}:${port}/api/health`, { signal: AbortSignal.timeout(1500) });
        setOnline(r.ok);
      } catch {
        setOnline(false);
      }
    };
    const t = setTimeout(check, 1000);
    const id = setInterval(check, 30000);
    return () => { clearTimeout(t); clearInterval(id); };
  }, [port]);

  return (
    <span className={`home-panel__stat-value ${online ? "home-panel__stat-value--ok" : "home-panel__stat-value--off"}`}>
      {online ? "EN LINEA" : "OFFLINE"}
    </span>
  );
}
