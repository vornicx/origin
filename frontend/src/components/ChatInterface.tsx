import { useState, useEffect, useRef, useCallback } from "react";
import { Message, WSMessage } from "../types";
import { useWebSocket } from "../hooks/useWebSocket";
import { ContextTags } from "./ContextTags";
import { MarkdownRenderer } from "./MarkdownRenderer";
import { WakeWordIndicator } from "./WakeWordIndicator";
import { apiBase } from "../config/api";

function genId() {
  return Math.random().toString(36).slice(2);
}

function formatTime(iso: string) {
  return new Date(iso).toLocaleTimeString("en-GB", {
    hour: "2-digit",
    minute: "2-digit",
  });
}

interface MonitorToast {
  id: string;
  title: string;
  message: string;
  severity: string;
}

const API_BASE = apiBase();

// ── Persistence ──────────────────────────────────────────────
const STORAGE_KEY = "origin_chat_history";
const MAX_STORED_MESSAGES = 200;

function loadHistory(): Message[] {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    // Validate basic structure
    return parsed.filter(
      (m: any) =>
        m &&
        typeof m.id === "string" &&
        (m.role === "user" || m.role === "assistant") &&
        typeof m.content === "string"
    );
  } catch {
    return [];
  }
}

function saveHistory(messages: Message[]) {
  try {
    // Only persist user + assistant (not status), cap at MAX
    const persistable = messages
      .filter((m) => m.role === "user" || m.role === "assistant")
      .slice(-MAX_STORED_MESSAGES);
    localStorage.setItem(STORAGE_KEY, JSON.stringify(persistable));
  } catch {
    // Storage full or unavailable — silently ignore
  }
}

// ── Step labels for reasoning progress ───────────────────────
const STEP_LABELS: Record<string, string> = {
  intent: "INTENT",
  plan: "PLAN",
  act: "ACT",
  check: "CHECK",
  save: "SAVE",
  answer: "ANSWER",
};

interface ReasoningProgress {
  step: string;
  stepNumber: number;
  totalSteps: number;
  status: "running" | "done";
  detail?: string;
}

// ── Web Speech API check ─────────────────────────────────────
const SpeechRecognition =
  (window as any).SpeechRecognition || (window as any).webkitSpeechRecognition;

export function ChatInterface() {
  const [messages, setMessages] = useState<Message[]>(loadHistory);
  const [input, setInput] = useState("");
  const [isThinking, setIsThinking] = useState(false);
  const [reasoningProgress, setReasoningProgress] = useState<ReasoningProgress | null>(null);
  const [toasts, setToasts] = useState<MonitorToast[]>([]);
  const [isListening, setIsListening] = useState(false);
  const [voiceMode, setVoiceMode] = useState(false);
  const voiceModeRef = useRef(voiceMode);
  const bottomRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const recognitionRef = useRef<any>(null);
  const audioRef = useRef<HTMLAudioElement | null>(null);

  // Keep ref in sync so handleWSMessage stays stable across voiceMode toggles
  useEffect(() => { voiceModeRef.current = voiceMode; }, [voiceMode]);

  const handleWSMessage = useCallback((msg: WSMessage) => {
    if (msg.type === "status") {
      setIsThinking(true);
      setReasoningProgress(null);
    } else if (msg.type === "reasoning_step") {
      const step = msg.step ?? "";
      const status = msg.data?.status ?? "running";
      let detail = "";
      if (step === "intent" && status === "done") {
        detail = msg.data?.intent ?? "";
      } else if (step === "act" && status === "done") {
        const skills = msg.data?.skills_executed ?? [];
        detail = skills.length > 0 ? skills.join(", ") : "";
      }
      setReasoningProgress({
        step,
        stepNumber: msg.step_number ?? 1,
        totalSteps: msg.total_steps ?? 6,
        status,
        detail,
      });
    } else if (msg.type === "reasoning") {
      // Legacy format — still supported
      setReasoningProgress(null);
    } else if (msg.type === "answer") {
      setIsThinking(false);
      setReasoningProgress(null);
      const answer = msg.content ?? "";
      if (typeof msg.latency_ms === "number") {
        window.dispatchEvent(new CustomEvent("origin:latency", { detail: msg.latency_ms }));
      }
      if (typeof msg.tokens_per_s === "number" && msg.tokens_per_s > 0) {
        window.dispatchEvent(new CustomEvent("origin:tokens_per_s", { detail: msg.tokens_per_s }));
      }
      setMessages((prev) => [
        ...prev.filter((m) => m.role !== "status"),
        {
          id: genId(),
          role: "assistant",
          content: answer,
          timestamp: msg.timestamp ?? new Date().toISOString(),
          cycleId: msg.cycle_id,
          skillsUsed: msg.skills_used,
        },
      ]);
      // Auto-TTS if voice mode is on (use ref to avoid recreating this callback)
      if (voiceModeRef.current && answer) {
        speakResponse(answer);
      }
    } else if (msg.type === "monitor_alert" && msg.alert) {
      const toast: MonitorToast = {
        id: genId(),
        title: msg.alert.title,
        message: msg.alert.message,
        severity: msg.alert.severity,
      };
      setToasts((prev) => [...prev.slice(-4), toast]);
      setTimeout(() => {
        setToasts((prev) => prev.filter((t) => t.id !== toast.id));
      }, 5000);
    } else if (msg.type === "wake_word_event") {
      // Wake word events are handled by WakeWordIndicator directly
      // but we surface command completions as messages too
      if (msg.event === "processing" && msg.command) {
        setMessages((prev) => [
          ...prev,
          {
            id: genId(),
            role: "user",
            content: `[🎤 Voz] ${msg.command}`,
            timestamp: new Date().toISOString(),
          },
        ]);
      }
    } else if (msg.type === "subconscious_thought" && msg.content) {
      // Subconscious insight surfaced as a subtle toast
      const toast: MonitorToast = {
        id: genId(),
        title: `Subconscious [${msg.thought_type || "insight"}]`,
        message: msg.content,
        severity: "info",
      };
      setToasts((prev) => [...prev.slice(-4), toast]);
      setTimeout(() => {
        setToasts((prev) => prev.filter((t) => t.id !== toast.id));
      }, 8000);
    } else if (msg.type === "proactive_suggestion" && msg.proactive_event) {
      // Proactive contextual suggestion (window change, clipboard, etc.)
      const ev = msg.proactive_event;
      const toast: MonitorToast = {
        id: genId(),
        title: `Origin · ${ev.title}`,
        message: ev.message,
        severity: ev.severity === "high" ? "error" : ev.severity === "medium" ? "warning" : "info",
      };
      setToasts((prev) => [...prev.slice(-4), toast]);
      setTimeout(() => {
        setToasts((prev) => prev.filter((t) => t.id !== toast.id));
      }, 10000);
    } else if (msg.type === "error") {
      setIsThinking(false);
      setReasoningProgress(null);
      setMessages((prev) => [
        ...prev.filter((m) => m.role !== "status"),
        {
          id: genId(),
          role: "assistant",
          content: `[Error] ${msg.message}`,
          timestamp: new Date().toISOString(),
        },
      ]);
    }
  }, []);

  const { status, send } = useWebSocket(handleWSMessage);

  // ── Persist messages to localStorage ────────────────────
  useEffect(() => {
    saveHistory(messages);
  }, [messages]);

  const clearHistory = useCallback(() => {
    setMessages([]);
    localStorage.removeItem(STORAGE_KEY);
  }, []);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, isThinking]);

  // ── TTS: speak response via API ──────────────────────────
  const speakResponse = useCallback(async (text: string) => {
    try {
      const res = await fetch(`${API_BASE}/voice/speak`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text, preset: "origin" }),
      });
      const data = await res.json();
      if (data.audio_b64) {
        const audioBlob = Uint8Array.from(atob(data.audio_b64), (c) => c.charCodeAt(0));
        const blob = new Blob([audioBlob], { type: "audio/mpeg" });
        const url = URL.createObjectURL(blob);
        if (audioRef.current) {
          audioRef.current.src = url;
          audioRef.current.play().catch(() => {});
        }
      }
    } catch (e) {
      console.error("TTS error:", e);
    }
  }, []);

  // ── STT: microphone input via Web Speech API ─────────────
  const toggleMic = useCallback(() => {
    if (!SpeechRecognition) {
      alert("Speech Recognition not supported in this browser");
      return;
    }

    if (isListening) {
      // Stop
      recognitionRef.current?.stop();
      setIsListening(false);
      return;
    }

    // Start listening
    const recognition = new SpeechRecognition();
    recognition.lang = "es-ES";
    recognition.interimResults = true;
    recognition.maxAlternatives = 1;
    recognition.continuous = false;

    recognition.onstart = () => setIsListening(true);

    recognition.onresult = (event: any) => {
      const last = event.results[event.results.length - 1];
      const transcript = last[0].transcript;
      if (last.isFinal) {
        setInput(transcript);
        // Auto-send in voice mode
        if (voiceMode && transcript.trim()) {
          const userMsg: Message = {
            id: genId(),
            role: "user",
            content: transcript.trim(),
            timestamp: new Date().toISOString(),
          };
          setMessages((prev) => [...prev, userMsg]);
          setInput("");
          send(transcript.trim());
        }
      } else {
        setInput(transcript);
      }
    };

    recognition.onerror = () => setIsListening(false);
    recognition.onend = () => setIsListening(false);

    recognitionRef.current = recognition;
    recognition.start();
  }, [isListening, voiceMode, send]);

  const handleSend = useCallback(() => {
    const text = input.trim();
    if (!text || status !== "connected" || isThinking) return;

    const userMsg: Message = {
      id: genId(),
      role: "user",
      content: text,
      timestamp: new Date().toISOString(),
    };

    setMessages((prev) => [...prev, userMsg]);
    setInput("");
    send(text);
  }, [input, status, isThinking, send]);

  // ── Textarea auto-resize ─────────────────────────────────
  const autoResize = useCallback(() => {
    const el = inputRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 160)}px`;
  }, []);

  useEffect(() => {
    autoResize();
  }, [input, autoResize]);

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  const userMessages = messages.filter((m) => m.role !== "status");

  return (
    <>
    {toasts.length > 0 && (
      <div className="monitor-toast">
        {toasts.map((t) => (
          <div key={t.id} className={`monitor-toast__item ${t.severity === "critical" ? "monitor-toast__item--critical" : ""}`}>
            <strong>{t.title}</strong><br />
            {t.message}
          </div>
        ))}
      </div>
    )}
    {/* Hidden audio element for TTS playback */}
    <audio ref={audioRef} style={{ display: "none" }} />
    <div className="chat-layout">
      <header className="chat-header">
        <div className="chat-header__title">
          <span className="chat-header__name">Origin</span>
          <span className="chat-header__sub">Just A Rather Very Intelligent System</span>
        </div>
        <div className="chat-header__right">
          <WakeWordIndicator />
          <ContextTags
            connectionStatus={status}
            messageCount={userMessages.length}
            mode={voiceMode ? "voice" : "focus"}
          />
          {userMessages.length > 0 && (
            <button
              className="btn-clear"
              onClick={clearHistory}
              title="Clear conversation history"
            >
              CLR
            </button>
          )}
        </div>
      </header>

      <main className="chat-messages">
        {userMessages.length === 0 && (
          <div className="chat-empty">
            <p className="chat-empty__line">System online.</p>
            <p className="chat-empty__line chat-empty__line--dim">
              Ready when you are, Vadim.
            </p>
          </div>
        )}

        {messages.map((msg) => (
          <div key={msg.id} className={`message message--${msg.role}`}>
            <div className="message__meta">
              <span className="message__role">
                {msg.role === "user" ? "vadim" : "origin"}
              </span>
              <span className="message__time">{formatTime(msg.timestamp)}</span>
              {msg.cycleId && (
                <span className="message__cycle" title={msg.cycleId}>
                  #{msg.cycleId.slice(0, 8)}
                </span>
              )}
            </div>
            <div className="message__content">
              {msg.role === "assistant" ? (
                <MarkdownRenderer content={msg.content} />
              ) : (
                msg.content
              )}
            </div>
            {msg.skillsUsed && msg.skillsUsed.length > 0 && (
              <div className="message__skills">
                {msg.skillsUsed.map((s) => (
                  <span key={s} className="skill-tag">{s}</span>
                ))}
              </div>
            )}
            {/* Speaker icon on assistant messages for manual TTS */}
            {msg.role === "assistant" && msg.content && (
              <button
                className="message__speak-btn"
                onClick={() => speakResponse(msg.content)}
                title="Speak this message"
              >
                &#9835;
              </button>
            )}
          </div>
        ))}

        {isThinking && (
          <div className="message message--assistant">
            <div className="message__meta">
              <span className="message__role">origin</span>
            </div>
            {reasoningProgress ? (
              <div className="message__content reasoning-progress">
                <div className="reasoning-progress__steps">
                  {Object.keys(STEP_LABELS).map((s) => {
                    const idx = Object.keys(STEP_LABELS).indexOf(s) + 1;
                    const isCurrent = s === reasoningProgress.step;
                    const isDone = reasoningProgress.stepNumber > idx
                      || (isCurrent && reasoningProgress.status === "done");
                    const isSkipped = reasoningProgress.totalSteps < 6 && s === "check";
                    let cls = "reasoning-step";
                    if (isSkipped) cls += " reasoning-step--skipped";
                    else if (isDone) cls += " reasoning-step--done";
                    else if (isCurrent && reasoningProgress.status === "running") cls += " reasoning-step--active";
                    return (
                      <span key={s} className={cls}>
                        {STEP_LABELS[s]}
                      </span>
                    );
                  })}
                </div>
                {reasoningProgress.detail && (
                  <div className="reasoning-progress__detail">
                    {reasoningProgress.detail}
                  </div>
                )}
                <div className="reasoning-progress__bar">
                  <div
                    className="reasoning-progress__fill"
                    style={{
                      width: `${(reasoningProgress.stepNumber / reasoningProgress.totalSteps) * 100}%`,
                    }}
                  />
                </div>
              </div>
            ) : (
              <div className="message__content thinking">
                <span />
                <span />
                <span />
              </div>
            )}
          </div>
        )}

        <div ref={bottomRef} />
      </main>

      <footer className="chat-input-area">
        <button
          className={`btn-mic ${isListening ? "btn-mic--active" : ""}`}
          onClick={toggleMic}
          disabled={status !== "connected" || isThinking}
          title={isListening ? "Stop listening" : "Start voice input"}
        >
          {isListening ? "||" : "MIC"}
        </button>
        <textarea
          ref={inputRef}
          className="chat-input"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder={
            isListening
              ? "Listening..."
              : status === "connected"
              ? "Enter input — Shift+Enter for newline"
              : status === "connecting"
              ? "Connecting..."
              : "Offline — reconnecting..."
          }
          disabled={status !== "connected" || isThinking}
          rows={1}
        />
        <button
          className="chat-send"
          onClick={handleSend}
          disabled={!input.trim() || status !== "connected" || isThinking}
        >
          Send
        </button>
        <button
          className={`btn-voice-mode ${voiceMode ? "btn-voice-mode--active" : ""}`}
          onClick={() => setVoiceMode((v) => !v)}
          title={voiceMode ? "Disable voice mode (auto TTS)" : "Enable voice mode (auto TTS)"}
        >
          {voiceMode ? "VOX" : "TXT"}
        </button>
      </footer>
    </div>
    </>
  );
}
