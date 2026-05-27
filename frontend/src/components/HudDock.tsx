interface HudDockProps {
  showLeft: boolean;
  showCamera: boolean;
  showRight: boolean;
  onToggleLeft: () => void;
  onToggleCamera: () => void;
  onToggleRight: () => void;
  onLock: () => void;
  onMusic?: () => void;
}

// Lineart icons (Iron Man HUD style — thin strokes, no fill)
function IconPanelLeft() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.5} strokeLinecap="round" strokeLinejoin="round">
      <rect x={3} y={4} width={18} height={16} rx={1.5} />
      <path d="M9 4v16" />
    </svg>
  );
}

function IconSystems() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.5} strokeLinecap="round" strokeLinejoin="round">
      <rect x={3} y={4} width={18} height={14} rx={1} />
      <path d="M3 9h18M8 4v14" />
    </svg>
  );
}

function IconCamera() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.5} strokeLinecap="round" strokeLinejoin="round">
      <path d="M4 7h4l1.5-2h5L16 7h4v12H4z" />
      <circle cx={12} cy={13} r={3.5} />
    </svg>
  );
}

function IconMusic() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.5} strokeLinecap="round" strokeLinejoin="round">
      <path d="M9 18V6l10-2v12" />
      <circle cx={7} cy={18} r={2.2} />
      <circle cx={17} cy={16} r={2.2} />
    </svg>
  );
}

function IconLock() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.5} strokeLinecap="round" strokeLinejoin="round">
      <rect x={5} y={11} width={14} height={9} rx={1} />
      <path d="M8 11V7a4 4 0 0 1 8 0v4" />
    </svg>
  );
}

export function HudDock({
  showLeft,
  showCamera,
  showRight,
  onToggleLeft,
  onToggleCamera,
  onToggleRight,
  onLock,
  onMusic,
}: HudDockProps) {
  return (
    <div className="hud-dock" role="toolbar" aria-label="Dock HUD">
      <button
        className={`hud-dock__btn ${showLeft ? "hud-dock__btn--active" : ""}`}
        onClick={onToggleLeft}
        title="Panel izquierdo (núcleo + memoria)"
      >
        <IconPanelLeft />
      </button>
      <button
        className={`hud-dock__btn ${showCamera ? "hud-dock__btn--active" : ""}`}
        onClick={onToggleCamera}
        title="Cámara"
      >
        <IconCamera />
      </button>
      <button
        className={`hud-dock__btn ${showRight ? "hud-dock__btn--active" : ""}`}
        onClick={onToggleRight}
        title="Sistemas (panel derecho)"
      >
        <IconSystems />
      </button>
      <button
        className="hud-dock__btn"
        onClick={onMusic ?? (() => {})}
        title="Música"
        disabled={!onMusic}
      >
        <IconMusic />
      </button>
      <button className="hud-dock__btn" onClick={onLock} title="Bloquear ahora">
        <IconLock />
      </button>
    </div>
  );
}
