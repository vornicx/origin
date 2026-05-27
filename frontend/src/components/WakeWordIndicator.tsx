import { useState, useEffect, useCallback, useRef } from "react";
import { apiBase } from "../config/api";

const API_BASE = apiBase();

type WakeState = "idle" | "wake_detected" | "listening_command" | "processing" | "speaking" | "stopped";

interface WakeWordEvent {
  type: "wake_word_event";
  event: string;
  state: WakeState;
  trigger?: string;
  command?: string;
  answer?: string;
}

const STATE_LABELS: Record<WakeState, string> = {
  idle: "Escuchando...",
  wake_detected: "¡Detectado!",
  listening_command: "Escuchando comando...",
  processing: "Procesando...",
  speaking: "Hablando...",
  stopped: "Inactivo",
};

const STATE_COLORS: Record<WakeState, string> = {
  idle: "#0078d4",
  wake_detected: "#ffaa00",
  listening_command: "#00b294",
  processing: "#8b5cf6",
  speaking: "#059669",
  stopped: "#6b7280",
};

interface Props {
  onWakeEvent?: (event: WakeWordEvent) => void;
}

export function WakeWordIndicator({ onWakeEvent }: Props) {
  const [isActive, setIsActive] = useState(false);
  const [state, setState] = useState<WakeState>("stopped");
  const [lastCommand, setLastCommand] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Track incoming WS wake events from the main WS connection
  // (forwarded from ChatInterface via onWakeEvent prop)

  useEffect(() => {
    // Load current status on mount
    fetch(`${API_BASE}/voice/wake-word/status`)
      .then((r) => r.json())
      .then((data) => {
        setIsActive(data.running || false);
        if (data.running) setState("idle");
      })
      .catch(() => {});
  }, []);

  // Handle incoming WS wake word events (forwarded from parent)
  const handleExternalEvent = useCallback(
    (msg: any) => {
      if (msg.type !== "wake_word_event") return;
      const ev = msg as WakeWordEvent;
      setState(ev.state as WakeState);
      if (ev.command) setLastCommand(ev.command);
      onWakeEvent?.(ev);
    },
    [onWakeEvent]
  );

  // Expose handleExternalEvent for parent to call
  const handlerRef = useRef(handleExternalEvent);
  handlerRef.current = handleExternalEvent;

  const toggle = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      if (isActive) {
        await fetch(`${API_BASE}/voice/wake-word/stop`, { method: "POST" });
        setIsActive(false);
        setState("stopped");
        setLastCommand(null);
      } else {
        const res = await fetch(`${API_BASE}/voice/wake-word/start`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ language: "es-ES" }),
        });
        const data = await res.json();
        if (data.result?.status === "started" || data.result?.status === "already_running") {
          setIsActive(true);
          setState("idle");
        } else {
          setError(data.result?.error || data.error || "Error iniciando modo voz");
        }
      }
    } catch (err: any) {
      setError(`Error: ${err.message}`);
    } finally {
      setLoading(false);
    }
  }, [isActive]);

  const color = STATE_COLORS[state];
  const isListening = state !== "stopped";

  return (
    <div className="wake-word-indicator">
      <button
        className={`wake-btn ${isActive ? "wake-btn--active" : ""} ${loading ? "wake-btn--loading" : ""}`}
        onClick={toggle}
        disabled={loading}
        title={isActive ? `Hey Origin activo — ${STATE_LABELS[state]}` : "Activar modo voz (Hey Origin)"}
        style={{ "--wake-color": color } as any}
      >
        <span className={`wake-btn__dot ${isListening ? "wake-btn__dot--pulse" : ""}`} />
        <span className="wake-btn__label">
          {loading ? "..." : isActive ? STATE_LABELS[state] : "VOZ"}
        </span>
      </button>

      {lastCommand && isActive && (
        <div className="wake-cmd-preview" title={lastCommand}>
          "{lastCommand.slice(0, 40)}{lastCommand.length > 40 ? "…" : ""}"
        </div>
      )}

      {error && (
        <div className="wake-error" title={error}>
          ⚠ {error.slice(0, 60)}
        </div>
      )}
    </div>
  );
}
