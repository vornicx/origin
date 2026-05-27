import { useState, useEffect, useCallback, useRef } from "react";
import { apiBase } from "../config/api";

const API_BASE = apiBase();
const POLL_INTERVAL_MS = 10_000;       // refresh every 10s (not 5s)
const SLIDER_DEBOUNCE_MS = 120;         // wait 120ms after last drag before POST

interface Battery {
  percent: number;
  plugged_in: boolean;
  time_left?: string;
  error?: string;
}

interface ActiveWindow {
  process?: string;
  title?: string;
  pid?: number;
}

interface NetworkInfo {
  wifi?: { ssid?: string; signal?: string; state?: string };
  interfaces?: any[];
  stats?: { bytes_recv_mb?: number; bytes_sent_mb?: number };
}

export function OSPanel() {
  const [volume, setVolume] = useState<{ level: number; muted: boolean } | null>(null);
  const [brightness, setBrightness] = useState<number | null>(null);
  const [battery, setBattery] = useState<Battery | null>(null);
  const [window, setWindow] = useState<ActiveWindow | null>(null);
  const [network, setNetwork] = useState<NetworkInfo | null>(null);
  const [idleSec, setIdleSec] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);

  const refresh = useCallback(async () => {
    setBusy(true);
    try {
      const [volRes, batRes, winRes, netRes, idleRes, brRes] = await Promise.allSettled([
        fetch(`${API_BASE}/os/volume`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ op: "get" }),
        }).then((r) => r.json()),
        fetch(`${API_BASE}/os/battery`).then((r) => r.json()),
        fetch(`${API_BASE}/window/current`).then((r) => r.json()),
        fetch(`${API_BASE}/os/network`).then((r) => r.json()),
        fetch(`${API_BASE}/os/idle`).then((r) => r.json()),
        fetch(`${API_BASE}/os/brightness`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ op: "get" }),
        }).then((r) => r.json()),
      ]);

      if (volRes.status === "fulfilled" && volRes.value.result?.level !== undefined) {
        setVolume({ level: volRes.value.result.level, muted: volRes.value.result.muted });
      }
      if (batRes.status === "fulfilled") setBattery(batRes.value);
      if (winRes.status === "fulfilled") setWindow(winRes.value);
      if (netRes.status === "fulfilled") setNetwork(netRes.value);
      if (idleRes.status === "fulfilled") setIdleSec(idleRes.value.idle_seconds ?? null);
      if (brRes.status === "fulfilled" && brRes.value.result?.primary !== undefined) {
        setBrightness(brRes.value.result.primary);
      }
    } finally {
      setBusy(false);
    }
  }, []);

  useEffect(() => {
    refresh();
    const timer = setInterval(refresh, POLL_INTERVAL_MS);
    return () => clearInterval(timer);
  }, [refresh]);

  // Debounce timers — POST only after the user stops dragging
  const volTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const briTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const setVolumeLevel = (level: number) => {
    // Optimistic update so the slider feels instant
    setVolume((prev) => (prev ? { ...prev, level } : { level, muted: false }));
    if (volTimer.current) clearTimeout(volTimer.current);
    volTimer.current = setTimeout(() => {
      fetch(`${API_BASE}/os/volume`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ op: "set", level }),
      }).catch(() => {});
    }, SLIDER_DEBOUNCE_MS);
  };

  const setBrightnessLevel = (level: number) => {
    setBrightness(level);
    if (briTimer.current) clearTimeout(briTimer.current);
    briTimer.current = setTimeout(() => {
      fetch(`${API_BASE}/os/brightness`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ op: "set", level }),
      }).catch(() => {});
    }, SLIDER_DEBOUNCE_MS);
  };

  const toggleMute = async () => {
    const r = await fetch(`${API_BASE}/os/volume`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ op: "toggle_mute" }),
    }).then((r) => r.json());
    if (r.result) setVolume((prev) => (prev ? { ...prev, muted: r.result.muted } : prev));
  };

  const powerAction = async (action: string) => {
    if (action === "restart" || action === "shutdown") {
      if (!confirm(`¿Confirmar ${action}?`)) return;
    }
    await fetch(`${API_BASE}/os/power/${action}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ confirm: true, delay_sec: 5 }),
    });
  };

  return (
    <div className="os-panel">
      <h3 className="dashboard__title">SISTEMA</h3>

      {/* Volume */}
      <div className="os-row">
        <div className="os-row__label">
          VOL
          <button className="os-mute-btn" onClick={toggleMute}>
            {volume?.muted ? "M" : "♪"}
          </button>
        </div>
        <input
          type="range"
          min={0}
          max={100}
          value={volume?.level ?? 0}
          onChange={(e) => setVolumeLevel(parseInt(e.target.value))}
          className={`os-slider ${volume?.muted ? "os-slider--muted" : ""}`}
        />
        <div className="os-row__value">{Math.round(volume?.level ?? 0)}%</div>
      </div>

      {/* Brightness */}
      {brightness !== null && (
        <div className="os-row">
          <div className="os-row__label">BRI</div>
          <input
            type="range"
            min={0}
            max={100}
            value={brightness}
            onChange={(e) => setBrightnessLevel(parseInt(e.target.value))}
            className="os-slider"
          />
          <div className="os-row__value">{brightness}%</div>
        </div>
      )}

      {/* Battery */}
      {battery && !battery.error && (
        <div className="os-row">
          <div className="os-row__label">BAT</div>
          <div className="os-bar">
            <div
              className={`os-bar__fill ${battery.percent < 20 ? "os-bar__fill--low" : ""}`}
              style={{ width: `${battery.percent}%` }}
            />
          </div>
          <div className="os-row__value">
            {Math.round(battery.percent)}% {battery.plugged_in ? "⚡" : ""}
          </div>
        </div>
      )}

      {/* Active Window */}
      <div className="os-section">
        <div className="os-section__label">VENTANA ACTIVA</div>
        <div className="os-section__value">{window?.process || "—"}</div>
        <div className="os-section__sub">{window?.title?.slice(0, 60) || ""}</div>
      </div>

      {/* WiFi */}
      {network?.wifi && (
        <div className="os-section">
          <div className="os-section__label">WIFI</div>
          <div className="os-section__value">{network.wifi.ssid || "—"}</div>
          <div className="os-section__sub">
            {network.wifi.signal} · {network.wifi.state}
          </div>
        </div>
      )}

      {/* Network IO */}
      {network?.stats && (
        <div className="os-row">
          <div className="os-row__label">NET</div>
          <div className="os-row__value os-row__value--mono">
            ↓{network.stats.bytes_recv_mb}M ↑{network.stats.bytes_sent_mb}M
          </div>
        </div>
      )}

      {/* Idle */}
      {idleSec !== null && (
        <div className="os-row">
          <div className="os-row__label">IDLE</div>
          <div className="os-row__value os-row__value--mono">{Math.round(idleSec)}s</div>
        </div>
      )}

      {/* Power actions */}
      <div className="os-power-row">
        <button className="os-power-btn" onClick={() => powerAction("lock")}>LOCK</button>
        <button className="os-power-btn" onClick={() => powerAction("sleep")}>SLEEP</button>
        <button className="os-power-btn os-power-btn--danger" onClick={() => powerAction("restart")}>RESTART</button>
      </div>

      <button className="os-refresh" onClick={refresh} disabled={busy}>
        {busy ? "..." : "REFRESH"}
      </button>
    </div>
  );
}
