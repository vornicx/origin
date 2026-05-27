import { useEffect, useState } from "react";

interface LockScreenProps {
  onDismiss: () => void;
}

function pad(n: number) {
  return n.toString().padStart(2, "0");
}

const WEEKDAYS = ["SUN", "MON", "TUE", "WED", "THU", "FRI", "SAT"];
const MONTHS = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"];

export function LockScreen({ onDismiss }: LockScreenProps) {
  const [now, setNow] = useState(() => new Date());

  useEffect(() => {
    const id = setInterval(() => setNow(new Date()), 1000);
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape" || e.key === " " || e.key === "Enter") onDismiss();
    };
    window.addEventListener("keydown", onKey);
    return () => {
      clearInterval(id);
      window.removeEventListener("keydown", onKey);
    };
  }, [onDismiss]);

  const time = `${pad(now.getHours())}:${pad(now.getMinutes())}`;
  const dateLine = `${WEEKDAYS[now.getDay()]} ${now.getDate()} ${MONTHS[now.getMonth()]}`;

  const hexPoints = (() => {
    const cx = 60;
    const cy = 60;
    const r = 50;
    return Array.from({ length: 6 }, (_, i) => {
      const a = (Math.PI / 3) * i - Math.PI / 2;
      return `${cx + r * Math.cos(a)},${cy + r * Math.sin(a)}`;
    }).join(" ");
  })();

  const particles = Array.from({ length: 28 }, (_, i) => {
    const seed = (i * 9301 + 49297) % 233280;
    const x = 12 + ((seed * 31) % 96);
    const y = 12 + ((seed * 17) % 96);
    const delay = (i * 0.13) % 3;
    return <circle key={i} cx={x} cy={y} r={0.8} fill="var(--cyan)" opacity={0.6} style={{ animationDelay: `${delay}s` }} className="lockscreen__particle" />;
  });

  return (
    <div className="lockscreen" onClick={onDismiss} role="button" aria-label="Pulsa para desbloquear">
      <div className="lockscreen__hex-wrap">
        <svg viewBox="0 0 120 120" className="lockscreen__hex" width={180} height={180}>
          <polygon
            points={hexPoints}
            fill="rgba(0, 60, 120, 0.18)"
            stroke="var(--cyan)"
            strokeWidth={1.2}
            style={{ filter: "drop-shadow(0 0 10px var(--cyan))" }}
          />
          {particles}
          <text x={60} y={64} textAnchor="middle" dominantBaseline="central" fill="var(--text-bright)" fontSize={11} letterSpacing={1.4} fontFamily="var(--orb)">
            Origin
          </text>
        </svg>
      </div>
      <div className="lockscreen__time">{time}</div>
      <div className="lockscreen__date">{dateLine}</div>
      <div className="lockscreen__hint">PULSA O PRESIONA ESC PARA CONTINUAR</div>
    </div>
  );
}
