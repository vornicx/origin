import { useEffect } from "react";
import { isTauri } from "../config/api";

export type HotkeyPTTState = "Pressed" | "Released";

export interface HotkeyHandlers {
  onPTT?: (state: HotkeyPTTState) => void;
  onToggleMode?: () => void;
  onTrayAction?: (action: string) => void;
  onBackendReady?: () => void;
  onBackendError?: (err: string) => void;
  onClipboardAnalyze?: () => void;
  onScreenshot?: () => void;
  onQuickQuery?: () => void;
}

export function useGlobalHotkeys(handlers: HotkeyHandlers) {
  useEffect(() => {
    if (!isTauri()) return;

    let unlistenFns: Array<() => void> = [];

    (async () => {
      try {
        const { listen } = await import("@tauri-apps/api/event");

        if (handlers.onPTT) {
          const unlisten = await listen<HotkeyPTTState>("hotkey-ptt", (e) => {
            handlers.onPTT?.(e.payload);
          });
          unlistenFns.push(unlisten);
        }

        if (handlers.onToggleMode) {
          const unlisten = await listen("hotkey-toggle-mode", () => {
            handlers.onToggleMode?.();
          });
          unlistenFns.push(unlisten);
        }

        if (handlers.onTrayAction) {
          const unlisten = await listen<string>("tray-action", (e) => {
            handlers.onTrayAction?.(e.payload);
          });
          unlistenFns.push(unlisten);
        }

        if (handlers.onBackendReady) {
          const unlisten = await listen("backend-ready", () => {
            handlers.onBackendReady?.();
          });
          unlistenFns.push(unlisten);
        }

        if (handlers.onBackendError) {
          const unlisten = await listen<string>("backend-error", (e) => {
            handlers.onBackendError?.(String(e.payload));
          });
          unlistenFns.push(unlisten);
        }

        if (handlers.onClipboardAnalyze) {
          const unlisten = await listen("hotkey-clipboard", () => {
            handlers.onClipboardAnalyze?.();
          });
          unlistenFns.push(unlisten);
        }

        if (handlers.onScreenshot) {
          const unlisten = await listen("hotkey-screenshot", () => {
            handlers.onScreenshot?.();
          });
          unlistenFns.push(unlisten);
        }

        if (handlers.onQuickQuery) {
          const unlisten = await listen("hotkey-quick-query", () => {
            handlers.onQuickQuery?.();
          });
          unlistenFns.push(unlisten);
        }
      } catch (e) {
        console.warn("[useGlobalHotkeys] Tauri event listener setup failed:", e);
      }
    })();

    return () => {
      for (const fn of unlistenFns) {
        try {
          fn();
        } catch {
          // ignore
        }
      }
      unlistenFns = [];
    };
  }, [
    handlers.onPTT,
    handlers.onToggleMode,
    handlers.onTrayAction,
    handlers.onBackendReady,
    handlers.onBackendError,
    handlers.onClipboardAnalyze,
    handlers.onScreenshot,
    handlers.onQuickQuery,
  ]);
}
