"""
VoiceSession — Sesion de voz en tiempo real estilo Mark-XXXIX / OpenAI Realtime.

Pipeline:
  Browser captura PCM 16kHz mono → WebSocket binario
  → Server bufferea chunks
  → webrtcvad detecta end-of-utterance (silence > N ms)
  → Whisper transcribe el buffer
  → InjectionGuard filtra
  → Mind.think() reasoning loop
  → ElevenLabs TTS streaming
  → WebSocket envia chunks MP3 al browser
  → Browser reproduce con Web Audio API

Estados broadcasted al cliente:
  idle      - Esperando audio
  listening - Hay audio entrante con voz
  thinking  - Procesando (Whisper + Mind)
  speaking  - Enviando audio TTS al cliente
  muted     - Mic silenciado por el usuario
  error     - Algo fallo (con mensaje)

Half-duplex echo-gate:
  Mientras state == speaking, el audio entrante se IGNORA.
  Esto evita feedback sin necesidad de AEC.

Barge-in:
  Si llegan audio chunks con voz mientras state == speaking,
  se cancela el TTS y se vuelve a state listening.
  (controlado por el cliente que detiene playback al detectar voz)
"""

import asyncio
import base64
import logging
import os
import time
import wave
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, Optional

logger = logging.getLogger("origin.voice_session")
ORIGIN_ROOT = Path(__file__).resolve().parent.parent

# ── Audio config ──────────────────────────────────────────────
SAMPLE_RATE = 16000  # Hz — webrtcvad needs 8k/16k/32k/48k
SAMPLE_WIDTH = 2  # int16 → 2 bytes/sample
CHANNELS = 1
FRAME_DURATION_MS = 30  # webrtcvad accepts 10/20/30 ms frames
FRAME_BYTES = SAMPLE_RATE * SAMPLE_WIDTH * FRAME_DURATION_MS // 1000  # 960 bytes

# ── VAD tuning ────────────────────────────────────────────────
VAD_AGGRESSIVENESS = int(os.getenv("ORIGIN_VOICE_VAD_AGGRESSIVENESS", "3"))  # 0-3, higher = stricter
SILENCE_TIMEOUT_MS = int(os.getenv("ORIGIN_VOICE_SILENCE_TIMEOUT_MS", "550"))
MIN_UTTERANCE_MS = int(os.getenv("ORIGIN_VOICE_MIN_UTTERANCE_MS", "450"))
MAX_UTTERANCE_MS = int(os.getenv("ORIGIN_VOICE_MAX_UTTERANCE_MS", "15000"))
PREROLL_MS = 200  # Audio kept before first detected voice frame
ENERGY_THRESHOLD = int(os.getenv("ORIGIN_VOICE_ENERGY_THRESHOLD", "700"))
ENERGY_FALLBACK_THRESHOLD = int(os.getenv("ORIGIN_VOICE_FALLBACK_THRESHOLD", "1200"))
BARGE_IN_THRESHOLD = int(os.getenv("ORIGIN_VOICE_BARGE_IN_THRESHOLD", "2200"))

SENSITIVITY_PRESETS = {
    "low": {"energy": 900, "fallback": 1500, "barge": 2600, "silence": 450},
    "medium": {"energy": 700, "fallback": 1200, "barge": 2200, "silence": 550},
    "high": {"energy": 450, "fallback": 900, "barge": 1700, "silence": 700},
}

# ── TTS streaming ─────────────────────────────────────────────
TTS_CHUNK_SIZE = 4096  # Bytes per audio chunk sent to client


class State(str, Enum):
    IDLE = "idle"
    LISTENING = "listening"
    THINKING = "thinking"
    SPEAKING = "speaking"
    MUTED = "muted"
    ERROR = "error"


@dataclass
class SessionStats:
    """Per-session counters."""

    utterances_processed: int = 0
    audio_seconds_received: float = 0.0
    audio_seconds_spoken: float = 0.0
    avg_latency_ms: float = 0.0
    last_transcript: str = ""
    last_answer: str = ""
    last_latency_ms: float = 0.0
    errors: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "utterances_processed": self.utterances_processed,
            "audio_seconds_received": round(self.audio_seconds_received, 1),
            "audio_seconds_spoken": round(self.audio_seconds_spoken, 1),
            "avg_latency_ms": round(self.avg_latency_ms, 1),
            "last_transcript": self.last_transcript[:200],
            "last_answer": self.last_answer[:200],
            "last_latency_ms": round(self.last_latency_ms, 1),
            "errors": self.errors,
        }


class VoiceSession:
    """Sesion de voz bidireccional para un cliente WebSocket.

    Hilo de control:
      - El consumer del WS llama add_audio_chunk(pcm_bytes) por cada paquete.
      - Cuando VAD detecta end-of-utterance, dispara _process_utterance() en background.
      - El processing emite state changes + transcript + audio chunks al cliente
        via la funcion broadcast inyectada.

    No bloquea el WS receive loop — todo es async + executor para CPU-bound work.
    """

    def __init__(
        self, send_fn: Callable[[Dict[str, Any]], Awaitable[None]], mind, skill_executor, language: str = "es"
    ):
        self._send = send_fn
        self._mind = mind
        self._skill_executor = skill_executor
        self._language = language

        self._state = State.IDLE
        self._muted = False
        self._buffer = bytearray()  # Current utterance buffer (raw PCM)
        self._preroll_buffer = deque()  # Pre-voice frames (FIFO of frames)
        self._preroll_max_frames = PREROLL_MS // FRAME_DURATION_MS
        self._voiced_ms = 0
        self._silence_ms = 0
        self._in_speech = False
        self._frame_remainder = bytearray()  # Partial frame from previous chunk
        self._last_activity_at = time.time()
        self._energy_threshold = ENERGY_THRESHOLD
        self._fallback_energy_threshold = ENERGY_FALLBACK_THRESHOLD
        self._barge_in_threshold = BARGE_IN_THRESHOLD
        self._silence_timeout_ms = SILENCE_TIMEOUT_MS
        self._sensitivity = os.getenv("ORIGIN_VOICE_SENSITIVITY", "low").lower()
        if self._sensitivity in SENSITIVITY_PRESETS:
            self._apply_sensitivity(self._sensitivity)

        self._processing_task: Optional[asyncio.Task] = None
        self._tts_task: Optional[asyncio.Task] = None
        self._tts_cancel_event = asyncio.Event()
        self._closed = False

        self.stats = SessionStats()

        # Init VAD
        try:
            import webrtcvad

            self._vad = webrtcvad.Vad(VAD_AGGRESSIVENESS)
            self._vad_available = True
        except ImportError:
            logger.warning("webrtcvad not available, using energy-based VAD fallback")
            self._vad = None
            self._vad_available = False

        logger.info(
            f"VoiceSession created: vad={'webrtcvad' if self._vad_available else 'energy'}, "
            f"lang={language}, sr={SAMPLE_RATE}Hz"
        )

    # ── Public API ────────────────────────────────────────────

    async def start(self):
        """Annouce session ready to the client."""
        await self._set_state(State.IDLE)
        await self._send(
            {
                "type": "voice_session_ready",
                "config": {
                    "sample_rate": SAMPLE_RATE,
                    "channels": CHANNELS,
                    "frame_ms": FRAME_DURATION_MS,
                    "silence_timeout_ms": self._silence_timeout_ms,
                    "sensitivity": self._sensitivity,
                    "energy_threshold": self._energy_threshold,
                    "language": self._language,
                },
            }
        )

    async def add_audio_chunk(self, pcm_bytes: bytes):
        """Procesa un chunk PCM int16 16kHz mono.

        - Hace VAD por frame.
        - Acumula en buffer durante voz.
        - Cuando detecta silencio post-voz → procesa utterance.
        """
        if self._closed:
            return

        # SECURITY: cap chunk size to avoid DoS
        if len(pcm_bytes) > 1_000_000:  # 1MB per chunk max
            logger.warning(f"Audio chunk too large ({len(pcm_bytes)} bytes), dropping")
            return

        self._last_activity_at = time.time()
        self.stats.audio_seconds_received += len(pcm_bytes) / (SAMPLE_RATE * SAMPLE_WIDTH)

        # Half-duplex gate: while speaking, only check for barge-in (any voiced frame)
        if self._state == State.SPEAKING:
            if self._detect_barge_in(pcm_bytes):
                logger.info("Barge-in detected — cancelling TTS")
                await self._cancel_tts()
                # Cancel the processing task so _end_utterance can start a new one
                if self._processing_task and not self._processing_task.done():
                    self._processing_task.cancel()
                # Reset state immediately so incoming frames are VAD-processed normally
                self._state = State.IDLE
                self._in_speech = False
                # Fall through to normal processing of this chunk
            else:
                return

        if self._muted:
            return

        # Process frame-by-frame for VAD
        data = self._frame_remainder + pcm_bytes
        self._frame_remainder = bytearray()

        offset = 0
        while offset + FRAME_BYTES <= len(data):
            frame = bytes(data[offset : offset + FRAME_BYTES])
            offset += FRAME_BYTES
            await self._process_frame(frame)

        # Save remainder for next chunk
        if offset < len(data):
            self._frame_remainder = bytearray(data[offset:])

    async def _process_frame(self, frame: bytes):
        """Procesa un frame de 30ms."""
        is_speech = self._is_speech(frame)

        if is_speech:
            if not self._in_speech:
                # Just started speaking
                self._in_speech = True
                self._voiced_ms = 0
                # Include preroll buffer (audio before voice detected)
                for pf in self._preroll_buffer:
                    self._buffer.extend(pf)
                self._preroll_buffer.clear()
                if self._state == State.IDLE:
                    await self._set_state(State.LISTENING)

            self._buffer.extend(frame)
            self._voiced_ms += FRAME_DURATION_MS
            self._silence_ms = 0

            # Hard cap
            if self._voiced_ms >= MAX_UTTERANCE_MS:
                logger.warning(f"Utterance exceeded {MAX_UTTERANCE_MS}ms, force processing")
                await self._end_utterance()

        else:
            if self._in_speech:
                # Was speaking, now silence — count silence duration
                self._buffer.extend(frame)
                self._silence_ms += FRAME_DURATION_MS

                if self._silence_ms >= self._silence_timeout_ms:
                    await self._end_utterance()
            else:
                # Pure silence, keep in preroll buffer
                self._preroll_buffer.append(frame)
                if len(self._preroll_buffer) > self._preroll_max_frames:
                    self._preroll_buffer.popleft()

    def _is_speech(self, frame: bytes) -> bool:
        """Detect speech in a 30ms frame.

        Strategy:
          1. Energy pre-filter (drops silence + low-amplitude noise).
          2. webrtcvad on frames that pass the pre-filter (rejects non-speech).
        """
        if len(frame) != FRAME_BYTES:
            return False

        # Energy pre-filter — robust against webrtcvad false positives on absolute silence
        import struct

        samples = struct.unpack(f"<{len(frame)//2}h", frame)
        energy = sum(abs(s) for s in samples) / len(samples)
        if energy < self._energy_threshold:
            return False

        if self._vad_available:
            try:
                return self._vad.is_speech(frame, SAMPLE_RATE)
            except Exception:
                return energy > self._fallback_energy_threshold
        # Energy-based fallback
        return energy > self._fallback_energy_threshold

    def _detect_barge_in(self, pcm_bytes: bytes) -> bool:
        """Check if user is speaking (during TTS playback).

        Energy-based only — webrtcvad is too restrictive for barge-in
        (it specifically targets human speech and rejects other strong audio).
        We just want: is there a loud sound suggesting the user is trying to talk?
        """
        if len(pcm_bytes) < FRAME_BYTES:
            return False
        import struct

        # Check first 3 frames worth
        for offset in range(0, min(len(pcm_bytes), FRAME_BYTES * 3), FRAME_BYTES):
            frame = pcm_bytes[offset : offset + FRAME_BYTES]
            if len(frame) != FRAME_BYTES:
                continue
            samples = struct.unpack(f"<{len(frame)//2}h", frame)
            energy = sum(abs(s) for s in samples) / len(samples)
            if energy > self._barge_in_threshold:
                return True
        return False

    async def _end_utterance(self):
        """Buffer cerrado → procesar utterance."""
        if self._voiced_ms < MIN_UTTERANCE_MS:
            # Too short, discard
            logger.debug(f"Utterance too short ({self._voiced_ms}ms), discarding")
            self._reset_buffers()
            await self._set_state(State.IDLE)
            return

        audio_bytes = bytes(self._buffer)
        voiced_ms = self._voiced_ms
        self._reset_buffers()

        # Process in background — don't block frame ingestion
        if self._processing_task and not self._processing_task.done():
            if self._tts_cancel_event.is_set():
                # Task was cancelled by barge-in — wait briefly for it to exit cleanly
                try:
                    await asyncio.wait_for(asyncio.shield(self._processing_task), timeout=0.5)
                except (asyncio.CancelledError, asyncio.TimeoutError, Exception):
                    pass
            if not self._processing_task.done():
                logger.warning("Previous utterance still processing, dropping new one")
                return

        self._tts_cancel_event.clear()
        self._processing_task = asyncio.create_task(self._process_utterance(audio_bytes, voiced_ms))

    def _reset_buffers(self):
        self._buffer.clear()
        self._preroll_buffer.clear()
        self._voiced_ms = 0
        self._silence_ms = 0
        self._in_speech = False
        self._frame_remainder.clear()

    # ── Processing pipeline ───────────────────────────────────

    async def _process_utterance(self, pcm_bytes: bytes, voiced_ms: int):
        """STT → Guard → Mind → TTS streaming."""
        t0 = time.perf_counter()
        try:
            await self._set_state(State.THINKING)

            # 1. STT — wrap PCM in WAV and transcribe
            transcript = await self._transcribe(pcm_bytes)
            if not transcript or not transcript.strip():
                logger.info("STT returned empty, back to idle")
                await self._set_state(State.IDLE)
                return

            self.stats.last_transcript = transcript
            await self._send(
                {
                    "type": "voice_transcript",
                    "text": transcript,
                    "duration_ms": voiced_ms,
                }
            )

            # 2. Injection Guard
            guard_result = self._mind.injection_guard.analyze(transcript)
            if guard_result.is_blocked:
                logger.warning(f"Voice input blocked: score={guard_result.risk_score:.2f}")
                await self._send(
                    {
                        "type": "voice_error",
                        "message": "Mensaje bloqueado por seguridad.",
                    }
                )
                await self._set_state(State.IDLE)
                self.stats.errors += 1
                return

            if guard_result.sanitized_input and guard_result.risk_level in ("medium", "high"):
                transcript = guard_result.sanitized_input

            # 3. Mind reasoning loop
            think_t0 = time.perf_counter()
            result = await self._mind.think(transcript)
            think_ms = (time.perf_counter() - think_t0) * 1000
            answer = result.final_answer or "Sin respuesta."

            self.stats.last_answer = answer
            llm_metrics = getattr(self._mind.responder, "last_llm_result", {}) or {}
            await self._send(
                {
                    "type": "voice_answer",
                    "text": answer,
                    "cycle_id": result.cycle_id,
                    "think_ms": round(think_ms, 1),
                    "latency_ms": round(think_ms, 1),
                    "llm_latency_ms": llm_metrics.get("latency_ms"),
                    "completion_tokens": llm_metrics.get("completion_tokens"),
                    "tokens_per_s": llm_metrics.get("tokens_per_s"),
                    "provider": llm_metrics.get("provider"),
                }
            )

            # 4. TTS streaming
            await self._set_state(State.SPEAKING)
            await self._stream_tts(answer)

            # 5. Done
            self.stats.utterances_processed += 1
            total_ms = (time.perf_counter() - t0) * 1000
            self.stats.last_latency_ms = total_ms
            # Running average
            n = self.stats.utterances_processed
            self.stats.avg_latency_ms = (self.stats.avg_latency_ms * (n - 1) + total_ms) / n

            await self._set_state(State.IDLE)

        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error(f"VoiceSession processing error: {e}")
            self.stats.errors += 1
            await self._send(
                {
                    "type": "voice_error",
                    "message": f"Error procesando: {str(e)[:200]}",
                }
            )
            await self._set_state(State.IDLE)

    async def _transcribe(self, pcm_bytes: bytes) -> str:
        """Convert PCM to WAV in memory and transcribe via voice skill."""
        # Build WAV in temp file (voice_skill needs path)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        tmp_path = ORIGIN_ROOT / "data" / "audio" / f"voice_in_{timestamp}.wav"
        tmp_path.parent.mkdir(parents=True, exist_ok=True)

        try:
            def _write_wav():
                with wave.open(str(tmp_path), "wb") as wf:
                    wf.setnchannels(CHANNELS)
                    wf.setsampwidth(SAMPLE_WIDTH)
                    wf.setframerate(SAMPLE_RATE)
                    wf.writeframes(pcm_bytes)

            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, _write_wav)

            # Call voice skill transcribe
            voice_skill = self._skill_executor.get("voice")
            if not voice_skill:
                return ""

            result = await voice_skill.execute(
                {
                    "action": "transcribe",
                    "audio_path": str(tmp_path),
                    "language": self._language,
                    "engine": "whisper",
                }
            )

            if result.get("success"):
                return result["result"].get("text", "")
            return ""
        finally:
            try:
                if tmp_path.exists():
                    tmp_path.unlink()
            except Exception:
                pass

    async def _stream_tts(self, text: str):
        """Generate TTS and stream audio chunks to the client."""
        if not text or self._closed:
            return

        # Reset cancel event
        self._tts_cancel_event.clear()

        voice_skill = self._skill_executor.get("voice")
        if not voice_skill:
            return

        # Generate full audio first (could be optimized to streaming API later)
        result = await voice_skill.execute(
            {
                "action": "speak",
                "text": text,
                "save_only": True,  # Don't auto-play on server
            }
        )

        if not result.get("success"):
            logger.warning(f"TTS failed: {result.get('error')}")
            return

        audio_path = result["result"].get("audio_path")
        if not audio_path or not os.path.exists(audio_path):
            return

        engine = result["result"].get("engine", "unknown")

        # Read and stream in chunks
        try:
            with open(audio_path, "rb") as f:
                audio_bytes = f.read()

            total_bytes = len(audio_bytes)
            self.stats.audio_seconds_spoken += total_bytes / 16000  # rough MP3 estimate

            # Announce start
            await self._send(
                {
                    "type": "voice_audio_start",
                    "format": "mp3",
                    "engine": engine,
                    "total_bytes": total_bytes,
                }
            )

            # Stream chunks
            for offset in range(0, total_bytes, TTS_CHUNK_SIZE):
                if self._tts_cancel_event.is_set():
                    logger.info("TTS streaming cancelled")
                    break
                chunk = audio_bytes[offset : offset + TTS_CHUNK_SIZE]
                await self._send(
                    {
                        "type": "voice_audio_chunk",
                        "data": base64.b64encode(chunk).decode("ascii"),
                        "is_final": offset + TTS_CHUNK_SIZE >= total_bytes,
                    }
                )
                # Small await to let the WS flush and the client process
                await asyncio.sleep(0.01)

            if not self._tts_cancel_event.is_set():
                await self._send({"type": "voice_audio_end"})

        except Exception as e:
            logger.warning(f"TTS streaming error: {e}")

    async def _cancel_tts(self):
        """Cancel ongoing TTS playback (called on barge-in)."""
        self._tts_cancel_event.set()
        await self._send({"type": "voice_audio_cancel"})

    # ── Control commands ──────────────────────────────────────

    async def set_muted(self, muted: bool):
        """Mute / unmute mic processing."""
        self._muted = bool(muted)
        if muted:
            self._reset_buffers()
            await self._set_state(State.MUTED)
        else:
            await self._set_state(State.IDLE)
        logger.info(f"VoiceSession muted={muted}")

    async def set_language(self, lang: str):
        self._language = lang
        await self._send({"type": "voice_config_changed", "language": lang})

    def _apply_sensitivity(self, sensitivity: str):
        preset = SENSITIVITY_PRESETS[sensitivity]
        self._sensitivity = sensitivity
        self._energy_threshold = preset["energy"]
        self._fallback_energy_threshold = preset["fallback"]
        self._barge_in_threshold = preset["barge"]
        self._silence_timeout_ms = preset["silence"]

    async def set_sensitivity(self, sensitivity: str):
        sensitivity = sensitivity.lower().strip()
        if sensitivity not in SENSITIVITY_PRESETS:
            await self._send(
                {
                    "type": "voice_error",
                    "message": f"Sensibilidad invalida: {sensitivity}",
                }
            )
            return
        self._apply_sensitivity(sensitivity)
        self._reset_buffers()
        await self._set_state(State.IDLE)
        await self._send(
            {
                "type": "voice_config_changed",
                "sensitivity": self._sensitivity,
                "energy_threshold": self._energy_threshold,
                "silence_timeout_ms": self._silence_timeout_ms,
            }
        )

    async def interrupt(self):
        """Cancel any ongoing TTS and return to idle (user requested)."""
        await self._cancel_tts()
        if self._processing_task and not self._processing_task.done():
            self._processing_task.cancel()
        self._reset_buffers()
        await self._set_state(State.IDLE)

    # ── State broadcast ───────────────────────────────────────

    async def _set_state(self, new_state: State):
        if self._state == new_state:
            return
        old = self._state
        self._state = new_state
        try:
            await self._send(
                {
                    "type": "voice_state",
                    "state": new_state.value,
                    "previous": old.value,
                }
            )
        except Exception as e:
            logger.debug(f"State broadcast error: {e}")

    @property
    def state(self) -> str:
        return self._state.value

    # ── Cleanup ───────────────────────────────────────────────

    async def close(self):
        """Cleanup session resources."""
        self._closed = True
        await self._cancel_tts()
        if self._processing_task and not self._processing_task.done():
            self._processing_task.cancel()
            try:
                await self._processing_task
            except (asyncio.CancelledError, Exception):
                pass
        self._reset_buffers()
        logger.info(
            f"VoiceSession closed: {self.stats.utterances_processed} utterances, "
            f"{self.stats.audio_seconds_received:.1f}s audio in, "
            f"avg {self.stats.avg_latency_ms:.0f}ms latency"
        )
