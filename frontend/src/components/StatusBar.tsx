import { useState, useEffect } from "react";
import { apiBase } from "../config/api";

const API = apiBase();

function useTime() {
  const [t, setT] = useState(new Date());
  useEffect(() => {
    const id = setInterval(() => setT(new Date()), 1000);
    return () => clearInterval(id);
  }, []);
  return t;
}

function pad(n: number) {
  return String(n).padStart(2, "0");
}

interface StatusBarProps {
  wsStatus: "connected" | "connecting" | "disconnected" | "error";
}

export function StatusBar({ wsStatus }: StatusBarProps) {
  const now = useTime();
  const [cpu, setCpu] = useState<number | null>(null);
  const [ram, setRam] = useState<number | null>(null);
  const [skills, setSkills] = useState<number>(0);
  const [uptime, setUptime] = useState<string>("");

  useEffect(() => {
    const fetchMetrics = async () => {
      try {
        const [monRes, skillRes] = await Promise.allSettled([
          fetch(`${API}/dashboard/monitor`).then((r) => r.json()),
          fetch(`${API}/dashboard/skills`).then((r) => r.json()),
        ]);

        if (monRes.status === "fulfilled") {
          const m = monRes.value?.current_metrics;
          if (m) {
            setCpu(m.cpu_percent ?? null);
            setRam(m.ram_percent ?? null);
          }
        }
        if (skillRes.status === "fulfilled") {
          setSkills(skillRes.value?.skills?.length ?? 0);
        }
      } catch {}
    };

    fetchMetrics();
    const id = setInterval(fetchMetrics, 10000);
    return () => clearInterval(id);
  }, []);

  useEffect(() => {
    const fetchUptime = async () => {
      try {
        const res = await fetch(`${API}/health`);
        const data = await res.json();
        if (data.uptime_seconds) {
          const s = data.uptime_seconds;
          const h = Math.floor(s / 3600);
          const m = Math.floor((s % 3600) / 60);
          setUptime(`${pad(h)}:${pad(m)}`);
        }
      } catch {}
    };
    fetchUptime();
    const id = setInterval(fetchUptime, 30000);
    return () => clearInterval(id);
  }, []);

  const timeStr = `${pad(now.getHours())}:${pad(now.getMinutes())}:${pad(now.getSeconds())}`;
  const dateStr = now.toLocaleDateString("es-ES", { weekday: "short", day: "2-digit", month: "short" }).toUpperCase();

  const wsLabel =
    wsStatus === "connected" ? "ONLINE" :
    wsStatus === "connecting" ? "SYNC…" :
    wsStatus === "error" ? "ERROR" : "OFFLINE";
  const wsCls =
    wsStatus === "connected" ? "sb-status--ok" :
    wsStatus === "connecting" ? "sb-status--warn" : "sb-status--err";

  return (
    <div className="statusbar">
      {/* Left: logo */}
      <div className="statusbar__left">
        <span className="statusbar__logo">Origin</span>
        <span className="statusbar__ver">v2.0</span>
        <span className={`statusbar__ws ${wsCls}`}>
          <span className="statusbar__dot" />
          {wsLabel}
        </span>
      </div>

      {/* Center: metrics */}
      <div className="statusbar__center">
        {cpu !== null && (
          <div className="sb-metric">
            <span className="sb-metric__lbl">CPU</span>
            <span className={`sb-metric__val ${cpu > 90 ? "sb-metric__val--crit" : cpu > 75 ? "sb-metric__val--warn" : ""}`}>
              {cpu.toFixed(0)}%
            </span>
            <div className="sb-metric__bar">
              <div className="sb-metric__fill" style={{ width: `${cpu}%`, backgroundColor: cpu > 90 ? "var(--red)" : cpu > 75 ? "var(--amber)" : "var(--cyan)" }} />
            </div>
          </div>
        )}
        {ram !== null && (
          <div className="sb-metric">
            <span className="sb-metric__lbl">RAM</span>
            <span className={`sb-metric__val ${ram > 90 ? "sb-metric__val--crit" : ram > 80 ? "sb-metric__val--warn" : ""}`}>
              {ram.toFixed(0)}%
            </span>
            <div className="sb-metric__bar">
              <div className="sb-metric__fill" style={{ width: `${ram}%`, backgroundColor: ram > 90 ? "var(--red)" : ram > 80 ? "var(--amber)" : "var(--cyan)" }} />
            </div>
          </div>
        )}
        {skills > 0 && (
          <div className="sb-metric">
            <span className="sb-metric__lbl">SKL</span>
            <span className="sb-metric__val">{skills}</span>
          </div>
        )}
        {uptime && (
          <div className="sb-metric">
            <span className="sb-metric__lbl">UP</span>
            <span className="sb-metric__val">{uptime}</span>
          </div>
        )}
      </div>

      {/* Right: time */}
      <div className="statusbar__right">
        <span className="statusbar__date">{dateStr}</span>
        <span className="statusbar__time">{timeStr}</span>
      </div>
    </div>
  );
}
