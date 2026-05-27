/**
 * VoiceConversation â€” Origin Holographic Sphere Interface
 *
 * Esfera hologrÃ¡fica central inspirada en Iron Man Origin + Mark-XXXIX.
 * Auto-conecta al montarse. Sin botones â€” simplemente habla con Origin.
 *
 * VisualizaciÃ³n por capas (de fondo a frente):
 *   1. Fondo profundo con rejilla hexagonal sutil
 *   2. LÃ­neas de escaneo CRT (efecto hologrÃ¡fico)
 *   3. Resplandor radial desde el centro
 *   4. Anillos orbitales tridimensionales con dashes animados
 *   5. PartÃ­culas orbitando con halo luminoso
 *   6. Forma de onda de audio reactiva
 *   7. Esfera principal con gradiente y brillo especular
 *   8. Texto hologrÃ¡fico central
 *   9. Decoradores HUD en las esquinas (estilo Iron Man)
 */

import { useEffect, useRef } from "react";
import { useVoiceStream, type VoiceState, type VoiceTranscript } from "../hooks/useVoiceStream";
import { isTauri } from "../config/api";

/* â”€â”€â”€ Paleta por estado â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€ */
interface Palette {
  bg: string;       // fondo profundo
  glow: string;     // color de resplandor principal
  ring: string;     // anillos orbitales
  particle: string; // partÃ­culas
  accent: string;   // detalles
}

const PALETTE: Record<VoiceState, Palette> = {
  idle:      { bg: "#020e1a", glow: "#00b4d8", ring: "#0096c7", particle: "#48cae4", accent: "#90e0ef" },
  listening: { bg: "#02101e", glow: "#00d4ff", ring: "#0099ee", particle: "#7df9ff", accent: "#bef0ff" },
  thinking:  { bg: "#1a1100", glow: "#f59e0b", ring: "#d97706", particle: "#fbbf24", accent: "#fde68a" },
  speaking:  { bg: "#001a10", glow: "#10b981", ring: "#059669", particle: "#34d399", accent: "#a7f3d0" },
  muted:     { bg: "#1a0000", glow: "#ef4444", ring: "#b91c1c", particle: "#fca5a5", accent: "#fecaca" },
  error:     { bg: "#1a0000", glow: "#ef4444", ring: "#991b1b", particle: "#fca5a5", accent: "#fecaca" },
};

const STATE_LABEL: Record<VoiceState, string> = {
  idle:      "EN ESPERA",
  listening: "ESCUCHANDO",
  thinking:  "PROCESANDO",
  speaking:  "RESPONDIENDO",
  muted:     "SILENCIADO",
  error:     "ERROR",
};

/* â”€â”€â”€ Anillos orbitales â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€ */
interface Ring {
  rx: number;     // radio horizontal
  ry: number;     // radio vertical (tilt)
  speed: number;  // velocidad de rotaciÃ³n de partÃ­culas
  slowRot: number; // rotaciÃ³n lenta del plano orbital
  width: number;
  dash: [number, number];
}

const RINGS_FULL: Ring[] = [
  { rx: 138, ry: 38, speed: 0.5,  slowRot: 0.04,  width: 1.3, dash: [9, 5] },
  { rx: 156, ry: 60, speed: -0.3, slowRot: -0.025, width: 0.9, dash: [13, 7] },
  { rx: 118, ry: 22, speed: 0.75, slowRot: 0.06,  width: 1.5, dash: [5, 3] },
];

const RINGS_COMPACT: Ring[] = [
  { rx: 95,  ry: 26, speed: 0.5,  slowRot: 0.04,  width: 1.1, dash: [7, 4] },
  { rx: 108, ry: 42, speed: -0.3, slowRot: -0.025, width: 0.8, dash: [10, 5] },
  { rx: 82,  ry: 15, speed: 0.75, slowRot: 0.06,  width: 1.3, dash: [4, 3] },
];

const RINGS_PUCK: Ring[] = [
  { rx: 58, ry: 16, speed: 0.5,  slowRot: 0.04,  width: 1.0, dash: [6, 4] },
  { rx: 66, ry: 25, speed: -0.3, slowRot: -0.025, width: 0.8, dash: [8, 5] },
  { rx: 48, ry: 10, speed: 0.75, slowRot: 0.06,  width: 1.1, dash: [4, 3] },
];

const RINGS_HUD_FULL: Ring[] = [
  { rx: 175, ry: 48, speed: 0.5,  slowRot: 0.04,  width: 1.5, dash: [11, 6] },
  { rx: 198, ry: 76, speed: -0.3, slowRot: -0.025, width: 1.1, dash: [16, 8] },
  { rx: 150, ry: 28, speed: 0.75, slowRot: 0.06,  width: 1.7, dash: [6, 4] },
];

/* â”€â”€â”€ PartÃ­culas por anillo â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€ */
interface Particle {
  ring: number;
  phase: number;
  size: number;
}

const PARTICLES: Particle[] = [
  { ring: 0, phase: 0,            size: 4.0 },
  { ring: 0, phase: Math.PI,      size: 2.8 },
  { ring: 1, phase: Math.PI / 2,  size: 3.8 },
  { ring: 1, phase: Math.PI * 1.5, size: 2.4 },
  { ring: 2, phase: Math.PI / 3,  size: 3.2 },
  { ring: 2, phase: Math.PI * 1.3, size: 1.9 },
];

/* â”€â”€â”€ Helpers â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€ */
function hexToRgba(hex: string, alpha: number): string {
  const r = parseInt(hex.slice(1, 3), 16);
  const g = parseInt(hex.slice(3, 5), 16);
  const b = parseInt(hex.slice(5, 7), 16);
  const a = Math.max(0, Math.min(1, alpha));
  return `rgba(${r},${g},${b},${a})`;
}

function buildHexGrid(W: number, H: number, color: string): HTMLCanvasElement {
  const offscreen = document.createElement("canvas");
  offscreen.width = W;
  offscreen.height = H;
  const c = offscreen.getContext("2d")!;
  const size = 22;
  const w = size * 2;
  const h = Math.sqrt(3) * size;
  c.strokeStyle = hexToRgba(color, 0.06);
  c.lineWidth = 0.5;
  for (let row = -1; row < H / h + 2; row++) {
    for (let col = -1; col < W / w + 2; col++) {
      const ox = row % 2 === 0 ? 0 : w * 0.5;
      const ccx = col * w + ox;
      const ccy = row * h;
      c.beginPath();
      for (let i = 0; i < 6; i++) {
        const a = (Math.PI / 3) * i - Math.PI / 6;
        const x = ccx + size * Math.cos(a);
        const y = ccy + size * Math.sin(a);
        if (i === 0) c.moveTo(x, y);
        else c.lineTo(x, y);
      }
      c.closePath();
      c.stroke();
    }
  }
  return offscreen;
}

function drawHUDCorners(
  ctx: CanvasRenderingContext2D,
  size: number,
  color: string,
  alpha: number,
  t: number
) {
  const len = 22;
  const off = 12;
  ctx.lineWidth = 1.2;
  ctx.strokeStyle = hexToRgba(color, alpha);
  ctx.shadowColor = color;
  ctx.shadowBlur = 6;

  const corners: Array<[number, number, number, number]> = [
    [off, off, 1, 1],
    [size - off, off, -1, 1],
    [off, size - off, 1, -1],
    [size - off, size - off, -1, -1],
  ];

  for (const [x, y, dx, dy] of corners) {
    ctx.beginPath();
    ctx.moveTo(x + dx * len, y);
    ctx.lineTo(x, y);
    ctx.lineTo(x, y + dy * len);
    ctx.stroke();
  }
  ctx.shadowBlur = 0;

  // Tick marks (rotating slowly)
  const tickR = size / 2 - 30;
  const cx = size / 2;
  const cy = size / 2;
  ctx.strokeStyle = hexToRgba(color, alpha * 0.7);
  ctx.lineWidth = 1;
  const tickCount = 36;
  for (let i = 0; i < tickCount; i++) {
    const a = (i / tickCount) * Math.PI * 2 + t * 0.04;
    const isMajor = i % 9 === 0;
    const r1 = tickR;
    const r2 = tickR + (isMajor ? 8 : 4);
    ctx.beginPath();
    ctx.moveTo(cx + Math.cos(a) * r1, cy + Math.sin(a) * r1);
    ctx.lineTo(cx + Math.cos(a) * r2, cy + Math.sin(a) * r2);
    ctx.stroke();
  }
}

/* â”€â”€â”€ Voice control commands â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€ */
// Normalize: lowercase, strip accents, strip punctuation
function normalizeText(text: string): string {
  return text
    .toLowerCase()
    .normalize("NFD")
    .replace(/[̀-ͯ]/g, "")
    .replace(/[^a-z\s]/g, "")
    .trim();
}

type VoiceCommand = "mute" | "unmute" | "hide" | "show";

const MUTE_WORDS   = ["silenciate", "silenciar", "silencio", "mute", "mutate", "calla", "callate"];
const UNMUTE_WORDS = ["desilenciate", "desilenciar", "habla", "activa", "unmute", "deja de silenciar"];
const HIDE_WORDS   = ["hide", "ocultate", "escondete", "ocultar", "esconder", "alejate", "desaparece", "hazte invisible", "ocultarse"];
const SHOW_WORDS   = ["aparece", "aparecer", "mostrate", "muestrate", "ven", "vuelve", "regresa", "hazte visible", "aparecete"];

function detectVoiceCommand(text: string): VoiceCommand | null {
  const n = normalizeText(text);
  if (n.length > 60) return null; // Too long to be a pure command
  const has = (words: string[]) => words.some(w => n.includes(normalizeText(w)));
  if (has(MUTE_WORDS))   return "mute";
  if (has(UNMUTE_WORDS)) return "unmute";
  if (has(HIDE_WORDS))   return "hide";
  if (has(SHOW_WORDS))   return "show";
  return null;
}

interface Props {
  onClose?: () => void;
  compact?: boolean;
  hudPuck?: boolean;
  hudFull?: boolean;
  manageClickThrough?: boolean;
  onVoiceStateChange?: (state: VoiceState) => void;
  onTranscriptUpdate?: (transcripts: VoiceTranscript[]) => void;
}

export function VoiceConversation({
  onClose,
  compact = false,
  hudPuck = false,
  hudFull = false,
  manageClickThrough = true,
  onVoiceStateChange,
  onTranscriptUpdate,
}: Props) {
  const SIZE = hudPuck ? 150 : hudFull ? 540 : compact ? 230 : 460;
  const SPHERE_R = hudPuck ? 31 : hudFull ? 115 : compact ? 48 : 90;
  const CX = SIZE / 2;
  const CY = SIZE / 2;
  const RINGS = hudPuck ? RINGS_PUCK : hudFull ? RINGS_HUD_FULL : compact ? RINGS_COMPACT : RINGS_FULL;

  const {
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
    isConnected,
  } = useVoiceStream({ language: "es", autoConnect: false, micSensitivity: "low" });

  const canvasRef = useRef<HTMLCanvasElement>(null);
  const animRef = useRef<number | null>(null);
  const hexGridRef = useRef<HTMLCanvasElement | null>(null);
  const stateRef = useRef<VoiceState>(voiceState);
  const levelRef = useRef(0);
  const tRef = useRef(0);
  const transcriptRef = useRef<HTMLDivElement>(null);
  const cmdCheckCountRef = useRef(0);
  const connectRetryRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => { stateRef.current = voiceState; }, [voiceState]);
  useEffect(() => { levelRef.current = audioLevel; }, [audioLevel]);
  useEffect(() => { onVoiceStateChange?.(voiceState); }, [onVoiceStateChange, voiceState]);
  useEffect(() => { onTranscriptUpdate?.(transcripts); }, [onTranscriptUpdate, transcripts]);

  // Auto-scroll transcript
  useEffect(() => {
    if (transcriptRef.current)
      transcriptRef.current.scrollTop = transcriptRef.current.scrollHeight;
  }, [transcripts.length]);

  // Click-through: transparent to mouse clicks when idle/muted so user can work underneath
  useEffect(() => {
    if (!manageClickThrough) return;
    if (!isTauri()) return;
    const clickThrough = voiceState === "idle" || voiceState === "muted";
    void import("@tauri-apps/api/core").then(({ invoke }) => {
      void invoke("set_click_through", { enabled: clickThrough });
    });
  }, [manageClickThrough, voiceState]);

  // Voice control commands: silenciate / desilenciate / hide / aparece
  useEffect(() => {
    if (!isTauri()) return;
    if (transcripts.length <= cmdCheckCountRef.current) return;
    cmdCheckCountRef.current = transcripts.length;

    const last = transcripts[transcripts.length - 1];
    if (last.role !== "user") return;

    const cmd = detectVoiceCommand(last.text);
    if (!cmd) return;

    if (cmd === "mute"   && !muted) { toggleMute(); return; }
    if (cmd === "unmute" &&  muted) { toggleMute(); return; }
    if (cmd === "hide" || cmd === "show") {
      void import("@tauri-apps/api/core").then(({ invoke }) => {
        void invoke(cmd === "hide" ? "hide_window" : "show_window");
      });
    }
  }, [transcripts, muted, toggleMute]);

  // Atajos de teclado
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (!isConnected) return;
      const target = e.target as HTMLElement;
      if (target?.tagName === "INPUT" || target?.tagName === "TEXTAREA") return;
      if (e.key === "m" || e.key === "M") {
        e.preventDefault();
        toggleMute();
      } else if (e.key === "Escape") {
        e.preventDefault();
        interrupt();
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [isConnected, toggleMute, interrupt]);

  const retryCountRef = useRef(0);

  useEffect(() => {
    let cancelled = false;

    const tryConnect = async () => {
      if (cancelled) return;
      try {
        const host = window.location.hostname || "localhost";
        const r = await fetch(`http://${host}:9001/health`, { signal: AbortSignal.timeout(800) });
        if (!r.ok) throw new Error("not ok");
      } catch {
        const delay = Math.min(2000 * Math.pow(1.5, retryCountRef.current), 15000);
        retryCountRef.current++;
        connectRetryRef.current = setTimeout(tryConnect, delay);
        return;
      }
      if (cancelled) return;
      retryCountRef.current = 0;
      void connect();
    };

    tryConnect();
    return () => {
      cancelled = true;
      if (connectRetryRef.current) {
        clearTimeout(connectRetryRef.current);
        connectRetryRef.current = null;
      }
      disconnect();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (connectionState !== "error" && connectionState !== "disconnected") return;
    if (lastError.toLowerCase().includes("microfono") || lastError.toLowerCase().includes("mic")) return;
    if (connectRetryRef.current) return;
    const delay = Math.min(2000 * Math.pow(1.5, retryCountRef.current), 15000);
    retryCountRef.current++;
    connectRetryRef.current = setTimeout(async () => {
      connectRetryRef.current = null;
      try {
        const host = window.location.hostname || "localhost";
        const r = await fetch(`http://${host}:9001/health`, { signal: AbortSignal.timeout(800) });
        if (!r.ok) return;
      } catch { return; }
      retryCountRef.current = 0;
      void connect();
    }, delay);
    return () => {
      if (connectRetryRef.current) {
        clearTimeout(connectRetryRef.current);
        connectRetryRef.current = null;
      }
    };
  }, [connect, connectionState, lastError]);

  // Canvas animation
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    const dpr = window.devicePixelRatio || 1;
    canvas.width = SIZE * dpr;
    canvas.height = SIZE * dpr;
    canvas.style.width = `${SIZE}px`;
    canvas.style.height = `${SIZE}px`;
    ctx.scale(dpr, dpr);

    // Rejilla hexagonal pre-renderizada
    hexGridRef.current = buildHexGrid(SIZE, SIZE, "#00b4d8");

    const draw = () => {
      tRef.current += 0.016;
      const t = tRef.current;
      const state = stateRef.current;
      const level = levelRef.current;
      const pal = PALETTE[state];

      const speedMult =
        state === "speaking"  ? 2.8 :
        state === "thinking"  ? 2.0 :
        state === "listening" ? 1.3 :
        0.55;

      const intensity =
        state === "speaking"  ? 0.85 + level * 0.15 :
        state === "listening" ? 0.65 + level * 0.35 :
        state === "thinking"  ? 0.75 + 0.1 * Math.sin(t * 2) :
        0.4;

      ctx.clearRect(0, 0, SIZE, SIZE);

      // 1. Fondo profundo
      ctx.fillStyle = pal.bg;
      ctx.fillRect(0, 0, SIZE, SIZE);

      // 2. Rejilla hexagonal
      if (hexGridRef.current) {
        ctx.globalAlpha = 0.55;
        ctx.drawImage(hexGridRef.current, 0, 0);
        ctx.globalAlpha = 1;
      }

      // 3. Scanlines CRT
      ctx.fillStyle = "rgba(0,0,0,0.18)";
      for (let y = 0; y < SIZE; y += 3) {
        ctx.fillRect(0, y, SIZE, 1);
      }

      // 4. Resplandor radial profundo
      const spaceGlow = ctx.createRadialGradient(CX, CY, 0, CX, CY, SIZE * 0.75);
      spaceGlow.addColorStop(0, hexToRgba(pal.glow, 0.18 * intensity));
      spaceGlow.addColorStop(0.4, hexToRgba(pal.glow, 0.06 * intensity));
      spaceGlow.addColorStop(1, "transparent");
      ctx.fillStyle = spaceGlow;
      ctx.fillRect(0, 0, SIZE, SIZE);

      // 5. Anillos orbitales
      for (let ri = 0; ri < RINGS.length; ri++) {
        const ring = RINGS[ri];
        const ringRot = t * ring.slowRot;

        ctx.save();
        ctx.translate(CX, CY);
        ctx.rotate(ringRot);

        // Glow exterior del anillo (capas para efecto blur)
        for (let g = 3; g >= 0; g--) {
          ctx.lineWidth = ring.width + g * 2.2;
          ctx.strokeStyle = hexToRgba(pal.ring, (0.04 - g * 0.008) * intensity);
          ctx.beginPath();
          ctx.ellipse(0, 0, ring.rx, ring.ry, 0, 0, Math.PI * 2);
          ctx.stroke();
        }

        // Anillo principal con dashes animados
        ctx.setLineDash([ring.dash[0], ring.dash[1]]);
        ctx.lineDashOffset = -(t * ring.speed * speedMult * 80);
        ctx.lineWidth = ring.width;
        ctx.strokeStyle = hexToRgba(pal.ring, 0.75 * intensity);
        ctx.shadowColor = pal.glow;
        ctx.shadowBlur = 4;
        ctx.beginPath();
        ctx.ellipse(0, 0, ring.rx, ring.ry, 0, 0, Math.PI * 2);
        ctx.stroke();
        ctx.shadowBlur = 0;
        ctx.setLineDash([]);

        ctx.restore();
      }

      // 6. PartÃ­culas orbitando
      for (const p of PARTICLES) {
        const ring = RINGS[p.ring];
        const orbitAngle = t * ring.speed * speedMult + p.phase;
        const slowAngle = t * ring.slowRot;

        // PosiciÃ³n local en la elipse
        const lx = Math.cos(orbitAngle) * ring.rx;
        const ly = Math.sin(orbitAngle) * ring.ry;

        // RotaciÃ³n del plano orbital
        const px = CX + lx * Math.cos(slowAngle) - ly * Math.sin(slowAngle);
        const py = CY + lx * Math.sin(slowAngle) + ly * Math.cos(slowAngle);

        // Halo de la partÃ­cula
        const phaloGrad = ctx.createRadialGradient(px, py, 0, px, py, p.size * 4.5);
        phaloGrad.addColorStop(0, hexToRgba(pal.particle, 0.95 * intensity));
        phaloGrad.addColorStop(0.5, hexToRgba(pal.particle, 0.3 * intensity));
        phaloGrad.addColorStop(1, "transparent");
        ctx.fillStyle = phaloGrad;
        ctx.beginPath();
        ctx.arc(px, py, p.size * 4.5, 0, Math.PI * 2);
        ctx.fill();

        // NÃºcleo blanco brillante
        ctx.fillStyle = "rgba(255,255,255,0.95)";
        ctx.beginPath();
        ctx.arc(px, py, p.size * 0.5, 0, Math.PI * 2);
        ctx.fill();
      }

      // 7. Forma de onda de audio (cuando hay actividad)
      if (state === "listening" || state === "speaking") {
        const waveBaseR = SPHERE_R + (compact ? 14 : 20);
        const N = 90;
        ctx.beginPath();
        for (let i = 0; i <= N; i++) {
          const angle = (i / N) * Math.PI * 2;
          const wave =
            level * 22 * Math.sin(angle * 11 + t * 5) +
            level * 11 * Math.sin(angle * 7 - t * 3.5) +
            (state === "speaking" ? level * 6 * Math.sin(angle * 3 + t * 8) : 0);
          const r = waveBaseR + wave + 3 * Math.sin(angle * 4 + t * 2);
          const x = CX + Math.cos(angle) * r;
          const y = CY + Math.sin(angle) * r;
          if (i === 0) ctx.moveTo(x, y);
          else ctx.lineTo(x, y);
        }
        ctx.strokeStyle = hexToRgba(pal.glow, 0.6 * intensity);
        ctx.lineWidth = 1.4;
        ctx.shadowColor = pal.glow;
        ctx.shadowBlur = 6;
        ctx.stroke();
        ctx.shadowBlur = 0;
      }

      // 8. Esfera principal
      const pulseFactor =
        state === "speaking"  ? 1.0 + 0.07 * Math.sin(t * 9) + level * 0.09 :
        state === "listening" ? 1.0 + level * 0.13 :
        state === "thinking"  ? 1.0 + 0.04 * Math.sin(t * 4) :
        1.0 + 0.02 * Math.sin(t * 1.5);
      const sR = SPHERE_R * pulseFactor;

      // Halo exterior de la esfera
      const halo = ctx.createRadialGradient(CX, CY, sR * 0.7, CX, CY, sR * 2.8);
      halo.addColorStop(0, hexToRgba(pal.glow, 0.4 * intensity));
      halo.addColorStop(0.4, hexToRgba(pal.glow, 0.12 * intensity));
      halo.addColorStop(1, "transparent");
      ctx.fillStyle = halo;
      ctx.beginPath();
      ctx.arc(CX, CY, sR * 2.8, 0, Math.PI * 2);
      ctx.fill();

      // Cuerpo de la esfera con gradiente esfÃ©rico
      const body = ctx.createRadialGradient(
        CX - sR * 0.35, CY - sR * 0.35, 0,
        CX, CY, sR
      );
      body.addColorStop(0, hexToRgba(pal.glow, 0.75));
      body.addColorStop(0.35, hexToRgba(pal.ring, 0.6));
      body.addColorStop(0.78, hexToRgba(pal.bg, 0.9));
      body.addColorStop(1, "rgba(0,0,15,0.95)");
      ctx.fillStyle = body;
      ctx.beginPath();
      ctx.arc(CX, CY, sR, 0, Math.PI * 2);
      ctx.fill();

      // Reflejo especular (esquina superior izquierda)
      const spec = ctx.createRadialGradient(
        CX - sR * 0.4, CY - sR * 0.45, 0,
        CX - sR * 0.4, CY - sR * 0.45, sR * 0.7
      );
      spec.addColorStop(0, "rgba(255,255,255,0.32)");
      spec.addColorStop(0.5, "rgba(255,255,255,0.08)");
      spec.addColorStop(1, "transparent");
      ctx.fillStyle = spec;
      ctx.beginPath();
      ctx.arc(CX, CY, sR, 0, Math.PI * 2);
      ctx.fill();

      // Borde de la esfera con glow
      ctx.shadowColor = pal.glow;
      ctx.shadowBlur = 10;
      ctx.lineWidth = 1.5;
      ctx.strokeStyle = hexToRgba(pal.glow, 0.9);
      ctx.beginPath();
      ctx.arc(CX, CY, sR, 0, Math.PI * 2);
      ctx.stroke();
      ctx.shadowBlur = 0;

      // Detalle interno: anillo elÃ­ptico interno rotando
      ctx.save();
      ctx.translate(CX, CY);
      ctx.rotate(t * 0.8 * speedMult);
      ctx.strokeStyle = hexToRgba(pal.accent, 0.4 * intensity);
      ctx.lineWidth = 0.7;
      ctx.beginPath();
      ctx.ellipse(0, 0, sR * 0.62, sR * 0.22, 0, 0, Math.PI * 2);
      ctx.stroke();
      // Segundo anillo interno perpendicular
      ctx.rotate(Math.PI / 2);
      ctx.beginPath();
      ctx.ellipse(0, 0, sR * 0.55, sR * 0.18, 0, 0, Math.PI * 2);
      ctx.stroke();
      ctx.restore();

      // NÃºcleo brillante central
      const coreGlow = ctx.createRadialGradient(CX, CY, 0, CX, CY, sR * 0.25);
      coreGlow.addColorStop(0, hexToRgba(pal.accent, 0.7 * intensity));
      coreGlow.addColorStop(1, "transparent");
      ctx.fillStyle = coreGlow;
      ctx.beginPath();
      ctx.arc(CX, CY, sR * 0.25, 0, Math.PI * 2);
      ctx.fill();

      // 9. Texto hologrÃ¡fico central
      ctx.textAlign = "center";
      ctx.textBaseline = "middle";

      const titleSize = compact ? 14 : 17;
      const labelSize = compact ? 8 : 10;

      ctx.font = `bold ${titleSize}px 'JetBrains Mono', monospace`;
      ctx.fillStyle = "rgba(255, 255, 255, 0.96)";
      ctx.shadowColor = pal.glow;
      ctx.shadowBlur = 12;
      ctx.fillText("Origin", CX, CY - (compact ? 8 : 11));
      ctx.shadowBlur = 0;

      ctx.font = `${labelSize}px 'JetBrains Mono', monospace`;
      ctx.fillStyle = hexToRgba(pal.accent, 0.95);
      ctx.fillText(STATE_LABEL[state], CX, CY + (compact ? 8 : 11));

      // 10. Decoradores HUD en esquinas
      drawHUDCorners(ctx, SIZE, pal.glow, intensity * 0.5, t);

      animRef.current = requestAnimationFrame(draw);
    };

    animRef.current = requestAnimationFrame(draw);
    return () => {
      if (animRef.current) cancelAnimationFrame(animRef.current);
    };
  }, [SIZE, SPHERE_R, CX, CY, compact, RINGS]);

  return (
    <div className={`jv-conv ${compact ? "jv-conv--compact" : ""} ${hudPuck ? "jv-conv--hud-puck" : ""} ${hudFull ? "jv-conv--hud-full" : ""}`}>
      {/* Header */}
      {!hudPuck && !hudFull && <div className="jv-header">
        <div className="jv-header-left">
          <span className={`jv-dot jv-dot--${connectionState}`} />
          <span className="jv-title">Origin VOICE</span>
          <span className="jv-state-tag">{STATE_LABEL[voiceState]}</span>
        </div>
        <div className="jv-header-right">
          {isConnected && (
            <>
              <button
                className={`jv-btn-sm ${muted ? "jv-btn-sm--danger" : ""}`}
                onClick={toggleMute}
                title="Silenciar (M)"
              >
                {muted ? "MUTE" : "MIC"}
              </button>
              <button
                className="jv-btn-sm"
                onClick={interrupt}
                title="Interrumpir (ESC)"
                disabled={voiceState === "idle" || voiceState === "listening"}
              >
                STOP
              </button>
            </>
          )}
          {onClose && (
            <button className="jv-btn-close" onClick={onClose} title="Cerrar">x</button>
          )}
        </div>
      </div>}

      {/* Sphere canvas + overlays */}
      <div className="jv-sphere-wrap">
        <canvas ref={canvasRef} className="jv-canvas" />
        {connectionState === "connecting" && (
          <div className="jv-overlay">
            <div className="jv-overlay-text">INICIALIZANDO...</div>
          </div>
        )}
        {connectionState === "disconnected" && (
          <div className="jv-overlay">
            <button className="jv-connect-btn" onClick={connect}>
              CONECTAR
            </button>
          </div>
        )}
        {connectionState === "error" && (
          <div className="jv-overlay jv-overlay--error">
            <div className="jv-overlay-text">CONEXION PERDIDA</div>
            <button className="jv-connect-btn" onClick={connect}>
              REINTENTAR
            </button>
          </div>
        )}
      </div>

      {/* Indicador de error */}
      {lastError && (
        <div className="jv-error-bar">
          <span className="jv-error-icon">!</span> {lastError}
        </div>
      )}

      {/* Transcript */}
      {!hudPuck && !hudFull && <div className="jv-transcript" ref={transcriptRef}>
        {transcripts.length === 0 ? (
          <div className="jv-empty">
            <div className="jv-empty-icon">O</div>
            <div className="jv-empty-text">
              {isConnected ? "Habla con Origin..." : "Esperando conexion..."}
            </div>
            <div className="jv-empty-hint">
              <kbd>M</kbd> silenciar <span aria-hidden="true">/</span> <kbd>ESC</kbd> interrumpir
            </div>
          </div>
        ) : (
          transcripts.map((tr, i) => (
            <div key={i} className={`jv-tr jv-tr--${tr.role}`}>
              <div className="jv-tr-meta">
                <span className="jv-tr-who">{tr.role === "user" ? "TU" : "Origin"}</span>
                {tr.thinkMs !== undefined && tr.thinkMs > 0 && (
                  <span className="jv-tr-ms">{Math.round(tr.thinkMs)}ms</span>
                )}
              </div>
              <div className="jv-tr-text">{tr.text}</div>
            </div>
          ))
        )}
      </div>}
    </div>
  );
}
