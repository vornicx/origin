import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { apiBase } from "../config/api";

const API_BASE = apiBase();

type HudMode = "CORE" | "VISION" | "CAPTURE" | "ANALYSIS";

const MODES: HudMode[] = ["CORE", "VISION", "CAPTURE", "ANALYSIS"];

interface IronHudOverlayProps {
  stream: MediaStream | null;
  videoRef: React.RefObject<HTMLVideoElement>;
  onOpenChat: () => void;
  showLeft: boolean;
  showRight: boolean;
}

function downloadDataUrl(dataUrl: string, filename: string) {
  const link = document.createElement("a");
  link.href = dataUrl;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
}

function timestampName(prefix: string, extension: string) {
  const stamp = new Date().toISOString().replace(/[:.]/g, "-");
  return `${prefix}-${stamp}.${extension}`;
}

function formatHudTime(date: Date) {
  return date.toLocaleTimeString("es-ES", {
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  });
}

function formatHudDate(date: Date) {
  return date
    .toLocaleDateString("es-ES", {
      weekday: "long",
      day: "2-digit",
      month: "long",
    })
    .toUpperCase();
}

export function IronHudOverlay({
  stream,
  videoRef,
  onOpenChat,
  showLeft,
  showRight,
}: IronHudOverlayProps) {
  const [mode, setMode] = useState<HudMode>("VISION");
  const [isAnalyzing, setIsAnalyzing] = useState(false);
  const [analysis, setAnalysis] = useState("Awaiting visual request.");
  const [isRecording, setIsRecording] = useState(false);
  const [recordSeconds, setRecordSeconds] = useState(0);
  const [lastAction, setLastAction] = useState("System idle");
  const [previewFrame, setPreviewFrame] = useState<string | null>(null);
  const [now, setNow] = useState(() => new Date());
  const recorderRef = useRef<MediaRecorder | null>(null);
  const chunksRef = useRef<BlobPart[]>([]);
  const audioRef = useRef<HTMLAudioElement | null>(null);

  const coreMode = mode === "CORE" || !stream;
  const activeModeIndex = Math.max(0, MODES.indexOf(mode));

  const statusItems = useMemo(
    () => [
      { label: "CAM", value: stream ? "LIVE" : "STANDBY" },
      { label: "REC", value: isRecording ? `${recordSeconds}s` : "OFF" },
      { label: "L", value: showLeft ? "1" : "0" },
      { label: "R", value: showRight ? "1" : "0" },
    ],
    [isRecording, recordSeconds, showLeft, showRight, stream]
  );

  useEffect(() => {
    const id = window.setInterval(() => setNow(new Date()), 1000);
    return () => window.clearInterval(id);
  }, []);

  useEffect(() => {
    if (!isRecording) return;
    const id = window.setInterval(() => setRecordSeconds((s) => s + 1), 1000);
    return () => window.clearInterval(id);
  }, [isRecording]);

  useEffect(() => {
    if (stream && mode === "CORE") setMode("VISION");
  }, [mode, stream]);

  useEffect(() => {
    return () => {
      if (recorderRef.current?.state === "recording") {
        recorderRef.current.stop();
      }
    };
  }, []);

  const captureFrame = useCallback(() => {
    const video = videoRef.current;
    if (!video || !video.videoWidth || !video.videoHeight) return null;

    const canvas = document.createElement("canvas");
    canvas.width = video.videoWidth;
    canvas.height = video.videoHeight;
    const ctx = canvas.getContext("2d");
    if (!ctx) return null;

    ctx.translate(canvas.width, 0);
    ctx.scale(-1, 1);
    ctx.drawImage(video, 0, 0, canvas.width, canvas.height);
    return canvas.toDataURL("image/jpeg", 0.88);
  }, [videoRef]);

  const cycleMode = useCallback(() => {
    setMode((current) => {
      const next = MODES[(MODES.indexOf(current) + 1) % MODES.length];
      setLastAction(`Mode ${next.toLowerCase()}`);
      return next;
    });
  }, []);

  const speakBootLine = useCallback(async () => {
    setLastAction("Origin voice online");
    onOpenChat();
    try {
      const res = await fetch(`${API_BASE}/voice/speak`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          text: "Origin online. Visual interface ready.",
          preset: "origin",
        }),
      });
      const data = await res.json();
      if (data.audio_b64) {
        const audioBytes = Uint8Array.from(atob(data.audio_b64), (c) => c.charCodeAt(0));
        const blob = new Blob([audioBytes], { type: "audio/mpeg" });
        const url = URL.createObjectURL(blob);
        if (audioRef.current) {
          audioRef.current.src = url;
          await audioRef.current.play();
        }
      }
    } catch {
      setLastAction("Voice service offline");
    }
  }, [onOpenChat]);

  const saveSnapshot = useCallback(() => {
    const dataUrl = captureFrame();
    if (!dataUrl) {
      setLastAction("No camera frame");
      setMode("CORE");
      return;
    }
    setPreviewFrame(dataUrl);
    setMode("CAPTURE");
    downloadDataUrl(dataUrl, timestampName("origin-capture", "jpg"));
    setLastAction("Capture transmitted");
  }, [captureFrame]);

  const analyzeFrame = useCallback(async () => {
    const dataUrl = captureFrame();
    if (!dataUrl) {
      setAnalysis("Camera frame unavailable.");
      setLastAction("Vision unavailable");
      setMode("CORE");
      return;
    }
    setPreviewFrame(dataUrl);
    setMode("ANALYSIS");
    setIsAnalyzing(true);
    setLastAction("Analysis request sent");
    try {
      const frame_b64 = dataUrl.split(",")[1];
      const res = await fetch(`${API_BASE}/camera/analyze`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          frame_b64,
          question: "Describe el escritorio del usuario e identifica objetos relevantes de forma breve.",
        }),
      });
      const data = await res.json();
      const result = data.result?.analysis || data.error || "No visual response.";
      setAnalysis(result);
      setLastAction("Objects classified");
    } catch {
      setAnalysis("Vision service offline.");
      setLastAction("Vision service offline");
    } finally {
      setIsAnalyzing(false);
    }
  }, [captureFrame]);

  const toggleRecording = useCallback(() => {
    if (recorderRef.current?.state === "recording") {
      recorderRef.current.stop();
      setLastAction("Recording saved");
      return;
    }

    if (!stream) {
      setMode("CORE");
      setLastAction("No camera feed");
      return;
    }

    try {
      chunksRef.current = [];
      const recorder = new MediaRecorder(stream, { mimeType: "video/webm" });
      recorder.ondataavailable = (event) => {
        if (event.data.size > 0) chunksRef.current.push(event.data);
      };
      recorder.onstop = () => {
        const blob = new Blob(chunksRef.current, { type: "video/webm" });
        const url = URL.createObjectURL(blob);
        downloadDataUrl(url, timestampName("origin-recording", "webm"));
        window.setTimeout(() => URL.revokeObjectURL(url), 1000);
        setIsRecording(false);
        setRecordSeconds(0);
      };
      recorderRef.current = recorder;
      recorder.start();
      setIsRecording(true);
      setRecordSeconds(0);
      setMode("CAPTURE");
      setLastAction("Recording live");
    } catch {
      setLastAction("Recorder unavailable");
    }
  }, [stream]);

  return (
    <div className={`origin-hud ${coreMode ? "origin-hud--core" : "origin-hud--vision"} origin-hud--${mode.toLowerCase()}`}>
      <audio ref={audioRef} className="origin-hud__audio" />
      <div className="origin-hud__blueprint" />
      <div className="origin-hud__scan" />
      <div className="origin-hud__top-rule">
        <span />
      </div>

      <main className="origin-hud__core">
        <button className="origin-hud__orb" onClick={speakBootLine} title="Abrir Origin">
          <span className="origin-hud__orb-ring" />
          <span className="origin-hud__orb-ring origin-hud__orb-ring--b" />
          <strong>Origin</strong>
        </button>
        <div className="origin-hud__clock">{formatHudTime(now)}</div>
        <div className="origin-hud__date">{formatHudDate(now)}</div>
      </main>

      {!coreMode && (
        <section className="origin-hud__cam-card">
          <div className="origin-hud__cam-tab">CAM_FEED</div>
          <div className="origin-hud__cam-preview">
            {previewFrame ? <img src={previewFrame} alt="" /> : <span>Live feed synced</span>}
          </div>
          <div className="origin-hud__cam-meta">
            <span>{isAnalyzing ? "ANALYSING" : lastAction}</span>
            <strong>{isRecording ? `REC ${recordSeconds}s` : "LAST SHOT 00:00:05"}</strong>
          </div>
          <p className="origin-hud__analysis">
            {isAnalyzing ? "Request: identify desk objects and estimate their value..." : analysis}
          </p>
          <button className="origin-hud__reset" onClick={() => setPreviewFrame(null)}>
            reset camera
          </button>
        </section>
      )}

      {!coreMode && (
        <div className="origin-hud__desk-markers" aria-hidden="true">
          <span className="origin-hud__marker origin-hud__marker--keyboard" />
          <span className="origin-hud__marker origin-hud__marker--object" />
          <span className="origin-hud__marker origin-hud__marker--cup" />
        </div>
      )}

      <nav className="origin-hud__dock" aria-label="Funciones Origin">
        <button className={mode === "CORE" ? "is-active" : ""} onClick={() => setMode("CORE")} title="Core" />
        <button className={mode === "VISION" ? "is-active" : ""} onClick={cycleMode} title="Modo" />
        <button onClick={speakBootLine} title="Chat" />
        <button onClick={saveSnapshot} title="Captura" />
        <button className={isRecording ? "is-active" : ""} onClick={toggleRecording} title="Grabar" />
        <button className={mode === "ANALYSIS" ? "is-active" : ""} onClick={analyzeFrame} title="Analizar" disabled={isAnalyzing} />
      </nav>

      <footer className="origin-hud__footer">
        <div className="origin-hud__pager">
          {MODES.map((item, index) => (
            <span key={item} className={index === activeModeIndex ? "is-active" : ""} />
          ))}
        </div>
        <div className="origin-hud__status">
          {statusItems.map((item) => (
            <span key={item.label}>
              {item.label}:{item.value}
            </span>
          ))}
        </div>
      </footer>
    </div>
  );
}
