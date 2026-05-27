/**
 * useWindowMode — Manages the Origin overlay's visual mode and click-through.
 *
 * Modes:
 *   - "orb"  : small floating sphere (200×200), click-through when idle
 *   - "full" : full chat UI (640×800), focusable
 *
 * State machine:
 *   voice idle  + no interaction for >5s + mode=orb  → click-through ON
 *   voice listening/thinking/speaking                → mode=full, click-through OFF
 *   user click on hit-zone                            → mode=full, click-through OFF
 *
 * In browser mode, this is a no-op.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { isTauri } from "../config/api";

export type WindowMode = "orb" | "hud";

interface UseWindowModeOpts {
  /** Initial mode. Default "orb". */
  initial?: WindowMode;
  /** Voice state from useVoiceStream — drives auto-mode transitions. */
  voiceState?: "idle" | "listening" | "thinking" | "speaking" | "muted" | "error";
  /** Idle timeout in ms before returning to orb + click-through. */
  idleTimeoutMs?: number;
}

export function useWindowMode(opts: UseWindowModeOpts = {}) {
  const { initial = "orb", voiceState, idleTimeoutMs = 8000 } = opts;
  const [mode, setMode] = useState<WindowMode>(initial);
  const [clickThrough, setClickThrough] = useState(false);
  const idleTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const lastInteraction = useRef<number>(Date.now());

  // Call Rust command to set click-through
  const applyClickThrough = useCallback(async (enabled: boolean) => {
    if (!isTauri()) return;
    try {
      const { invoke } = await import("@tauri-apps/api/core");
      await invoke("set_click_through", { enabled });
      setClickThrough(enabled);
    } catch (e) {
      console.warn("[useWindowMode] set_click_through failed:", e);
    }
  }, []);

  // Call Rust command to set window mode (visual only; the React side scales)
  const applyMode = useCallback(async (newMode: WindowMode) => {
    setMode(newMode);
    if (!isTauri()) return;
    try {
      const { invoke } = await import("@tauri-apps/api/core");
      await invoke("set_window_mode", { mode: newMode });
    } catch (e) {
      console.warn("[useWindowMode] set_window_mode failed:", e);
    }
  }, []);

  // Toggle between orb and command panel
  const toggleMode = useCallback(() => {
    const next = mode === "orb" ? "hud" : "orb";
    void applyMode(next);
    // When expanding to full, ensure interactive
    if (next === "hud") {
      void applyClickThrough(false);
    }
    lastInteraction.current = Date.now();
  }, [mode, applyMode, applyClickThrough]);

  // Mark user interaction (resets idle timer)
  const markInteraction = useCallback(() => {
    lastInteraction.current = Date.now();
    if (clickThrough) {
      void applyClickThrough(false);
    }
  }, [clickThrough, applyClickThrough]);

  // Auto-transitions based on voice state
  useEffect(() => {
    if (!voiceState) return;
    const active = voiceState === "listening" || voiceState === "speaking" || voiceState === "thinking";
    if (active) {
      // Active conversation → ensure full mode + interactive
      if (mode === "orb") {
        void applyMode("hud");
      }
      if (clickThrough) {
        void applyClickThrough(false);
      }
      lastInteraction.current = Date.now();
    }
  }, [voiceState, mode, clickThrough, applyMode, applyClickThrough]);

  // Idle timer: after timeout of no activity AND voice idle → orb + click-through
  useEffect(() => {
    if (!isTauri()) return;
    if (idleTimer.current) clearTimeout(idleTimer.current);

    const checkIdle = () => {
      const elapsed = Date.now() - lastInteraction.current;
      if (elapsed >= idleTimeoutMs && voiceState === "idle" && mode === "hud") {
        // Don't auto-shrink while user is doing things; only after long idle
        // For Phase 1 we DON'T auto-shrink → keep full mode visible
        // (Phase 2 will enable this behavior with explicit user opt-in)
      }
    };

    idleTimer.current = setTimeout(checkIdle, idleTimeoutMs);
    return () => {
      if (idleTimer.current) clearTimeout(idleTimer.current);
    };
  }, [voiceState, mode, idleTimeoutMs]);

  return {
    mode,
    clickThrough,
    setMode: applyMode,
    setClickThrough: applyClickThrough,
    toggleMode,
    markInteraction,
    isTauri: isTauri(),
  };
}
