import { useRef, useState, useCallback, useEffect, lazy, Suspense } from "react";
import { Win11TitleBar } from "./components/Win11TitleBar";
import { Win11Sidebar, type NavSection } from "./components/Win11Sidebar";
import { HomePanel } from "./components/HomePanel";
import { VoiceConversation } from "./components/VoiceConversation";
import { useGlobalHotkeys } from "./hooks/useGlobalHotkeys";
import { isTauri } from "./config/api";
import type { VoiceState, VoiceTranscript } from "./hooks/useVoiceStream";

const ChatInterface = lazy(() => import("./components/ChatInterface").then(m => ({ default: m.ChatInterface })));
const Dashboard = lazy(() => import("./components/Dashboard").then(m => ({ default: m.Dashboard })));
const CameraView = lazy(() => import("./components/CameraView").then(m => ({ default: m.CameraView })));
const WebcamBackground = lazy(() => import("./components/WebcamBackground").then(m => ({ default: m.WebcamBackground })));
const SettingsPanel = lazy(() => import("./components/SettingsPanel").then(m => ({ default: m.SettingsPanel })));
const NewsPanel = lazy(() => import("./components/NewsPanel").then(m => ({ default: m.NewsPanel })));
const MarketPanel = lazy(() => import("./components/MarketPanel").then(m => ({ default: m.MarketPanel })));
const IntelPanel = lazy(() => import("./components/IntelPanel").then(m => ({ default: m.IntelPanel })));
const NewsTicker = lazy(() => import("./components/NewsTicker").then(m => ({ default: m.NewsTicker })));
const QuickQuery = lazy(() => import("./components/QuickQuery").then(m => ({ default: m.QuickQuery })));
const ClipboardAnalyzer = lazy(() => import("./components/ClipboardAnalyzer").then(m => ({ default: m.ClipboardAnalyzer })));
const ActivityLog = lazy(() => import("./components/ActivityLog").then(m => ({ default: m.ActivityLog })));

type WindowKind = "launcher" | "chat" | "systems" | "camera" | "music" | "news" | "market" | "intel" | "settings" | "hud" | "integrated";
type HudModule = "chat" | "news" | "market" | "systems" | "camera" | "music" | "intel" | "settings" | null;

function readWindowKind(): WindowKind {
  const p = new URLSearchParams(window.location.search);
  const w = p.get("window");
  switch (w) { case "launcher": case "chat": case "systems": case "camera": case "music": case "news": case "market": case "intel": case "settings": case "hud": return w; case "integrated": return "integrated"; default: return "hud"; }
}

export default function App() {
  const k = readWindowKind();
  if (k === "launcher") return <OriginShell mode="orb" />;
  if (k === "integrated") return <OriginShell mode="integrated" />;
  if (k === "hud") return <OriginShell mode="hud" />;
  return <SubShell title={k.toUpperCase()}><ModuleFor k={k} /></SubShell>;
}

const LazyFallback = () => <div className="lazy-loading" />;

function ModuleFor({ k }: { k: WindowKind }) {
  return <Suspense fallback={<LazyFallback />}>{(() => {
    switch (k) { case "chat": return <ChatInterface />; case "systems": return <Dashboard />; case "camera": return <CameraView defaultOpen />; case "music": return <MusicPlaceholder />; case "news": return <NewsPanel />; case "market": return <MarketPanel />; case "intel": return <IntelPanel />; case "settings": return <SettingsPanel />; default: return <ChatInterface />; }
  })()}</Suspense>;
}

// ────────────────────────────────────────────────────────────────
// Origin Shell — un solo componente, tres vistas: HUD | ORB | WIN
// ────────────────────────────────────────────────────────────────

type ShellMode = "orb" | "integrated" | "hud";

function OriginShell({ mode }: { mode: ShellMode }) {
  const [voiceState, setVoiceState] = useState<VoiceState>("idle");
  const [transcripts, setTranscripts] = useState<VoiceTranscript[]>([]);
  const [activeSection, setActiveSection] = useState<NavSection>("home");
  const [activeModule, setActiveModule] = useState<HudModule>(null);
  const [visionActive, setVisionActive] = useState(false);
  const [visionQ, setVisionQ] = useState("Que ves? Describe en detalle.");
  const [visionT, setVisionT] = useState(0);
  const [panels, setPanels] = useState(true); // Empiezan visibles
  const [quick, setQuick] = useState(false);
  const [clip, setClip] = useState(false);
  const [orbPos, setOrbPos] = useState({ x: 0, y: 0 });
  const [orbCentered, setOrbCentered] = useState(true);
  const [transMode, setTransMode] = useState<ShellMode>(mode);
  const [suggestion, setSuggestion] = useState("Di 'Hey Origin' o pulsa Ctrl+Alt+Space para hablar");
  const webcamRef = useRef<HTMLVideoElement>(null);
  const dragging = useRef(false);
  const dragOff = useRef({ x: 0, y: 0 });
  const cmdCursor = useRef(0);
  const idleTimer = useRef<ReturnType<typeof setInterval> | null>(null);

  const H = mode === "hud";
  const I = mode === "integrated";
  const O = mode === "orb";
  const latestTr = transcripts.slice(-5);
  const active = voiceState === "listening" || voiceState === "speaking" || voiceState === "thinking";

  // ── Mode transition ─────────────────────────────────────
  useEffect(() => { setTransMode(mode); }, [mode]);

  // ── Proactive: cycle suggestions when idle ───────────────
  useEffect(() => {
    if (active || !H) return;
    const tips = [
      "Di 'Hey Origin' para hablar",
      "Pregunta 'como esta el sistema'",
      "Di 'abre el chat' para texto",
      "Pide 'noticias' o 'mercado'",
      "Di 'activa vision' para ver",
      "Di 'intel' para Crucix + Osiris",
      "Pulsa P para mostrar/ocultar paneles",
      "Pulsa ESC para cerrar modulos",
      "Ctrl+Alt+J cambia al orbe",
    ];
    let i = 0;
    idleTimer.current = setInterval(() => {
      setSuggestion(tips[i % tips.length]);
      i++;
    }, 5000);
    return () => { if (idleTimer.current) clearInterval(idleTimer.current); };
  }, [active, H]);

  // ── Keyboard shortcuts ──────────────────────────────────
  useEffect(() => {
    if (!H) return;
    const h = (e: KeyboardEvent) => {
      if (e.target && ["INPUT","TEXTAREA"].includes((e.target as HTMLElement).tagName)) return;
      if (e.key === "Escape") { if (activeModule) { setActiveModule(null); setVisionActive(false); } else setPanels(false); }
      if (e.key === "p" && !e.ctrlKey && !e.altKey) setPanels(p => !p);
      if (e.key === "Tab") { e.preventDefault(); setPanels(p => !p); }
    };
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, [H, activeModule]);

  // ── Deep links ──────────────────────────────────────────
  useEffect(() => {
    if (!isTauri()) return;
    let u: (() => void) | undefined;
    import("@tauri-apps/api/event").then(({ listen }) => {
      listen<string>("deep-link", (ev) => {
        const s = deepSection(ev.payload);
        if (s) setActiveSection(s);
      }).then(fn => { u = fn; });
    });
    return () => u?.();
  }, []);

  // ── Voice commands ᗜ modules ─────────────────────────────
  useEffect(() => {
    if (transcripts.length <= cmdCursor.current) return;
    for (const tr of transcripts.slice(cmdCursor.current)) {
      cmdCursor.current = transcripts.length;
      if (tr.role !== "user") continue;
      const cmd = detectCmd(tr.text);
      if (!cmd) continue;
      if (cmd.vision !== undefined) { setVisionActive(cmd.vision); if (cmd.vision) { setVisionQ(tr.text); setVisionT(n => n + 1); } }
      if ("module" in cmd) openMod(cmd.module ?? null);
    }
  }, [transcripts]);

  // ── Mode switches ───────────────────────────────────────
  const toOrb = useCallback(() => {
    if (!isTauri()) return;
    void import("@tauri-apps/api/core").then(({ invoke }) => { void invoke("set_window_mode", { mode: "orb" }); });
    const u = new URL(window.location.href); u.searchParams.set("window", "launcher"); window.location.assign(u.toString());
  }, []);
  const toHud = useCallback(() => {
    if (!isTauri()) return;
    void import("@tauri-apps/api/core").then(({ invoke }) => { void invoke("set_window_mode", { mode: "hud" }); });
  }, []);
  const toWin = useCallback(() => {
    if (!isTauri()) return;
    void import("@tauri-apps/api/core").then(({ invoke }) => { void invoke("set_window_mode", { mode: "integrated" }); });
    const u = new URL(window.location.href); u.searchParams.set("window", "integrated"); window.location.assign(u.toString());
  }, []);

  useGlobalHotkeys({
    onToggleMode: () => { if (I) toOrb(); else if (H) toWin(); },
    onTrayAction: (act) => {
      if (act === "mode_orb") toOrb();
      if (act === "mode_hud" || act === "mode_full") { toHud(); const u = new URL(window.location.href); u.searchParams.set("window", "hud"); window.location.assign(u.toString()); }
    },
    onQuickQuery: useCallback(() => setQuick(p => !p), []),
    onClipboardAnalyze: useCallback(() => setClip(true), []),
    onScreenshot: useCallback(() => setActiveSection("camera"), []),
  });

  // ── Draggable orb ───────────────────────────────────────
  const orbD = (e: React.PointerEvent) => { dragging.current = true; const r = (e.currentTarget as HTMLElement).getBoundingClientRect(); dragOff.current = { x: e.clientX - r.left - r.width / 2, y: e.clientY - r.top - r.height / 2 }; (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId); };
  const orbM = (e: React.PointerEvent) => { if (!dragging.current) return; setOrbPos({ x: e.clientX - window.innerWidth / 2 - dragOff.current.x, y: e.clientY - window.innerHeight / 2 - dragOff.current.y }); setOrbCentered(false); };
  const orbU = () => { dragging.current = false; };

  function openMod(mod: HudModule) { setActiveModule(mod); if (!mod) return; setPanels(false); if (mod === "camera") { setVisionActive(true); return; } }

  const secTitles: Record<NavSection, string> = { home: "Inicio", chat: "Chat", voice: "Voz", systems: "Sistemas", camera: "Vision", music: "Musica", intel: "Inteligencia", settings: "Configuracion" };
  const shellClass = `origin-shell origin-shell--${transMode}`;

  return (
    <div className={shellClass}>
      {/* ═══════ INTEGRATED MODE ═══════ */}
      {I && <>
        <Win11TitleBar subtitle={secTitles[activeSection]} />
        <div className="win-app__body">
          <Win11Sidebar active={activeSection} onChange={setActiveSection} />
          <main className="win-app__content">
            {activeSection === "home" && <HomePanel />}
            {activeSection === "voice" && <div className="win-app__voice-section"><VoiceConversation compact manageClickThrough={false} onVoiceStateChange={setVoiceState} onTranscriptUpdate={setTranscripts} /></div>}
            {activeSection === "music" && <MusicPlaceholder />}
            <Suspense fallback={<LazyFallback />}>
              {activeSection === "chat" && <ChatInterface />}
              {activeSection === "systems" && <Dashboard />}
              {activeSection === "camera" && <CameraView defaultOpen />}
              {activeSection === "intel" && <IntelPanel />}
              {activeSection === "settings" && <SettingsPanel />}
            </Suspense>
          </main>
        </div>
        {activeSection !== "voice" && <button className="win-app__voice-float" onClick={() => setActiveSection("voice")} title="Voz"><svg viewBox="0 0 24 24" fill="currentColor" width="20" height="20"><path d="M12 2a4 4 0 014 4v4a4 4 0 01-8 0V6a4 4 0 014-4z"/><path d="M6 12a6 6 0 0012 0"/></svg></button>}
      </>}

      {/* ═══════ HUD MODE ═══════ */}
      {H && <>
        {visionActive && <Suspense fallback={<LazyFallback />}><WebcamBackground videoRef={webcamRef} /></Suspense>}

        {/* Header */}
        <header className="vhud-topline">
          <div className="vhud-brand">
            <span className="vhud-brand__kicker">Origin_OS</span>
            <strong>VOICE</strong>
            <span className={`vhud-brand__state vhud-brand__state--${voiceState}`}>{voiceState.toUpperCase()}</span>
          </div>
          <HudClock />
        </header>

        {/* Panels */}
        <Suspense fallback={<LazyFallback />}>
          <div className={`vhud-panel vhud-panel--left ${panels ? "vhud-panel--visible" : ""}`}><NewsPanel /></div>
          <div className={`vhud-panel vhud-panel--right ${panels ? "vhud-panel--visible" : ""}`}><MarketPanel /></div>
        </Suspense>

        {/* Activity Log */}
        <Suspense fallback={<LazyFallback />}>
          <div className={`vhud-activity ${panels ? "vhud-activity--visible" : ""}`}><ActivityLog /></div>
        </Suspense>

        {/* Transcript + Suggestions */}
        <section className={`vhud-transcript ${latestTr.length ? "vhud-transcript--active" : "vhud-transcript--hint"}`}>
          <div className="vhud-transcript__header"><span>COMMS</span><span>{latestTr.length ? "LIVE" : "STANDBY"}</span></div>
          <div className="vhud-transcript__body">
            {latestTr.length === 0 ? (
              <p className="vhud-transcript__hint fade-in">{suggestion}</p>
            ) : latestTr.map((tr, i) => (
              <p key={`${tr.timestamp}-${i}`} className={`vhud-transcript__line vhud-transcript__line--${tr.role} fade-in`}><span>{tr.role === "user" ? "YOU" : "Origin"}</span>{tr.text}</p>
            ))}
          </div>
        </section>

        {/* Inline panel */}
        <div className={`vhud-inline ${activeModule ? "vhud-inline--open" : ""}`}>
          <header className="vhud-inline__bar">
            <span className="vhud-inline__title">{activeModule === "camera" ? "VISION" : activeModule === "chat" ? "CHAT" : activeModule === "systems" ? "SYSTEMS" : activeModule === "news" ? "NEWS" : activeModule === "market" ? "MARKET" : activeModule === "intel" ? "INTELLIGENCE" : activeModule === "music" ? "MUSIC" : activeModule === "settings" ? "SETTINGS" : ""}</span>
            <span className="vhud-inline__status">{voiceState.toUpperCase()}</span>
            <button className="vhud-inline__close" onClick={() => { if (activeModule === "camera") setVisionActive(false); setActiveModule(null); }}>ESC</button>
          </header>
          <div className="vhud-inline__body">
            <Suspense fallback={<LazyFallback />}>
              {activeModule === "chat" && <ChatInterface />}
              {activeModule === "news" && <NewsPanel />}
              {activeModule === "market" && <MarketPanel />}
              {activeModule === "systems" && <Dashboard />}
              {activeModule === "camera" && <CameraView defaultOpen autoAnalyzeQuestion={visionQ} autoAnalyzeToken={visionT} onClose={() => { setVisionActive(false); setActiveModule(null); }} />}
              {activeModule === "intel" && <IntelPanel />}
              {activeModule === "music" && <MusicPlaceholder />}
              {activeModule === "settings" && <SettingsPanel />}
            </Suspense>
          </div>
        </div>

        {/* Ticker + Controls */}
        <div className={`vhud-ticker ${panels ? "vhud-ticker--visible" : ""}`}><Suspense fallback={<LazyFallback />}><NewsTicker /></Suspense></div>
        <button className="vhud-toggle-panels" onClick={() => setPanels(p => !p)} title={panels ? "Ocultar (P)" : "Mostrar (P)"}>{panels ? "HIDE" : "PANELS"}</button>

        {/* Mode bar */}
        <div className="vhud-modebar">
          <button className={`vhud-modebar__btn${O ? " vhud-modebar__btn--active" : ""}`} onClick={toOrb}>ORB</button>
          <button className="vhud-modebar__btn vhud-modebar__btn--active" disabled>HUD</button>
          <button className="vhud-modebar__btn" onClick={toWin}>WINDOW</button>
        </div>

        <Suspense fallback={<LazyFallback />}>
          {quick && <QuickQuery onClose={() => setQuick(false)} />}
          {clip && <ClipboardAnalyzer onClose={() => setClip(false)} />}
        </Suspense>
      </>}

      {/* ═══════ CORE: Voice orb (ALL modes) ═══════ */}
      <div
        className={H ? `vhud-orb${orbCentered ? " vhud-orb--centered" : ""}${active ? " vhud-orb--active" : ""}` : O ? "origin-core-orb origin-core-orb--center" : "origin-core-orb"}
        style={H && !orbCentered ? { transform: `translate(calc(-50% + ${orbPos.x}px), calc(-50% + ${orbPos.y}px))` } : undefined}
        onPointerDown={H ? orbD : undefined} onPointerMove={H ? orbM : undefined} onPointerUp={H ? orbU : undefined}
      >
        <VoiceConversation hudPuck={O} hudFull={H} compact={I} manageClickThrough={O} onVoiceStateChange={setVoiceState} onTranscriptUpdate={setTranscripts} />
      </div>
    </div>
  );
}

// ── Helpers ────────────────────────────────────────────────────

function SubShell({ title, children }: { title: string; children: React.ReactNode }) {
  return <div className="subwindow"><Win11TitleBar title={`Origin  ·  ${title}`} /><main className="subwindow__body">{children}</main></div>;
}

function MusicPlaceholder() {
  return <div className="music-placeholder"><div className="music-placeholder__title">MUSICA</div><p>Control de Spotify por voz.</p><ul className="music-placeholder__cmds"><li>"Pon musica de [artista]"</li><li>"Pausa" / "Reanuda"</li><li>"Siguiente" / "Anterior"</li></ul></div>;
}

function deepSection(url: string): NavSection | null {
  try { const p = new URL(url); const path = p.hostname || p.pathname.replace(/^\/+/, ""); const m: Record<string, NavSection> = { home: "home", chat: "chat", systems: "systems", camera: "camera", music: "music", intel: "intel", crucix: "intel", osiris: "intel", settings: "settings", vision: "camera", config: "settings" }; return m[path] ?? null; } catch { return null; }
}

function detectCmd(text: string): { module?: HudModule; vision?: boolean } | null {
  const t = text.toLowerCase().normalize("NFD").replace(/[\u0300-\u036f]/g, "");
  if (/\b(cierra|apaga|desactiva|quita|oculta)\b.*\b(vision|camara|camera)\b/.test(t)) return { vision: false };
  if (/\b(vision|camara|camera|webcam|ver|ve|mira|analiza lo que ves)\b/.test(t)) return { vision: true, module: "camera" };
  if (/\b(chat|texto|textual|escribir)\b/.test(t)) return { module: "chat" };
  if (/\b(noticias|news|titulares)\b/.test(t)) return { module: "news" };
  if (/\b(mercado|market|bolsa|finanzas)\b/.test(t)) return { module: "market" };
  if (/\b(sistemas|dashboard|estado|cpu|ram)\b/.test(t)) return { module: "systems" };
  if (/\b(musica|spotify|cancion)\b/.test(t)) return { module: "music" };
  if (/\b(intel|inteligencia|crucix|osiris|osint|radar|vigilancia)\b/.test(t)) return { module: "intel" };
  if (/\b(configuracion|ajustes|settings)\b/.test(t)) return { module: "settings" };
  if (/\b(cierra|cerrar|oculta|limpia)\b.*\b(panel|ventana|modulo)\b/.test(t)) return { module: null };
  return null;
}

function HudClock() {
  const [now, setNow] = useState(new Date());
  useEffect(() => { const id = setInterval(() => setNow(new Date()), 1000); return () => clearInterval(id); }, []);
  const time = now.toLocaleTimeString("es-ES", { hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false });
  const date = now.toLocaleDateString("es-ES", { weekday: "long", day: "numeric", month: "long", year: "numeric" });
  return <div className="vhud-clock"><div className="vhud-clock__time">{time}</div><div className="vhud-clock__date">{date.toUpperCase()}</div></div>;
}
