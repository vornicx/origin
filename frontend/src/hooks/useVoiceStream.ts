/**
 * useVoiceStream — Hook para conversacion oral en tiempo real con Origin.
 *
 * Captura microfono (16kHz PCM mono via AudioWorklet), lo envia por WebSocket
 * binario al servidor, recibe audio TTS en chunks MP3 y lo reproduce con
 * Web Audio API.
 *
 * Half-duplex: el mic no envia datos mientras Origin habla.
 * Barge-in opcional: si el usuario habla durante TTS, se cancela playback.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { ws as buildWsUrl } from "../config/api";

export type VoiceState = "idle" | "listening" | "thinking" | "speaking" | "muted" | "error";

export type ConnectionState = "disconnected" | "connecting" | "connected" | "error";

export interface VoiceStreamMessage {
  type: string;
  state?: VoiceState;
  previous?: VoiceState;
  text?: string;
  message?: string;
  data?: string; // base64 audio chunk
  is_final?: boolean;
  total_bytes?: number;
  engine?: string;
  format?: string;
  cycle_id?: string;
  think_ms?: number;
  duration_ms?: number;
  config?: {
    sample_rate?: number;
    silence_timeout_ms?: number;
    language?: string;
  };
}

export interface VoiceTranscript {
  role: "user" | "assistant";
  text: string;
  timestamp: number;
  cycleId?: string;
  thinkMs?: number;
  durationMs?: number;
}

interface UseVoiceStreamOptions {
  wsUrl?: string;
  autoConnect?: boolean;
  language?: string;
  micSensitivity?: "low" | "medium" | "high";
  onError?: (error: string) => void;
}

const DEFAULT_SAMPLE_RATE = 16000;

// AudioWorklet inline (runs in audio thread) — converts Float32 → Int16 PCM at 16kHz.
// Handles downsampling when the AudioContext runs at a higher native rate (e.g. 48kHz).
const WORKLET_SOURCE = `
  class PCMProcessor extends AudioWorkletProcessor {
    constructor(options) {
      super();
      this._buffer = [];
      // Output is always 16kHz; batch = 120ms = 1920 samples
      this._batchSize = 1920;
      this._noiseGate = options?.processorOptions?.noiseGate ?? 0.025;
      // Downsampling: step = inputRate / 16000 (e.g. 3.0 for 48k→16k)
      const inputRate = options?.processorOptions?.inputRate ?? 16000;
      this._step = inputRate / 16000;
      this._pos = 0;
    }
    process(inputs) {
      const input = inputs[0];
      if (!input || !input[0]) return true;
      const samples = input[0];
      // Noise gate on raw input
      let sumSq = 0;
      for (let i = 0; i < samples.length; i++) sumSq += samples[i] * samples[i];
      const rms = Math.sqrt(sumSq / Math.max(1, samples.length));
      const gated = rms < this._noiseGate;
      // Decimate to 16kHz output using accumulator-based integer decimation
      for (let i = 0; i < samples.length; i++) {
        this._pos += 1;
        if (this._pos >= this._step) {
          this._pos -= this._step;
          const s = gated ? 0 : Math.max(-1, Math.min(1, samples[i]));
          this._buffer.push(s < 0 ? s * 0x8000 : s * 0x7FFF);
        }
      }
      while (this._buffer.length >= this._batchSize) {
        const chunk = this._buffer.splice(0, this._batchSize);
        const int16 = new Int16Array(chunk);
        this.port.postMessage(int16.buffer, [int16.buffer]);
      }
      return true;
    }
  }
  registerProcessor('pcm-processor', PCMProcessor);
`;

export function useVoiceStream(opts: UseVoiceStreamOptions = {}) {
  const {
    wsUrl,
    autoConnect = false,
    language = "es",
    micSensitivity = "low",
    onError,
  } = opts;

  // Build default WS URL — detects Tauri vs browser via config/api
  const resolvedWsUrl = (() => {
    if (wsUrl) return wsUrl;
    if (typeof window === "undefined") return "";
    return buildWsUrl("/ws/voice/stream");
  })();

  const [voiceState, setVoiceState] = useState<VoiceState>("idle");
  const [connectionState, setConnectionState] = useState<ConnectionState>("disconnected");
  const [muted, setMuted] = useState(false);
  const [transcripts, setTranscripts] = useState<VoiceTranscript[]>([]);
  const [lastError, setLastError] = useState<string>("");
  const [audioLevel, setAudioLevel] = useState(0); // 0-1 mic input level

  // Refs to keep across re-renders
  const wsRef = useRef<WebSocket | null>(null);
  const audioCtxRef = useRef<AudioContext | null>(null);
  const mediaStreamRef = useRef<MediaStream | null>(null);
  const workletNodeRef = useRef<AudioWorkletNode | null>(null);
  const analyserRef = useRef<AnalyserNode | null>(null);
  const sourceNodeRef = useRef<MediaStreamAudioSourceNode | null>(null);
  const silentGainRef = useRef<GainNode | null>(null);
  const playbackQueueRef = useRef<Uint8Array[]>([]);
  const playbackElRef = useRef<HTMLAudioElement | null>(null);
  const playbackTotalBytesRef = useRef<number>(0);
  const playbackReceivedRef = useRef<number>(0);
  const levelRafRef = useRef<number | null>(null);

  // === WebSocket ===

  const sendCommand = useCallback((cmd: object) => {
    const ws = wsRef.current;
    if (!ws || ws.readyState !== WebSocket.OPEN) return;
    try {
      ws.send(JSON.stringify(cmd));
    } catch (e) {
      console.warn("[voice] sendCommand failed:", e);
    }
  }, []);

  const handleMessage = useCallback((msg: VoiceStreamMessage) => {
    switch (msg.type) {
      case "voice_session_ready":
        // Session is ready
        break;
      case "voice_state":
        if (msg.state) setVoiceState(msg.state);
        break;
      case "voice_transcript":
        if (msg.text) {
          setTranscripts((prev) => [
            ...prev.slice(-50),
            {
              role: "user",
              text: msg.text!,
              timestamp: Date.now(),
              durationMs: msg.duration_ms,
            },
          ]);
        }
        break;
      case "voice_answer":
        if (msg.text) {
          setTranscripts((prev) => [
            ...prev.slice(-50),
            {
              role: "assistant",
              text: msg.text!,
              timestamp: Date.now(),
              cycleId: msg.cycle_id,
              thinkMs: msg.think_ms,
            },
          ]);
        }
        break;
      case "voice_audio_start":
        playbackQueueRef.current = [];
        playbackTotalBytesRef.current = msg.total_bytes || 0;
        playbackReceivedRef.current = 0;
        break;
      case "voice_audio_chunk":
        if (msg.data) {
          // Decode base64 → bytes, queue
          const bytes = base64ToBytes(msg.data);
          playbackQueueRef.current.push(bytes);
          playbackReceivedRef.current += bytes.length;
          // If we have enough or final → trigger playback
          if (msg.is_final) {
            void flushPlayback();
          }
        }
        break;
      case "voice_audio_end":
        void flushPlayback();
        break;
      case "voice_audio_cancel":
        stopPlayback();
        break;
      case "voice_error":
        setLastError(msg.message || "Unknown error");
        if (onError) onError(msg.message || "voice error");
        break;
    }
  }, [micSensitivity, onError]);

  const flushPlayback = useCallback(async () => {
    if (playbackQueueRef.current.length === 0) return;

    // Concat all chunks
    const total = playbackQueueRef.current.reduce((s, c) => s + c.length, 0);
    const combined = new Uint8Array(total);
    let offset = 0;
    for (const c of playbackQueueRef.current) {
      combined.set(c, offset);
      offset += c.length;
    }
    playbackQueueRef.current = [];

    // Create blob and play via HTMLAudioElement (handles MP3 natively)
    const blob = new Blob([combined], { type: "audio/mpeg" });
    const url = URL.createObjectURL(blob);

    try {
      // Cleanup any previous element
      if (playbackElRef.current) {
        playbackElRef.current.pause();
        playbackElRef.current.src = "";
      }
      const audio = new Audio(url);
      playbackElRef.current = audio;
      audio.onended = () => {
        URL.revokeObjectURL(url);
        playbackElRef.current = null;
      };
      audio.onerror = (e) => {
        console.warn("[voice] playback error:", e);
        URL.revokeObjectURL(url);
        playbackElRef.current = null;
      };
      await audio.play();
    } catch (e) {
      console.warn("[voice] failed to start playback:", e);
      URL.revokeObjectURL(url);
    }
  }, []);

  const stopPlayback = useCallback(() => {
    if (playbackElRef.current) {
      try {
        playbackElRef.current.pause();
        playbackElRef.current.currentTime = 0;
        playbackElRef.current.src = "";
      } catch {
        // ignore
      }
      playbackElRef.current = null;
    }
    playbackQueueRef.current = [];
  }, []);

  // === Mic capture ===

  const startMicCapture = useCallback(async () => {
    try {
      // Request mic access
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: {
          channelCount: 1,
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: false,
        },
      });
      mediaStreamRef.current = stream;

      // Request 16kHz; browsers may return a different native rate (often 48kHz).
      // The worklet reads the actual rate and decimates to 16kHz before sending.
      const ctx = new AudioContext({ sampleRate: DEFAULT_SAMPLE_RATE });
      audioCtxRef.current = ctx;
      const actualRate = ctx.sampleRate;

      // Load worklet from blob
      const blob = new Blob([WORKLET_SOURCE], { type: "application/javascript" });
      const workletURL = URL.createObjectURL(blob);
      await ctx.audioWorklet.addModule(workletURL);
      URL.revokeObjectURL(workletURL);

      // Connect mic → analyser → worklet
      const source = ctx.createMediaStreamSource(stream);
      sourceNodeRef.current = source;

      const analyser = ctx.createAnalyser();
      analyser.fftSize = 256;
      analyserRef.current = analyser;

      const noiseGate =
        micSensitivity === "high" ? 0.012 :
        micSensitivity === "medium" ? 0.018 :
        0.025;
      const workletNode = new AudioWorkletNode(ctx, "pcm-processor", {
        processorOptions: { noiseGate, inputRate: actualRate },
      });
      workletNodeRef.current = workletNode;

      // Worklet sends Int16 PCM batches via postMessage
      workletNode.port.onmessage = (e: MessageEvent<ArrayBuffer>) => {
        const ws = wsRef.current;
        if (ws && ws.readyState === WebSocket.OPEN) {
          ws.send(e.data);
        }
      };

      source.connect(analyser);
      analyser.connect(workletNode);
      // Connect through a silent gain node to keep the worklet alive.
      // Without a path to the destination some browsers suspend AudioWorkletNodes.
      const silentGain = ctx.createGain();
      silentGain.gain.value = 0;
      workletNode.connect(silentGain);
      silentGain.connect(ctx.destination);
      silentGainRef.current = silentGain;

      // Start audio level monitoring (purely visual)
      const buf = new Uint8Array(analyser.frequencyBinCount);
      const updateLevel = () => {
        if (!analyserRef.current) return;
        analyserRef.current.getByteFrequencyData(buf);
        let sum = 0;
        for (let i = 0; i < buf.length; i++) sum += buf[i];
        const avg = sum / buf.length / 255;
        setAudioLevel(avg);
        levelRafRef.current = requestAnimationFrame(updateLevel);
      };
      levelRafRef.current = requestAnimationFrame(updateLevel);
    } catch (e: unknown) {
      const err = e instanceof Error ? e.message : "mic error";
      const message = `Microfono: ${err}`;
      setLastError(message);
      if (onError) onError(message);
      throw new Error(message);
    }
  }, [micSensitivity, onError]);

  const stopMicCapture = useCallback(() => {
    if (levelRafRef.current) {
      cancelAnimationFrame(levelRafRef.current);
      levelRafRef.current = null;
    }
    workletNodeRef.current?.disconnect();
    workletNodeRef.current = null;
    analyserRef.current?.disconnect();
    analyserRef.current = null;
    sourceNodeRef.current?.disconnect();
    sourceNodeRef.current = null;
    silentGainRef.current?.disconnect();
    silentGainRef.current = null;
    mediaStreamRef.current?.getTracks().forEach((t) => t.stop());
    mediaStreamRef.current = null;
    if (audioCtxRef.current) {
      void audioCtxRef.current.close();
      audioCtxRef.current = null;
    }
    setAudioLevel(0);
  }, []);

  // === Connection management ===

  const connect = useCallback(async () => {
    if (wsRef.current && wsRef.current.readyState === WebSocket.OPEN) return;

    setConnectionState("connecting");
    setLastError("");

    try {
      // Open WS first
      const ws = new WebSocket(resolvedWsUrl);
      ws.binaryType = "arraybuffer";
      wsRef.current = ws;

      await new Promise<void>((resolve, reject) => {
        ws.onopen = () => resolve();
        ws.onerror = (e) => reject(e);
        setTimeout(() => reject(new Error("WS connect timeout")), 5000);
      });

      ws.onmessage = (e: MessageEvent) => {
        if (typeof e.data === "string") {
          try {
            const msg = JSON.parse(e.data) as VoiceStreamMessage;
            handleMessage(msg);
          } catch (err) {
            console.warn("[voice] bad message:", err);
          }
        }
      };
      ws.onclose = () => {
        setConnectionState("disconnected");
        wsRef.current = null;
        stopMicCapture();
      };
      ws.onerror = () => {
        setConnectionState("error");
      };

      // Start mic only after WS is open
      await startMicCapture();

      setConnectionState("connected");

      // Send initial config
      if (language) {
        ws.send(JSON.stringify({ type: "set_language", language }));
      }
      ws.send(JSON.stringify({ type: "set_sensitivity", sensitivity: micSensitivity }));
    } catch (e: unknown) {
      const err = e instanceof Error ? e.message : "connect error";
      setConnectionState("error");
      setLastError(err);
      if (onError) onError(err);
      if (wsRef.current) {
        wsRef.current.close();
        wsRef.current = null;
      }
      stopMicCapture();
    }
  }, [resolvedWsUrl, handleMessage, language, micSensitivity, startMicCapture, stopMicCapture, onError]);

  const disconnect = useCallback(() => {
    stopPlayback();
    stopMicCapture();
    if (wsRef.current) {
      wsRef.current.close();
      wsRef.current = null;
    }
    setConnectionState("disconnected");
    setVoiceState("idle");
  }, [stopMicCapture, stopPlayback]);

  const toggleMute = useCallback(() => {
    const newMuted = !muted;
    setMuted(newMuted);
    sendCommand({ type: newMuted ? "mute" : "unmute" });
  }, [muted, sendCommand]);

  const interrupt = useCallback(() => {
    stopPlayback();
    sendCommand({ type: "interrupt" });
  }, [sendCommand, stopPlayback]);

  const setLang = useCallback((lang: string) => {
    sendCommand({ type: "set_language", language: lang });
  }, [sendCommand]);

  // Auto-connect on mount
  useEffect(() => {
    if (autoConnect) {
      void connect();
    }
    return () => {
      disconnect();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return {
    voiceState,
    connectionState,
    muted,
    transcripts,
    lastError,
    audioLevel,
    connect,
    disconnect,
    toggleMute,
    interrupt,
    setLanguage: setLang,
    isConnected: connectionState === "connected",
  };
}

// === Helpers ===

function base64ToBytes(b64: string): Uint8Array {
  const binStr = atob(b64);
  const bytes = new Uint8Array(binStr.length);
  for (let i = 0; i < binStr.length; i++) bytes[i] = binStr.charCodeAt(i);
  return bytes;
}
