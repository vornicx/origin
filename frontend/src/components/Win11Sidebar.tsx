import { useState } from "react";

export type NavSection = "home" | "chat" | "voice" | "systems" | "camera" | "music" | "intel" | "settings";

interface Win11SidebarProps {
  active: NavSection;
  onChange: (section: NavSection) => void;
}

interface NavItem {
  id: NavSection;
  label: string;
  icon: JSX.Element;
}

const sw = 1.5;

const NAV_ITEMS: NavItem[] = [
  {
    id: "home",
    label: "Inicio",
    icon: (
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={sw} strokeLinecap="round" strokeLinejoin="round">
        <path d="M3 12l9-8 9 8" />
        <path d="M5 10v9a1 1 0 001 1h4v-5h4v5h4a1 1 0 001-1v-9" />
      </svg>
    ),
  },
  {
    id: "chat",
    label: "Chat",
    icon: (
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={sw} strokeLinecap="round" strokeLinejoin="round">
        <path d="M4 5h16v11H8l-4 4z" />
      </svg>
    ),
  },
  {
    id: "voice",
    label: "Voz",
    icon: (
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={sw} strokeLinecap="round" strokeLinejoin="round">
        <path d="M12 2a4 4 0 014 4v4a4 4 0 01-8 0V6a4 4 0 014-4z" />
        <path d="M6 12a6 6 0 0012 0" />
        <path d="M12 18v4M8 22h8" />
      </svg>
    ),
  },
  {
    id: "systems",
    label: "Sistemas",
    icon: (
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={sw} strokeLinecap="round" strokeLinejoin="round">
        <rect x={3} y={4} width={18} height={14} rx={1} />
        <path d="M3 9h18M8 4v14" />
      </svg>
    ),
  },
  {
    id: "camera",
    label: "Vision",
    icon: (
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={sw} strokeLinecap="round" strokeLinejoin="round">
        <path d="M4 7h4l1.5-2h5L16 7h4v12H4z" />
        <circle cx={12} cy={13} r={3.5} />
      </svg>
    ),
  },
  {
    id: "music",
    label: "Musica",
    icon: (
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={sw} strokeLinecap="round" strokeLinejoin="round">
        <path d="M9 18V6l10-2v12" />
        <circle cx={7} cy={18} r={2.2} />
        <circle cx={17} cy={16} r={2.2} />
      </svg>
    ),
  },
  {
    id: "intel",
    label: "Intel",
    icon: (
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={sw} strokeLinecap="round" strokeLinejoin="round">
        <circle cx={12} cy={12} r={10} />
        <path d="M12 2v20M2 12h20" />
        <circle cx={12} cy={12} r={4} />
      </svg>
    ),
  },
];

const SETTINGS_ITEM: NavItem = {
  id: "settings",
  label: "Config",
  icon: (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={sw} strokeLinecap="round" strokeLinejoin="round">
      <circle cx={12} cy={12} r={3} />
      <path d="M19.4 15a1.65 1.65 0 00.33 1.82l.06.06a2 2 0 01-2.83 2.83l-.06-.06a1.65 1.65 0 00-1.82-.33 1.65 1.65 0 00-1 1.51V21a2 2 0 01-4 0v-.09A1.65 1.65 0 009 19.4a1.65 1.65 0 00-1.82.33l-.06.06a2 2 0 01-2.83-2.83l.06-.06A1.65 1.65 0 004.68 15a1.65 1.65 0 00-1.51-1H3a2 2 0 010-4h.09A1.65 1.65 0 004.6 9a1.65 1.65 0 00-.33-1.82l-.06-.06a2 2 0 012.83-2.83l.06.06A1.65 1.65 0 009 4.68a1.65 1.65 0 001-1.51V3a2 2 0 014 0v.09a1.65 1.65 0 001 1.51 1.65 1.65 0 001.82-.33l.06-.06a2 2 0 012.83 2.83l-.06.06A1.65 1.65 0 0019.4 9a1.65 1.65 0 001.51 1H21a2 2 0 010 4h-.09a1.65 1.65 0 00-1.51 1z" />
    </svg>
  ),
};

export function Win11Sidebar({ active, onChange }: Win11SidebarProps) {
  const [collapsed, setCollapsed] = useState(false);

  return (
    <nav className={`win-sidebar ${collapsed ? "win-sidebar--collapsed" : ""}`}>
      <button
        className="win-sidebar__toggle"
        onClick={() => setCollapsed((c) => !c)}
        title={collapsed ? "Expandir" : "Colapsar"}
      >
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round">
          <line x1={4} y1={6} x2={20} y2={6} />
          <line x1={4} y1={12} x2={20} y2={12} />
          <line x1={4} y1={18} x2={20} y2={18} />
        </svg>
      </button>

      <div className="win-sidebar__items">
        {NAV_ITEMS.map((item) => (
          <button
            key={item.id}
            className={`win-sidebar__item ${active === item.id ? "win-sidebar__item--active" : ""}`}
            onClick={() => onChange(item.id)}
            title={item.label}
          >
            <span className="win-sidebar__icon">{item.icon}</span>
            {!collapsed && <span className="win-sidebar__label">{item.label}</span>}
          </button>
        ))}
      </div>

      <div className="win-sidebar__bottom">
        <button
          className={`win-sidebar__item ${active === "settings" ? "win-sidebar__item--active" : ""}`}
          onClick={() => onChange("settings")}
          title="Configuracion"
        >
          <span className="win-sidebar__icon">{SETTINGS_ITEM.icon}</span>
          {!collapsed && <span className="win-sidebar__label">{SETTINGS_ITEM.label}</span>}
        </button>
      </div>
    </nav>
  );
}
