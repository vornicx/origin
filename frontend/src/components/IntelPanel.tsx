import { useState, useEffect, useCallback } from "react";

type IntelTab = "crucix" | "osiris";

interface ServiceStatus {
  online: boolean;
  label: string;
  detail?: string;
}

export function IntelPanel({ initialTab }: { initialTab?: IntelTab }) {
  const [tab, setTab] = useState<IntelTab>(initialTab ?? "crucix");
  const [crucixStatus, setCrucixStatus] = useState<ServiceStatus>({ online: false, label: "CHECKING..." });
  const [osirisStatus, setOsirisStatus] = useState<ServiceStatus>({ online: false, label: "CHECKING..." });
  const [crucixLoaded, setCrucixLoaded] = useState(false);
  const [osirisLoaded, setOsirisLoaded] = useState(false);

  const crucixUrl = resolveSibling(3117);
  const osirisUrl = resolveSibling(3000);

  const checkServices = useCallback(async () => {
    try {
      const r = await fetch(`${crucixUrl}/api/health`, { signal: AbortSignal.timeout(3000) });
      if (r.ok) {
        const d = await r.json();
        setCrucixStatus({
          online: true,
          label: "ONLINE",
          detail: `${d.sourcesOk ?? 0} sources | ${d.sweepInProgress ? "SWEEPING" : "IDLE"}`,
        });
      } else {
        setCrucixStatus({ online: false, label: "ERROR" });
      }
    } catch {
      setCrucixStatus({ online: false, label: "OFFLINE" });
    }

    try {
      const r = await fetch(`${osirisUrl}/api/health`, { signal: AbortSignal.timeout(3000) });
      if (r.ok) {
        setOsirisStatus({ online: true, label: "ONLINE" });
      } else {
        setOsirisStatus({ online: false, label: "ERROR" });
      }
    } catch {
      setOsirisStatus({ online: false, label: "OFFLINE" });
    }
  }, [crucixUrl, osirisUrl]);

  useEffect(() => {
    checkServices();
    const id = setInterval(checkServices, 30000);
    return () => clearInterval(id);
  }, [checkServices]);

  const activeUrl = tab === "crucix" ? crucixUrl : osirisUrl;
  const activeOnline = tab === "crucix" ? crucixStatus.online : osirisStatus.online;

  return (
    <div className="intel-panel">
      {/* Tab bar */}
      <div className="intel-panel__tabs">
        <button
          className={`intel-panel__tab ${tab === "crucix" ? "intel-panel__tab--active" : ""}`}
          onClick={() => setTab("crucix")}
        >
          <span className="intel-panel__tab-icon">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.5} width="14" height="14">
              <circle cx={12} cy={12} r={10} />
              <path d="M12 2v20M2 12h20" />
              <circle cx={12} cy={12} r={4} />
            </svg>
          </span>
          <span>CRUCIX</span>
          <span className={`intel-panel__status-dot ${crucixStatus.online ? "intel-panel__status-dot--on" : ""}`} />
        </button>
        <button
          className={`intel-panel__tab ${tab === "osiris" ? "intel-panel__tab--active" : ""}`}
          onClick={() => setTab("osiris")}
        >
          <span className="intel-panel__tab-icon">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.5} width="14" height="14">
              <path d="M12 2L2 7l10 5 10-5-10-5z" />
              <path d="M2 17l10 5 10-5" />
              <path d="M2 12l10 5 10-5" />
            </svg>
          </span>
          <span>OSIRIS</span>
          <span className={`intel-panel__status-dot ${osirisStatus.online ? "intel-panel__status-dot--on" : ""}`} />
        </button>

        <div className="intel-panel__tab-info">
          {tab === "crucix" && crucixStatus.detail && (
            <span className="intel-panel__tab-detail">{crucixStatus.detail}</span>
          )}
        </div>

        <button className="intel-panel__tab intel-panel__tab--action" onClick={checkServices} title="Refresh status">
          ↻
        </button>
      </div>

      {/* Content */}
      <div className="intel-panel__content">
        {!activeOnline ? (
          <div className="intel-panel__offline">
            <div className="intel-panel__offline-icon">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.5} width="48" height="48">
                <circle cx={12} cy={12} r={10} />
                <path d="M15 9l-6 6M9 9l6 6" />
              </svg>
            </div>
            <div className="intel-panel__offline-title">
              {tab === "crucix" ? "CRUCIX" : "OSIRIS"} OFFLINE
            </div>
            <div className="intel-panel__offline-hint">
              {tab === "crucix" ? (
                <>Start with: <code>cd crucix && node server.mjs</code><br />Or: <code>origin crucix</code></>
              ) : (
                <>Start with: <code>cd osiris && npm run dev</code><br />Or: <code>origin osiris</code></>
              )}
            </div>
            <button className="intel-panel__offline-retry" onClick={checkServices}>RETRY CONNECTION</button>
          </div>
        ) : (
          <>
            {/* Crucix iframe — keep mounted once loaded to preserve state */}
            <iframe
              src={crucixUrl}
              className="intel-panel__frame"
              title="Crucix Intelligence Engine"
              style={{ display: tab === "crucix" ? "block" : "none" }}
              onLoad={() => setCrucixLoaded(true)}
            />
            {/* Osiris iframe */}
            <iframe
              src={osirisUrl}
              className="intel-panel__frame"
              title="Osiris OSINT Dashboard"
              style={{ display: tab === "osiris" ? "block" : "none" }}
              onLoad={() => setOsirisLoaded(true)}
            />
            {/* Loading overlay */}
            {((tab === "crucix" && !crucixLoaded) || (tab === "osiris" && !osirisLoaded)) && (
              <div className="intel-panel__loading">
                <div className="intel-panel__loading-spinner" />
                <span>LOADING {tab === "crucix" ? "CRUCIX" : "OSIRIS"}...</span>
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
}

function resolveSibling(port: number): string {
  if (typeof window === "undefined") return `http://localhost:${port}`;
  const host = window.location.hostname || "localhost";
  const proto = window.location.protocol === "https:" ? "https:" : "http:";
  return `${proto}//${host}:${port}`;
}
