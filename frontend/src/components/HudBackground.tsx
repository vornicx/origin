export function HudBackground() {
  return (
    <div className="hud-bg" aria-hidden="true">
      <div className="hud-bg__grid" />
      <div className="hud-bg__scanline" />
      <div className="hud-bg__vignette" />

      {/* Top perimeter rule (Huw-style measurement ticks) */}
      <div className="hud-bg__rule hud-bg__rule--top">
        <div className="hud-bg__ticks" />
      </div>

      {/* Center trapezoidal top frame */}
      <svg className="hud-bg__top-frame" viewBox="0 0 400 40" preserveAspectRatio="xMidYMin meet" aria-hidden="true">
        <path
          d="M0 28 L150 28 L170 12 L230 12 L250 28 L400 28"
          fill="none"
          stroke="rgba(0, 212, 255, 0.55)"
          strokeWidth={1.2}
        />
        <path d="M186 4 L214 4" stroke="rgba(0, 212, 255, 0.5)" strokeWidth={1} />
        <circle cx={200} cy={4} r={1.6} fill="var(--cyan)" />
      </svg>

      {/* Bottom rule */}
      <div className="hud-bg__rule hud-bg__rule--bottom">
        <div className="hud-bg__ticks" />
      </div>
    </div>
  );
}
