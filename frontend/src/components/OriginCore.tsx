import { useEffect, useState } from "react";

interface OriginCoreProps {
  size?: number;
  showLatency?: boolean;
}

const LATENCY_KEY = "origin_latency_pb";
const TPS_KEY = "origin_tps_pb";

export function OriginCore({ size = 200, showLatency = true }: OriginCoreProps) {
  const [lastMs, setLastMs] = useState<number | null>(null);
  const [bestMs, setBestMs] = useState<number | null>(() => {
    const raw = localStorage.getItem(LATENCY_KEY);
    return raw ? Number(raw) : null;
  });
  const [lastTps, setLastTps] = useState<number | null>(null);
  const [bestTps, setBestTps] = useState<number | null>(() => {
    const raw = localStorage.getItem(TPS_KEY);
    return raw ? Number(raw) : null;
  });

  useEffect(() => {
    function onLatency(e: Event) {
      const ms = (e as CustomEvent<number>).detail;
      if (!Number.isFinite(ms) || ms <= 0) return;
      setLastMs(ms);
      setBestMs((prev) => {
        if (prev === null || ms < prev) {
          localStorage.setItem(LATENCY_KEY, String(ms));
          return ms;
        }
        return prev;
      });
    }
    function onTps(e: Event) {
      const tps = (e as CustomEvent<number>).detail;
      if (!Number.isFinite(tps) || tps <= 0) return;
      setLastTps(tps);
      setBestTps((prev) => {
        if (prev === null || tps > prev) {
          localStorage.setItem(TPS_KEY, String(tps));
          return tps;
        }
        return prev;
      });
    }
    window.addEventListener("origin:latency", onLatency);
    window.addEventListener("origin:tokens_per_s", onTps);
    return () => {
      window.removeEventListener("origin:latency", onLatency);
      window.removeEventListener("origin:tokens_per_s", onTps);
    };
  }, []);

  const cx = size / 2;
  const cy = size / 2;
  const rOuter = size * 0.46;
  const rRing = size * 0.36;
  const rInner = size * 0.28;

  const tickCount = 24;
  const ticks = Array.from({ length: tickCount }, (_, i) => {
    const angle = (i / tickCount) * Math.PI * 2 - Math.PI / 2;
    const long = i % 6 === 0;
    const x1 = cx + Math.cos(angle) * rOuter;
    const y1 = cy + Math.sin(angle) * rOuter;
    const x2 = cx + Math.cos(angle) * (rOuter - (long ? 8 : 4));
    const y2 = cy + Math.sin(angle) * (rOuter - (long ? 8 : 4));
    return <line key={i} x1={x1} y1={y1} x2={x2} y2={y2} stroke="var(--cyan)" strokeOpacity={long ? 0.55 : 0.25} strokeWidth={1} />;
  });

  const C = 2 * Math.PI * rRing;
  const arcA = C * 0.25;
  const arcB = C * 0.18;

  return (
    <div className="origin-core origin-core--booting" style={{ width: size, height: size + (showLatency ? 56 : 0) }}>
      <svg width={size} height={size} className="origin-core__svg">
        {/* Outer ticks */}
        <g className="origin-core__ticks">{ticks}</g>

        {/* Inner solid disk */}
        <circle cx={cx} cy={cy} r={rInner} fill="rgba(2,18,42,0.85)" stroke="rgba(0,212,255,0.35)" strokeWidth={1} />

        {/* Halo ring (static, glowing) */}
        <circle
          cx={cx}
          cy={cy}
          r={rRing}
          fill="none"
          stroke="var(--cyan)"
          strokeWidth={2}
          strokeOpacity={0.85}
          style={{ filter: "drop-shadow(0 0 8px var(--cyan))" }}
        />

        {/* Rotating arc A (clockwise) */}
        <g className="origin-core__rot origin-core__rot--cw" style={{ transformOrigin: `${cx}px ${cy}px` }}>
          <circle
            cx={cx}
            cy={cy}
            r={rRing + 6}
            fill="none"
            stroke="var(--cyan)"
            strokeWidth={2}
            strokeLinecap="round"
            strokeDasharray={`${arcA} ${C - arcA}`}
            style={{ filter: "drop-shadow(0 0 6px var(--cyan))" }}
          />
        </g>

        {/* Rotating arc B (counter-clockwise, smaller, inside) */}
        <g className="origin-core__rot origin-core__rot--ccw" style={{ transformOrigin: `${cx}px ${cy}px` }}>
          <circle
            cx={cx}
            cy={cy}
            r={rRing - 8}
            fill="none"
            stroke="var(--cyan)"
            strokeWidth={1.5}
            strokeOpacity={0.7}
            strokeLinecap="round"
            strokeDasharray={`${arcB} ${C - arcB}`}
          />
        </g>

        {/* Origin text */}
        <text
          x={cx}
          y={cy}
          textAnchor="middle"
          dominantBaseline="central"
          className="origin-core__label origin-core__label--boot"
          fill="var(--text-bright)"
          fontSize={size * 0.13}
          letterSpacing={size * 0.012}
        >
          Origin
        </text>

        {/* Inner pulse */}
        <circle cx={cx} cy={cy} r={rInner - 6} fill="none" stroke="var(--cyan)" strokeOpacity={0.18} className="origin-core__pulse" />
      </svg>

      {showLatency && (
        <>
          <div className="origin-core__metrics">
            <span className="origin-core__metric">
              <span className="origin-core__metric-k">LAT</span>
              <span className="origin-core__metric-v">{lastMs !== null ? `${Math.round(lastMs)}ms` : "—"}</span>
            </span>
            <span className="origin-core__metric">
              <span className="origin-core__metric-k">PB</span>
              <span className="origin-core__metric-v origin-core__metric-v--best">{bestMs !== null ? `${Math.round(bestMs)}ms` : "—"}</span>
            </span>
          </div>
          <div className="origin-core__metrics">
            <span className="origin-core__metric">
              <span className="origin-core__metric-k">TPS</span>
              <span className="origin-core__metric-v">{lastTps !== null ? lastTps.toFixed(1) : "—"}</span>
            </span>
            <span className="origin-core__metric">
              <span className="origin-core__metric-k">PB</span>
              <span className="origin-core__metric-v origin-core__metric-v--best">{bestTps !== null ? bestTps.toFixed(1) : "—"}</span>
            </span>
          </div>
        </>
      )}
    </div>
  );
}
