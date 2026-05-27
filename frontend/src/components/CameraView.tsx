import { useState, useRef, useCallback, useEffect } from "react";
import { apiBase } from "../config/api";

const API_BASE = apiBase();

interface AnalysisResult {
  analysis: string;
  question: string;
  frame_size_kb?: number;
}

interface CameraViewProps {
  /** When true the panel mounts open (use in dedicated camera windows/overlays). */
  defaultOpen?: boolean;
  /** Optional question used when the HUD asks vision to analyze immediately. */
  autoAnalyzeQuestion?: string;
  /** Increment this value to trigger a fresh automatic analysis. */
  autoAnalyzeToken?: number;
  /** Called when the user presses the close button; if omitted, the panel just collapses to a toggle. */
  onClose?: () => void;
}

export function CameraView({ defaultOpen = false, autoAnalyzeQuestion, autoAnalyzeToken, onClose }: CameraViewProps = {}) {
  const [isOpen, setIsOpen] = useState(defaultOpen);
  const [hasCamera, setHasCamera] = useState<boolean | null>(null);
  const [isAnalyzing, setIsAnalyzing] = useState(false);
  const [result, setResult] = useState<AnalysisResult | null>(null);
  const [question, setQuestion] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [stream, setStream] = useState<MediaStream | null>(null);
  const streamRef = useRef<MediaStream | null>(null);

  const videoRef = useRef<HTMLVideoElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);

  // Keep ref in sync so stopCamera always has the current stream
  useEffect(() => { streamRef.current = stream; }, [stream]);

  // Start camera when panel opens
  useEffect(() => {
    if (isOpen) {
      startCamera();
    } else {
      stopCamera();
    }
    return () => stopCamera();
  }, [isOpen]);

  const startCamera = useCallback(async () => {
    setError(null);
    try {
      const mediaStream = await navigator.mediaDevices.getUserMedia({
        video: { width: { ideal: 1280 }, height: { ideal: 720 }, facingMode: "user" },
        audio: false,
      });
      setStream(mediaStream);
      setHasCamera(true);
      if (videoRef.current) {
        videoRef.current.srcObject = mediaStream;
      }
    } catch (err: any) {
      setHasCamera(false);
      setError(
        err.name === "NotAllowedError"
          ? "Acceso a la cámara denegado. Permite el acceso en el navegador."
          : `Error de cámara: ${err.message}`
      );
    }
  }, []);

  const stopCamera = useCallback(() => {
    const s = streamRef.current;
    if (s) {
      s.getTracks().forEach((t) => t.stop());
      streamRef.current = null;
      setStream(null);
    }
    if (videoRef.current) {
      videoRef.current.srcObject = null;
    }
  }, []);

  const captureAndAnalyze = useCallback(async (questionOverride?: string) => {
    const video = videoRef.current;
    const canvas = canvasRef.current;
    if (!video || !canvas || !stream) return;

    // Draw current video frame to canvas
    canvas.width = video.videoWidth || 640;
    canvas.height = video.videoHeight || 480;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    ctx.drawImage(video, 0, 0, canvas.width, canvas.height);

    // Get base64 JPEG
    const dataUrl = canvas.toDataURL("image/jpeg", 0.85);
    const frame_b64 = dataUrl.split(",")[1];

    setIsAnalyzing(true);
    setResult(null);
    setError(null);

    try {
      const res = await fetch(`${API_BASE}/camera/analyze`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          frame_b64,
          question: questionOverride?.trim() || question.trim() || "¿Qué ves? Describe en detalle lo que hay en la imagen.",
        }),
      });
      const data = await res.json();
      if (data.result?.analysis) {
        setResult(data.result);
      } else if (data.error) {
        setError(data.error);
      } else {
        setError("No se recibió análisis.");
      }
    } catch (err: any) {
      setError(`Error de conexión: ${err.message}`);
    } finally {
      setIsAnalyzing(false);
    }
  }, [stream, question]);

  useEffect(() => {
    if (!autoAnalyzeToken || !stream) return;
    const id = window.setTimeout(() => {
      void captureAndAnalyze(autoAnalyzeQuestion);
    }, 900);
    return () => window.clearTimeout(id);
  }, [autoAnalyzeToken, autoAnalyzeQuestion, stream, captureAndAnalyze]);

  if (!isOpen) {
    return (
      <button className="camera-toggle-btn" onClick={() => setIsOpen(true)} title="Activar cámara">
        CAM
      </button>
    );
  }

  return (
    <div className="camera-panel">
      <div className="camera-panel__header">
        <span className="camera-panel__title">CÁMARA</span>
        <button
          className="camera-panel__close"
          onClick={() => (onClose ? onClose() : setIsOpen(false))}
        >
          ✕
        </button>
      </div>

      <div className="camera-panel__feed">
        {hasCamera === false ? (
          <div className="camera-panel__no-camera">{error || "Cámara no disponible"}</div>
        ) : (
          <video
            ref={videoRef}
            autoPlay
            playsInline
            muted
            className="camera-panel__video"
          />
        )}
        {/* Hidden canvas for frame capture */}
        <canvas ref={canvasRef} style={{ display: "none" }} />
      </div>

      <div className="camera-panel__controls">
        <input
          className="camera-panel__question"
          type="text"
          placeholder="¿Qué quieres que Origin analice?"
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && void captureAndAnalyze()}
        />
        <button
          className="camera-panel__analyze-btn"
          onClick={() => void captureAndAnalyze()}
          disabled={isAnalyzing || !stream}
        >
          {isAnalyzing ? "Analizando..." : "Mostrar a Origin"}
        </button>
      </div>

      {error && <div className="camera-panel__error">{error}</div>}

      {result && (
        <div className="camera-panel__result">
          <div className="camera-panel__result-label">Origin ve:</div>
          <div className="camera-panel__result-text">{result.analysis}</div>
        </div>
      )}
    </div>
  );
}
