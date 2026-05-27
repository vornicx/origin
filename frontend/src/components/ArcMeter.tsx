interface ArcMeterProps {
  value: number;
  label: string;
  unit?: string;
  size?: number;
  thickness?: number;
  color?: string;
  warnAt?: number;
  critAt?: number;
  subtitle?: string;
}

export function ArcMeter({
  value,
  label,
  unit = "%",
  size = 110,
  thickness = 7,
  warnAt = 80,
  critAt = 90,
  subtitle,
}: ArcMeterProps) {
  const v = Math.min(100, Math.max(0, value));
  const r = size / 2 - thickness;
  const C = 2 * Math.PI * r;

  // 270° visible arc, 90° gap at bottom
  const arcLen = C * 0.75;
  const arcGap = C * 0.25;
  const fillLen = arcLen * (v / 100);
  const fillGap = C - fillLen;

  const color =
    v >= critAt
      ? "var(--red)"
      : v >= warnAt
      ? "var(--amber)"
      : "var(--cyan)";

  const glow =
    v >= critAt
      ? "drop-shadow(0 0 6px var(--red))"
      : v >= warnAt
      ? "drop-shadow(0 0 6px var(--amber))"
      : "drop-shadow(0 0 6px var(--cyan))";

  const rotate = `rotate(135deg)`;
  const origin = `${size / 2}px ${size / 2}px`;

  return (
    <div className="arc-meter">
      <svg width={size} height={size} className="arc-meter__svg">
        {/* Track */}
        <circle
          cx={size / 2}
          cy={size / 2}
          r={r}
          fill="none"
          stroke="rgba(0,160,220,0.08)"
          strokeWidth={thickness}
          strokeLinecap="round"
          strokeDasharray={`${arcLen} ${arcGap}`}
          style={{ transform: rotate, transformOrigin: origin }}
        />
        {/* Fill */}
        {v > 0 && (
          <circle
            cx={size / 2}
            cy={size / 2}
            r={r}
            fill="none"
            stroke={color}
            strokeWidth={thickness}
            strokeLinecap="round"
            strokeDasharray={`${fillLen} ${fillGap}`}
            style={{
              transform: rotate,
              transformOrigin: origin,
              filter: glow,
              transition: "stroke-dasharray 0.8s cubic-bezier(0.4,0,0.2,1)",
            }}
          />
        )}
        {/* Value */}
        <text
          x={size / 2}
          y={size / 2 - 2}
          textAnchor="middle"
          dominantBaseline="middle"
          className="arc-meter__value"
          fill={color}
        >
          {v.toFixed(0)}
        </text>
        <text
          x={size / 2}
          y={size / 2 + 15}
          textAnchor="middle"
          className="arc-meter__unit"
          fill="var(--text-dim)"
        >
          {unit}
        </text>
      </svg>
      <div className="arc-meter__label">{label}</div>
      {subtitle && <div className="arc-meter__sub">{subtitle}</div>}
    </div>
  );
}
