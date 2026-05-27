import { useCallback } from "react";
import { isTauri } from "../config/api";

interface Win11TitleBarProps {
  title?: string;
  subtitle?: string;
}

export function Win11TitleBar({ title = "Origin", subtitle }: Win11TitleBarProps) {
  const minimize = useCallback(async () => {
    if (!isTauri()) return;
    const { invoke } = await import("@tauri-apps/api/core");
    await invoke("minimize_to_tray");
  }, []);

  const toggleMaximize = useCallback(async () => {
    if (!isTauri()) return;
    const { getCurrentWindow } = await import("@tauri-apps/api/window");
    const win = getCurrentWindow();
    if (await win.isMaximized()) {
      await win.unmaximize();
    } else {
      await win.maximize();
    }
  }, []);

  const close = useCallback(async () => {
    if (!isTauri()) return;
    const { invoke } = await import("@tauri-apps/api/core");
    await invoke("hide_window");
  }, []);

  return (
    <header className="win-titlebar" data-tauri-drag-region>
      <div className="win-titlebar__left" data-tauri-drag-region>
        <div className="win-titlebar__icon">
          <svg viewBox="0 0 24 24" fill="none" strokeWidth={1.5} stroke="currentColor">
            <circle cx={12} cy={12} r={8} strokeDasharray="4 2" />
            <circle cx={12} cy={12} r={3} />
          </svg>
        </div>
        <span className="win-titlebar__title" data-tauri-drag-region>{title}</span>
        {subtitle && (
          <span className="win-titlebar__subtitle" data-tauri-drag-region>{subtitle}</span>
        )}
      </div>

      {isTauri() && (
        <div className="win-titlebar__controls">
          <button className="win-titlebar__btn" onClick={minimize} title="Minimizar" aria-label="Minimize">
            <svg viewBox="0 0 12 12"><line x1={2} y1={6} x2={10} y2={6} /></svg>
          </button>
          <button className="win-titlebar__btn" onClick={toggleMaximize} title="Maximizar" aria-label="Maximize">
            <svg viewBox="0 0 12 12"><rect x={2} y={2} width={8} height={8} rx={1} fill="none" /></svg>
          </button>
          <button className="win-titlebar__btn win-titlebar__btn--close" onClick={close} title="Cerrar" aria-label="Close">
            <svg viewBox="0 0 12 12">
              <line x1={2.5} y1={2.5} x2={9.5} y2={9.5} />
              <line x1={9.5} y1={2.5} x2={2.5} y2={9.5} />
            </svg>
          </button>
        </div>
      )}
    </header>
  );
}
