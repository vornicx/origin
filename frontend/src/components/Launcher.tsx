import { useCallback } from "react";
import { invoke } from "@tauri-apps/api/core";
import { OriginCore } from "./OriginCore";
import { HudBackground } from "./HudBackground";
import { useGlobalHotkeys } from "../hooks/useGlobalHotkeys";

type SubWindow = "chat" | "systems" | "camera" | "music";

async function openSub(label: SubWindow) {
  try {
    await invoke("open_subwindow", { label });
  } catch (e) {
    console.error("open_subwindow failed:", e);
  }
}

function Icon({ name }: { name: SubWindow | "lock" }) {
  const stroke = "currentColor";
  const sw = 1.5;
  switch (name) {
    case "chat":
      return (
        <svg viewBox="0 0 24 24" fill="none" stroke={stroke} strokeWidth={sw} strokeLinecap="round" strokeLinejoin="round">
          <path d="M4 5h16v11H8l-4 4z" />
        </svg>
      );
    case "systems":
      return (
        <svg viewBox="0 0 24 24" fill="none" stroke={stroke} strokeWidth={sw} strokeLinecap="round" strokeLinejoin="round">
          <rect x={3} y={4} width={18} height={14} rx={1} />
          <path d="M3 9h18M8 4v14" />
        </svg>
      );
    case "camera":
      return (
        <svg viewBox="0 0 24 24" fill="none" stroke={stroke} strokeWidth={sw} strokeLinecap="round" strokeLinejoin="round">
          <path d="M4 7h4l1.5-2h5L16 7h4v12H4z" />
          <circle cx={12} cy={13} r={3.5} />
        </svg>
      );
    case "music":
      return (
        <svg viewBox="0 0 24 24" fill="none" stroke={stroke} strokeWidth={sw} strokeLinecap="round" strokeLinejoin="round">
          <path d="M9 18V6l10-2v12" />
          <circle cx={7} cy={18} r={2.2} />
          <circle cx={17} cy={16} r={2.2} />
        </svg>
      );
    case "lock":
      return (
        <svg viewBox="0 0 24 24" fill="none" stroke={stroke} strokeWidth={sw} strokeLinecap="round" strokeLinejoin="round">
          <rect x={5} y={11} width={14} height={9} rx={1} />
          <path d="M8 11V7a4 4 0 0 1 8 0v4" />
        </svg>
      );
  }
}

export function Launcher() {
  const open = useCallback((label: SubWindow) => () => void openSub(label), []);
  const hide = useCallback(() => {
    void invoke("hide_window").catch(() => {});
  }, []);

  const setWindowMode = useCallback((mode: string) => {
    void invoke("set_window_mode", { mode }).catch((e: unknown) =>
      console.error("set_window_mode failed:", e)
    );
  }, []);

  // Navigate the main webview to a different React route (?window=...).
  // The launcher window is always labeled "main"; we just rewrite the query
  // string in-place — React re-reads it via App.tsx's readWindowKind().
  const navigateRoute = useCallback((route: "launcher" | "hud" | "integrated") => {
    const url = new URL(window.location.href);
    url.searchParams.set("window", route);
    window.location.assign(url.toString());
  }, []);

  const toggleMode = useCallback(() => {
    // From the Launcher (orb) → expand to integrated Win11 app
    setWindowMode("integrated");
    navigateRoute("integrated");
  }, [setWindowMode, navigateRoute]);

  const handleTrayAction = useCallback(
    (action: string) => {
      switch (action) {
        case "mode_orb":
          setWindowMode("orb");
          navigateRoute("launcher");
          break;
        case "mode_integrated":
          setWindowMode("integrated");
          navigateRoute("integrated");
          break;
        case "mode_full":
          setWindowMode("hud");
          navigateRoute("hud");
          break;
        case "show":
          void invoke("show_window").catch(() => {});
          break;
      }
    },
    [setWindowMode, navigateRoute]
  );

  useGlobalHotkeys({
    onToggleMode: toggleMode,
    onTrayAction: handleTrayAction,
  });

  return (
    <div className="launcher" data-tauri-drag-region>
      <HudBackground />

      <div className="launcher__center">
        <OriginCore size={260} showLatency={false} />
      </div>

      {/* Dock orbiting around the core */}
      <div className="launcher__dock">
        <button className="launcher__btn" onClick={open("chat")} title="Chat">
          <Icon name="chat" />
        </button>
        <button className="launcher__btn" onClick={open("camera")} title="Cámara">
          <Icon name="camera" />
        </button>
        <button className="launcher__btn" onClick={open("systems")} title="Sistemas">
          <Icon name="systems" />
        </button>
        <button className="launcher__btn" onClick={open("music")} title="Música">
          <Icon name="music" />
        </button>
      </div>

      <button className="launcher__close" onClick={hide} title="Ocultar (sigue activo en bandeja)">
        ×
      </button>
    </div>
  );
}
