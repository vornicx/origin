"""
WakeWord Skill — Modo conversacional siempre activo para Origin.

Detecta la palabra clave "origin" y procesa comandos de voz automáticamente,
al estilo Siri / Alexa.

Flujo:
    IDLE → escucha pasiva → detecta wake word
    WAKE → sonido alerta + broadcast
    LISTENING → escucha comando (hasta 10s)
    PROCESSING → mind.think()
    SPEAKING → TTS respuesta
    → vuelve a IDLE
"""

import asyncio
import logging
import threading
import time
from typing import Any, Callable, Dict, List, Optional

from .base_skill import BaseSkill

logger = logging.getLogger("origin.skills.wake_word")

WAKE_WORDS: List[str] = [
    "origin",
    "hey origin",
    "oye origin",
    "hola origin",
    "ok origin",
    "ey origin",
]

# States
IDLE = "idle"
WAKE = "wake_detected"
LISTENING = "listening_command"
PROCESSING = "processing"
SPEAKING = "speaking"
ERROR = "error"


class WakeWordSkill(BaseSkill):
    """
    Daemon de escucha continua con activación por palabra clave.

    Requiere speech_recognition (pip install SpeechRecognition).
    Usa Google STT para reconocimiento (gratuito, requiere internet).
    """

    def __init__(self):
        super().__init__(
            name="wake_word",
            description="Modo siempre-escuchando: activa Origin con 'Hey Origin' como Siri/Alexa",
        )
        self._running = False
        self._state = IDLE
        self._thread: Optional[threading.Thread] = None
        self._stop_bg_listening: Optional[Callable] = None

        # Injected dependencies
        self._event_loop: Optional[asyncio.AbstractEventLoop] = None
        self._mind = None
        self._voice_skill = None

        # Broadcast callback (connected to WebSocket manager)
        self._broadcast_fn: Optional[Callable] = None

        # Stats
        self._wake_count = 0
        self._commands_processed = 0
        self._last_wake: Optional[str] = None
        self._last_command: Optional[str] = None
        self._error_message: Optional[str] = None

    # ── Dependency injection ──────────────────────────────────────

    def set_dependencies(
        self,
        event_loop: asyncio.AbstractEventLoop,
        mind,
        voice_skill,
    ) -> None:
        """Inject runtime dependencies. Call before start()."""
        self._event_loop = event_loop
        self._mind = mind
        self._voice_skill = voice_skill

    def set_broadcast(self, fn: Callable) -> None:
        """Inject WebSocket broadcast callback."""
        self._broadcast_fn = fn

    # ── BaseSkill interface ───────────────────────────────────────

    def validate_inputs(self, inputs: Dict[str, Any]) -> tuple[bool, str]:
        action = inputs.get("action", "")
        valid = ("start", "stop", "status", "set_sensitivity")
        if action not in valid:
            return False, f"Acción inválida: '{action}'. Válidas: {valid}"
        return True, ""

    async def execute(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        start = time.time()
        action = inputs.get("action", "status")

        try:
            if action == "start":
                result = self._start(inputs)
            elif action == "stop":
                result = self._stop()
            elif action == "status":
                result = self._get_status()
            elif action == "set_sensitivity":
                result = self._set_sensitivity(inputs)
            else:
                result = {"error": f"Acción desconocida: {action}"}

            success = result.get("error") is None
            return {
                "success": success,
                "result": result,
                "error": result.get("error"),
                "execution_time": round(time.time() - start, 3),
            }
        except Exception as e:
            logger.error(f"WakeWord skill error: {e}")
            return {
                "success": False,
                "result": None,
                "error": str(e),
                "execution_time": round(time.time() - start, 3),
            }

    # ── Start / Stop ─────────────────────────────────────────────

    def _start(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        if self._running:
            return {"status": "already_running", "state": self._state}

        if not self._event_loop or not self._mind:
            return {"error": "Dependencias no inyectadas. Inicia desde /voice/wake-word/start"}

        try:
            import speech_recognition as sr  # noqa: F401
        except ImportError:
            return {"error": "SpeechRecognition no instalado: pip install SpeechRecognition"}

        language = inputs.get("language", "es-ES")
        self._language = language
        self._energy_threshold = inputs.get("energy_threshold", 300)

        self._running = True
        self._state = IDLE
        self._thread = threading.Thread(
            target=self._listen_loop,
            name="origin-wake-word",
            daemon=True,
        )
        self._thread.start()
        logger.info(f"WakeWord daemon started (lang={language})")
        self._broadcast({"type": "wake_word_event", "event": "started", "state": IDLE})
        return {"status": "started", "language": language, "wake_words": WAKE_WORDS}

    def _stop(self) -> Dict[str, Any]:
        if not self._running:
            return {"status": "not_running"}

        self._running = False
        if self._stop_bg_listening:
            try:
                self._stop_bg_listening(wait_for_stop=False)
            except Exception:
                pass
        self._state = IDLE
        self._broadcast({"type": "wake_word_event", "event": "stopped", "state": IDLE})
        logger.info("WakeWord daemon stopped")
        return {"status": "stopped"}

    def _set_sensitivity(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        self._energy_threshold = inputs.get("energy_threshold", self._energy_threshold)
        return {"energy_threshold": self._energy_threshold}

    def _get_status(self) -> Dict[str, Any]:
        return {
            "running": self._running,
            "state": self._state,
            "wake_words": WAKE_WORDS,
            "wake_count": self._wake_count,
            "commands_processed": self._commands_processed,
            "last_wake": self._last_wake,
            "last_command": self._last_command,
            "error": self._error_message,
        }

    # ── Main listen loop (runs in daemon thread) ──────────────────

    def _listen_loop(self) -> None:
        try:
            import speech_recognition as sr
        except ImportError:
            logger.error("speech_recognition not available")
            return

        recognizer = sr.Recognizer()
        recognizer.energy_threshold = self._energy_threshold
        recognizer.dynamic_energy_threshold = True
        recognizer.pause_threshold = 0.8

        logger.info("WakeWord: entering listen loop")

        while self._running:
            try:
                with sr.Microphone() as source:
                    # Calibrate quickly
                    recognizer.adjust_for_ambient_noise(source, duration=0.3)

                    # Listen for utterance (timeout 4s silence = give up chunk)
                    audio = recognizer.listen(
                        source,
                        timeout=4,
                        phrase_time_limit=5,
                    )

                if not self._running:
                    break

                # Recognize in thread pool executor to not block the loop
                try:
                    text = recognizer.recognize_google(audio, language=getattr(self, "_language", "es-ES")).lower()
                    logger.debug(f"WakeWord heard: {text!r}")
                except sr.UnknownValueError:
                    continue
                except sr.RequestError as e:
                    logger.warning(f"WakeWord STT request error: {e}")
                    time.sleep(1)
                    continue

                # Check for wake word
                if any(kw in text for kw in WAKE_WORDS):
                    self._handle_wake(text, recognizer)

            except sr.WaitTimeoutError:
                # Normal: silence timeout, just loop again
                continue
            except OSError as e:
                logger.error(f"WakeWord microphone error: {e}")
                self._state = ERROR
                self._error_message = str(e)
                time.sleep(3)
            except Exception as e:
                if self._running:
                    logger.error(f"WakeWord loop error: {e}")
                    time.sleep(1)

        logger.info("WakeWord: listen loop exited")

    # ── Wake event handler ────────────────────────────────────────

    @staticmethod
    def _strip_wake_word(text: str) -> str:
        """Remove the wake word(s) from the beginning of the transcript.
        E.g. 'hey origin qué hora es' → 'qué hora es'.
        Returns empty string if nothing remains after stripping.
        """
        t = text.lower().strip()
        # Try longest phrases first so 'hey origin' wins over 'origin'
        for kw in sorted(WAKE_WORDS, key=len, reverse=True):
            if t.startswith(kw):
                return text[len(kw) :].lstrip(" ,.;:!?").strip()
        # Wake word might appear inside the phrase ("ok origin qué hora es")
        for kw in WAKE_WORDS:
            idx = t.find(kw)
            if idx != -1:
                return text[idx + len(kw) :].lstrip(" ,.;:!?").strip()
        return ""

    def _handle_wake(self, trigger_text: str, recognizer) -> None:
        """Called when wake word detected. Runs in the daemon thread.

        Smart flow (Siri-style):
          1. Try to extract the command from the SAME utterance that triggered the wake
             ("origin qué hora es" → command = "qué hora es"). One-breath flow.
          2. If trigger only contained the wake word, listen for a follow-up command.
        """
        import speech_recognition as sr

        self._wake_count += 1
        self._last_wake = time.strftime("%H:%M:%S")
        self._state = WAKE

        logger.info(f"Wake word detected! Trigger: {trigger_text!r}")
        self._broadcast(
            {
                "type": "wake_word_event",
                "event": "wake_detected",
                "state": WAKE,
                "trigger": trigger_text,
            }
        )

        # Try to extract the command from the wake utterance itself (one-breath flow)
        inline_command = self._strip_wake_word(trigger_text)
        command_text: str = ""

        if len(inline_command.split()) >= 2:
            # Already got a usable command in the wake phrase — skip the second listen
            command_text = inline_command
            logger.info(f"WakeWord: command extracted from trigger: {command_text!r}")
            self._play_wake_sound()
        else:
            # Wake word was bare ("hey origin") — beep then listen for the actual command
            self._play_wake_sound()
            self._state = LISTENING
            self._broadcast(
                {
                    "type": "wake_word_event",
                    "event": "listening_command",
                    "state": LISTENING,
                }
            )
            try:
                with sr.Microphone() as source:
                    recognizer.adjust_for_ambient_noise(source, duration=0.2)
                    logger.info("WakeWord: listening for command...")
                    audio = recognizer.listen(source, timeout=8, phrase_time_limit=15)
                command_text = recognizer.recognize_google(audio, language=getattr(self, "_language", "es-ES"))
                logger.info(f"Command recognized: {command_text!r}")
            except sr.WaitTimeoutError:
                logger.info("WakeWord: no command heard (timeout)")
                self._state = IDLE
                self._broadcast({"type": "wake_word_event", "event": "timeout", "state": IDLE})
                return
            except sr.UnknownValueError:
                logger.info("WakeWord: command not understood")
                self._state = IDLE
                self._broadcast({"type": "wake_word_event", "event": "not_understood", "state": IDLE})
                self._speak_async("No entendí el comando. Intenta de nuevo.")
                return
            except Exception as e:
                logger.error(f"WakeWord command listen error: {e}")
                self._state = IDLE
                return

        if not command_text.strip():
            self._state = IDLE
            return

        self._last_command = command_text
        self._state = PROCESSING
        self._broadcast(
            {
                "type": "wake_word_event",
                "event": "processing",
                "state": PROCESSING,
                "command": command_text,
            }
        )

        # Process command via mind.think() in the event loop
        answer = self._call_mind(command_text)

        self._commands_processed += 1
        self._state = SPEAKING
        self._broadcast(
            {
                "type": "wake_word_event",
                "event": "speaking",
                "state": SPEAKING,
                "answer": answer[:200] if answer else "",
            }
        )

        # Speak the response
        if answer:
            self._speak_async(answer)
            # Wait for speech to likely finish before going idle
            speak_wait = min(len(answer) * 0.06, 15.0)
            time.sleep(speak_wait)

        self._state = IDLE
        self._broadcast({"type": "wake_word_event", "event": "idle", "state": IDLE})

    # ── Async bridge helpers ──────────────────────────────────────

    def _call_mind(self, text: str) -> str:
        """Call mind.think() from the daemon thread via run_coroutine_threadsafe."""
        if not self._mind or not self._event_loop:
            return "Sistema no disponible."
        try:
            future = asyncio.run_coroutine_threadsafe(
                self._mind.think(text),
                self._event_loop,
            )
            result = future.result(timeout=60)
            return result.final_answer or "Sin respuesta."
        except asyncio.TimeoutError:
            logger.error("WakeWord: mind.think() timed out after 60s")
            return "El razonamiento tardó demasiado. Inténtalo de nuevo."
        except Exception as e:
            logger.error(f"WakeWord: mind.think() error: {e}")
            return "Ocurrió un error procesando tu solicitud."

    def _speak_async(self, text: str) -> None:
        """Speak text via voice skill. Fire-and-forget from daemon thread."""
        if not self._voice_skill or not self._event_loop:
            return
        try:
            future = asyncio.run_coroutine_threadsafe(
                self._voice_skill.execute({"action": "speak", "text": text}),
                self._event_loop,
            )
            future.result(timeout=30)
        except Exception as e:
            logger.warning(f"WakeWord TTS error: {e}")

    def _broadcast(self, message: dict) -> None:
        """Broadcast a message to all WebSocket clients."""
        if not self._broadcast_fn or not self._event_loop:
            return
        try:
            asyncio.run_coroutine_threadsafe(
                self._broadcast_fn(message),
                self._event_loop,
            )
        except Exception as e:
            logger.debug(f"WakeWord broadcast error: {e}")

    def _play_wake_sound(self) -> None:
        """Play a short chime to signal wake detection."""
        try:
            import subprocess

            # Generate a simple beep via PowerShell
            subprocess.Popen(
                [
                    "powershell",
                    "-WindowStyle",
                    "Hidden",
                    "-NoProfile",
                    "-Command",
                    "[console]::beep(880, 150); [console]::beep(1100, 100)",
                ],
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
        except Exception:
            pass
