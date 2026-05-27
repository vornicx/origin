"""
VoiceSkill — Entrada por voz (STT) y salida por voz (TTS) para Origin.

Motor TTS premium:   ElevenLabs (multilingual v2) — máxima calidad, voces pro.
Motor TTS premium:   Fish Audio (S2-Pro) — voz clonada de alta calidad.
Motor TTS fallback:  Edge TTS — gratuito, sin API key.

Cadena de fallback: ElevenLabs → Fish Audio → Edge TTS

Acciones:
  listen      → Escucha el micrófono y devuelve texto (Speech-to-Text)
  speak       → Convierte texto a voz y lo reproduce (ElevenLabs / Fish Audio / Edge TTS)
  voices      → Lista voces disponibles
  set_voice   → Cambia la voz por defecto
  status      → Estado del sistema de voz (micrófono, voz activa, etc.)
"""

import logging
import time
import asyncio
import threading
import os
import re
from typing import Dict, Any
from datetime import datetime
from pathlib import Path

from .base_skill import BaseSkill

logger = logging.getLogger("origin.skills")

# ── Audio output directory ──────────────────────────────────
ORIGIN_ROOT = Path(__file__).resolve().parent.parent
AUDIO_DIR = ORIGIN_ROOT / "data" / "audio"

# ── ElevenLabs config ──────────────────────────────────────
ELEVENLABS_API_URL = "https://api.elevenlabs.io/v1/text-to-speech"
ELEVENLABS_MODEL = "eleven_multilingual_v2"

# Voces pre-built de ElevenLabs ideales para Origin
ELEVENLABS_VOICES = {
    "daniel": "onwK4e9ZLuTAKqWW03F9",  # British deep male — ideal Origin (Bettany-like)
    "adam": "pNInz6obpgDQGcFmaJgB",  # Deep narrator male
    "josh": "TxGEqnHWrfWFTfGW9XjX",  # Deep young male
    "arnold": "VR6AewLTigWG4xSOukaG",  # Authoritative male
    "antoni": "ErXwobaYiN019PkySvjV",  # Well-rounded male
}
# Allow override via env var (paste a voice_id from elevenlabs.io/voice-library
# if you find a closer Bettany/Origin clone)
ELEVENLABS_BETTANY_VOICE_ID = os.getenv("ELEVENLABS_BETTANY_VOICE_ID", ELEVENLABS_VOICES["daniel"])
ELEVENLABS_DEFAULT_VOICE = ELEVENLABS_BETTANY_VOICE_ID

# ── Fish Audio config ───────────────────────────────────────
FISH_AUDIO_API_URL = "https://api.fish.audio/v1/tts"
FISH_AUDIO_MODEL = "s2-pro"

# Voz principal de Origin en Fish Audio (voice clone ID)
FISH_ORIGIN_VOICE_ID = "612b878b113047d9a770c069c8b4fdfe"

# ── Voice presets ───────────────────────────────────────────
# Edge TTS = libre, sin créditos, sin API key (Microsoft cloud)
# Fish Audio = de pago (voz clonada), requiere FISH_AUDIO_API_KEY
# ElevenLabs = premium (máxima calidad), requiere ELEVENLABS_API_KEY
VOICE_PRESETS = {
    # ── ElevenLabs premium (máxima calidad, multilingual) ──
    "origin_bettany": f"eleven:{ELEVENLABS_BETTANY_VOICE_ID}",  # Paul Bettany-like Origin (env override capable)
    "origin_eleven": f"eleven:{ELEVENLABS_VOICES['daniel']}",  # Origin British deep (Daniel)
    "origin_eleven_es": f"eleven:{ELEVENLABS_VOICES['adam']}",  # Origin narrator (Adam, multilingual)
    "eleven_josh": f"eleven:{ELEVENLABS_VOICES['josh']}",  # Deep young male
    "eleven_arnold": f"eleven:{ELEVENLABS_VOICES['arnold']}",  # Authoritative
    "eleven_antoni": f"eleven:{ELEVENLABS_VOICES['antoni']}",  # Well-rounded
    # ── Origin gratis (Edge TTS, voces masculinas españolas / inglesas con prosody Origin-like) ──
    "origin": "edge:es-ES-AlvaroNeural",  # Default Origin: masculino, español Spain, voz grave
    "origin_en": "edge:en-GB-RyanNeural",  # Origin inglés británico
    "origin_deep": "edge:es-ES-AlvaroNeural",  # Misma voz, pitch aún más grave (-8Hz, -12%)
    "origin_mx": "edge:es-MX-JorgeNeural",  # Origin español mexicano (más neutro)
    # ── Fish Audio premium (voz clonada, requiere créditos) ──
    "origin_fish": FISH_ORIGIN_VOICE_ID,
    # ── Otras voces Edge TTS ──
    "british": "edge:en-GB-RyanNeural",
    "us_male": "edge:en-US-GuyNeural",
    "female_es": "edge:es-ES-ElviraNeural",
    "female_en": "edge:en-US-JennyNeural",
}

# Prosody overrides para presets que requieren tuning (Edge TTS soporta pitch + rate)
# rate: cadencia (-N% = más lento). pitch: tono (-NHz = más grave).
VOICE_PROSODY = {
    "origin": {"rate": "-5%", "pitch": "-3Hz"},  # ligeramente lento + grave
    "origin_en": {"rate": "-5%", "pitch": "-2Hz"},
    "origin_deep": {"rate": "-12%", "pitch": "-8Hz"},  # Origin hardcore mode
    "origin_mx": {"rate": "-5%", "pitch": "-2Hz"},
}

# Por defecto: Origin Bettany-like via ElevenLabs (premium si hay key, sino fallback a Edge TTS)
DEFAULT_PRESET = "origin_bettany"
DEFAULT_VOICE = ELEVENLABS_BETTANY_VOICE_ID
EDGE_FALLBACK_VOICE = "en-GB-RyanNeural"  # UK male, closer to Origin feel


class VoiceSkill(BaseSkill):
    """Entrada/salida por voz: micrófono → texto y texto → audio.

    TTS Engine priority:
      1. ElevenLabs multilingual v2 (highest quality, multilingual)
      2. Fish Audio S2-Pro (high quality, cloned voice)
      3. Edge TTS (free fallback if nothing else available)
    """

    # Shared Whisper model cache — avoids reloading the model (~500MB) on every call.
    # Key: model_size str → faster_whisper.WhisperModel instance.
    _whisper_cache: dict = {}

    def __init__(self):
        super().__init__(
            name="voice",
            description="Escucha por micrófono (STT) y habla. Motores: ElevenLabs (premium), Fish Audio (premium), Edge TTS (gratis).",  # noqa: E501
        )
        # Default = ElevenLabs Bettany-like (premium si hay key, sino Edge UK male)
        self._current_voice = DEFAULT_VOICE
        self._current_preset = DEFAULT_PRESET
        self._eleven_api_key = os.getenv("ELEVENLABS_API_KEY", "")
        self._eleven_configured = bool(self._eleven_api_key)
        self._tts_engine = "elevenlabs" if self._eleven_configured else "edge_tts"
        self._listen_timeout = 8
        self._phrase_timeout = 5
        self._is_speaking = False
        self._is_listening = False

        # ElevenLabs config — tuned for serene/composed Bettany-like delivery
        self._eleven_stability = 0.65
        self._eleven_similarity = 0.85
        self._eleven_style = 0.15
        self._eleven_speaker_boost = True

        # Fish Audio config (opcional, solo si user pide preset='origin_fish')
        self._fish_api_key = os.getenv("FISH_AUDIO_API_KEY", "")
        self._fish_configured = bool(self._fish_api_key)
        self._fish_temperature = 0.7
        self._fish_top_p = 0.7
        self._fish_speed = 1.0

        # Edge TTS config base (cada preset puede sobrescribir)
        self._edge_rate = "+0%"
        self._edge_volume = "+0%"
        self._edge_pitch = "+0Hz"

        engines = ["Edge TTS (gratis)"]
        if self._eleven_configured:
            engines.insert(0, "ElevenLabs (premium)")
        if self._fish_configured:
            engines.insert(len(engines) - 1, "Fish Audio (premium)")
        logger.info(
            f"Voice TTS disponible: {' → '.join(engines)} — " f"preset='{DEFAULT_PRESET}' voice='{DEFAULT_VOICE}'"
        )

        # Ensure audio dir
        AUDIO_DIR.mkdir(parents=True, exist_ok=True)

    def validate_inputs(self, inputs: Dict[str, Any]) -> tuple[bool, str]:
        action = inputs.get("action", "")
        valid_actions = ("listen", "transcribe", "speak", "voices", "set_voice", "status")
        if action not in valid_actions:
            return False, f"Acción inválida: '{action}'. Válidas: {valid_actions}"

        if action == "speak":
            if not inputs.get("text"):
                return False, "Se requiere 'text' para speak"

        if action == "set_voice":
            if not inputs.get("voice") and not inputs.get("preset") and not inputs.get("engine"):
                return False, "Se requiere 'voice', 'preset' o 'engine'"

        return True, ""

    async def execute(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        is_valid, error_msg = self.validate_inputs(inputs)
        if not is_valid:
            return {"success": False, "result": None, "error": error_msg, "execution_time": 0}

        start = time.time()
        action = inputs["action"]

        try:
            if action == "listen":
                result = await self._listen(inputs)
            elif action == "transcribe":
                result = await self._transcribe(inputs)
            elif action == "speak":
                result = await self._speak(inputs)
            elif action == "voices":
                result = await self._list_voices(inputs)
            elif action == "set_voice":
                result = self._set_voice(inputs)
            elif action == "status":
                result = self._get_status()
            else:
                result = {"error": f"Unknown action: {action}"}

            elapsed = round(time.time() - start, 3)
            self.execution_count += 1
            self.last_execution = datetime.now()

            success = "error" not in result
            return {
                "success": success,
                "result": result,
                "error": result.get("error"),
                "execution_time": elapsed,
            }

        except Exception as e:
            logger.error(f"Voice skill error: {e}")
            return {
                "success": False,
                "result": None,
                "error": str(e),
                "execution_time": round(time.time() - start, 3),
            }

    # ── STT: Listen ──────────────────────────────────────────

    async def _listen(self, inputs: Dict[str, Any]) -> dict:
        """Escucha el micrófono y retorna texto reconocido."""
        import speech_recognition as sr

        timeout = inputs.get("timeout", self._listen_timeout)
        phrase_timeout = inputs.get("phrase_timeout", self._phrase_timeout)
        language = inputs.get("language", "es-ES")

        self._is_listening = True
        recognizer = sr.Recognizer()

        # Adjust for ambient noise
        recognizer.dynamic_energy_threshold = True
        recognizer.energy_threshold = 300

        try:
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(
                None, self._capture_audio, recognizer, timeout, phrase_timeout, language
            )
            return result

        except Exception as e:
            logger.error(f"Listen error: {e}")
            return {"error": f"Error escuchando: {str(e)}"}
        finally:
            self._is_listening = False

    def _capture_audio(self, recognizer, timeout: int, phrase_timeout: int, language: str) -> dict:
        """Captura audio del micrófono (blocking, runs in executor)."""
        import speech_recognition as sr

        try:
            with sr.Microphone() as source:
                logger.info("Adjusting for ambient noise...")
                recognizer.adjust_for_ambient_noise(source, duration=0.5)

                logger.info(f"Listening... (timeout={timeout}s, lang={language})")
                audio = recognizer.listen(source, timeout=timeout, phrase_time_limit=phrase_timeout)

                logger.info("Processing speech...")

                # Try Google Speech Recognition (fast, free)
                try:
                    text = recognizer.recognize_google(audio, language=language)
                    logger.info(f"Recognized: {text}")
                    return {
                        "text": text,
                        "language": language,
                        "engine": "google",
                        "confidence": "high",
                    }
                except sr.UnknownValueError:
                    return {
                        "text": "",
                        "error": "No se entendió el audio",
                        "hint": "Habla más claro o acércate al micrófono",
                    }
                except sr.RequestError as e:
                    logger.warning(f"Google STT failed: {e}, trying offline...")
                    try:
                        text = recognizer.recognize_sphinx(audio)
                        return {
                            "text": text,
                            "language": "en-US",
                            "engine": "sphinx_offline",
                            "confidence": "low",
                        }
                    except Exception:
                        return {"error": f"STT no disponible: {str(e)}"}

        except sr.WaitTimeoutError:
            return {
                "text": "",
                "error": "Timeout: no se detectó voz",
                "hint": "Intenta hablar antes de que expire el timeout",
            }
        except OSError as e:
            return {"error": f"Micrófono no disponible: {str(e)}", "hint": "Verifica que el micrófono esté conectado"}

    # ── STT: Transcribe (file-based, for API) ────────────────

    async def _transcribe(self, inputs: Dict[str, Any]) -> dict:
        """Transcribe un archivo de audio a texto usando faster-whisper o fallback speech_recognition."""
        audio_path = inputs.get("audio_path", "")
        if not audio_path or not os.path.isfile(audio_path):
            return {"error": "audio_path inválido o no encontrado"}

        engine = inputs.get("engine", "whisper")

        # Try faster-whisper first (local, accurate)
        if engine == "whisper":
            try:
                import faster_whisper

                model_size = inputs.get("model_size", "base")
                language = inputs.get("language", None)

                def _load_and_transcribe():
                    if model_size not in VoiceSkill._whisper_cache:
                        logger.info(f"Loading Whisper model '{model_size}' (first use)...")
                        VoiceSkill._whisper_cache[model_size] = faster_whisper.WhisperModel(
                            model_size, device="cpu", compute_type="int8"
                        )
                    m = VoiceSkill._whisper_cache[model_size]
                    segs, meta = m.transcribe(audio_path, language=language)
                    return " ".join(s.text for s in segs), meta

                loop = asyncio.get_event_loop()
                text, info = await loop.run_in_executor(None, _load_and_transcribe)
                if text.strip():
                    return {
                        "text": text.strip(),
                        "engine": "faster_whisper",
                        "language": info.language if info else "unknown",
                        "confidence": "high"
                        if (info and info.language_probability and info.language_probability > 0.8)
                        else "medium",
                    }
            except ImportError:
                logger.warning("faster-whisper not installed, falling back to speech_recognition")
            except Exception as e:
                logger.warning(f"faster-whisper failed: {e}, trying fallback")

        # Fallback: speech_recognition
        try:
            import speech_recognition as sr

            recognizer = sr.Recognizer()
            with sr.AudioFile(audio_path) as source:
                audio = recognizer.record(source)
                text = recognizer.recognize_google(audio, language=inputs.get("language", "es-ES"))
                return {
                    "text": text,
                    "engine": "google_stt",
                    "language": inputs.get("language", "es-ES"),
                    "confidence": "medium",
                }
        except sr.UnknownValueError:
            return {"text": "", "error": "No se entendió el audio"}
        except Exception as e:
            return {"error": f"STT falló: {str(e)}"}

    # ══════════════════════════════════════════════════════════
    # TTS: Speak — Fish Audio (primary) + Edge TTS (fallback)
    # ══════════════════════════════════════════════════════════

    async def _speak(self, inputs: Dict[str, Any]) -> dict:
        """Convierte texto a voz. Fallback chain: ElevenLabs → Fish Audio → Edge TTS."""
        text = inputs["text"]
        save_only = inputs.get("save_only", False)

        # Determine which engine and voice to use
        voice = inputs.get("voice", self._current_voice)
        engine = inputs.get("engine", self._tts_engine)
        preset_name = inputs.get("preset") or self._current_preset

        # Apply preset
        prosody: Dict[str, str] = {}
        if preset_name and preset_name in VOICE_PRESETS:
            voice = VOICE_PRESETS[preset_name]
            # Pick up any prosody tuning for this preset (Origin deeper/slower)
            prosody = VOICE_PROSODY.get(preset_name, {})

        # Detect engine from voice string prefix
        if isinstance(voice, str) and voice.startswith("eleven:"):
            engine = "elevenlabs"
            voice = voice[7:]  # Strip "eleven:" prefix
        elif isinstance(voice, str) and voice.startswith("edge:"):
            engine = "edge_tts"
            voice = voice[5:]  # Strip "edge:" prefix
        elif engine == "elevenlabs" and not self._eleven_configured:
            logger.warning("ElevenLabs not configured, trying Fish Audio...")
            if self._fish_configured:
                engine = "fish_audio"
                voice = FISH_ORIGIN_VOICE_ID
            else:
                engine = "edge_tts"
                voice = EDGE_FALLBACK_VOICE
        elif engine == "fish_audio" and not self._fish_configured:
            engine = "edge_tts"
            voice = EDGE_FALLBACK_VOICE
            logger.warning("Fish Audio not configured, falling back to Edge TTS")

        # Merge prosody into inputs (caller's explicit rate/pitch take precedence)
        effective_inputs = {**inputs}
        for k, v in prosody.items():
            effective_inputs.setdefault(k, v)
        inputs = effective_inputs

        # Generate filename
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        audio_path = AUDIO_DIR / f"tts_{timestamp}.mp3"

        self._is_speaking = True
        try:
            if engine == "elevenlabs":
                result = await self._speak_elevenlabs(text, voice, audio_path, inputs)
            elif engine == "fish_audio":
                result = await self._speak_fish_audio(text, voice, audio_path, inputs)
            else:
                result = await self._speak_edge_tts(text, voice, audio_path, inputs)

            # If TTS succeeded, handle playback
            if result.get("spoken") and not save_only:
                self._play_audio(str(audio_path))

            # Cleanup old audio files (keep last 10)
            self._cleanup_audio()

            return result

        except Exception as e:
            logger.error(f"TTS error ({engine}): {e}")
            # Cascaded fallback: ElevenLabs → Fish Audio → Edge TTS
            fallback_chain = []
            if engine == "elevenlabs":
                if self._fish_configured:
                    fallback_chain.append(("fish_audio", FISH_ORIGIN_VOICE_ID))
                fallback_chain.append(("edge_tts", EDGE_FALLBACK_VOICE))
            elif engine == "fish_audio":
                fallback_chain.append(("edge_tts", EDGE_FALLBACK_VOICE))

            original_error = str(e)
            for fb_engine, fb_voice in fallback_chain:
                logger.info(f"{engine} failed, trying {fb_engine} fallback...")
                try:
                    if fb_engine == "fish_audio":
                        result = await self._speak_fish_audio(text, fb_voice, audio_path, inputs)
                    else:
                        result = await self._speak_edge_tts(text, fb_voice, audio_path, inputs)
                    if result.get("spoken") and not save_only:
                        self._play_audio(str(audio_path))
                    result["fallback"] = True
                    result["fallback_from"] = engine
                    result["fallback_reason"] = original_error
                    return result
                except Exception as fb_err:
                    logger.warning(f"Fallback {fb_engine} also failed: {fb_err}")
                    original_error += f" | {fb_engine}: {fb_err}"

            return {"error": f"Todos los motores TTS fallaron: {original_error}"}
        finally:
            self._is_speaking = False

    # ── ElevenLabs TTS (premium) ──────────────────────────────

    async def _speak_elevenlabs(self, text: str, voice_id: str, audio_path: Path, inputs: Dict[str, Any]) -> dict:
        """Genera audio via ElevenLabs multilingual v2 API."""
        import httpx

        stability = inputs.get("stability", self._eleven_stability)
        similarity = inputs.get("similarity_boost", self._eleven_similarity)
        style = inputs.get("style", self._eleven_style)
        model = inputs.get("model_id", ELEVENLABS_MODEL)

        payload = {
            "text": text,
            "model_id": model,
            "voice_settings": {
                "stability": stability,
                "similarity_boost": similarity,
                "style": style,
                "use_speaker_boost": self._eleven_speaker_boost,
            },
        }

        headers = {
            "xi-api-key": self._eleven_api_key,
            "Content-Type": "application/json",
            "Accept": "audio/mpeg",
        }

        url = f"{ELEVENLABS_API_URL}/{voice_id}"
        logger.info(f"ElevenLabs TTS: text={len(text)} chars, voice={voice_id[:12]}..., model={model}")

        async with httpx.AsyncClient(timeout=60) as client:
            response = await client.post(url, json=payload, headers=headers)

            if response.status_code == 401:
                raise Exception("ElevenLabs: API key inválida. Verifica ELEVENLABS_API_KEY en .env")
            if response.status_code == 422:
                raise Exception(f"ElevenLabs: error de validación — {response.text[:200]}")
            if response.status_code == 429:
                raise Exception("ElevenLabs: rate limit / cuota excedida")

            response.raise_for_status()

            with open(audio_path, "wb") as f:
                f.write(response.content)

        file_size_kb = round(audio_path.stat().st_size / 1024, 1)
        logger.info(f"ElevenLabs TTS generated: {audio_path} ({file_size_kb} KB)")

        return {
            "spoken": True,
            "text_length": len(text),
            "voice": voice_id,
            "engine": "elevenlabs",
            "model": model,
            "audio_path": str(audio_path),
            "file_size_kb": file_size_kb,
            "stability": stability,
            "similarity_boost": similarity,
        }

    # ── Fish Audio TTS ──────────────────────────────────────

    async def _speak_fish_audio(self, text: str, voice_id: str, audio_path: Path, inputs: Dict[str, Any]) -> dict:
        """Genera audio via Fish Audio S2-Pro API."""
        import httpx

        temperature = inputs.get("temperature", self._fish_temperature)
        top_p = inputs.get("top_p", self._fish_top_p)
        speed = inputs.get("speed", self._fish_speed)

        # Build request payload
        payload = {
            "text": text,
            "reference_id": voice_id,
            "temperature": temperature,
            "top_p": top_p,
            "format": "mp3",
            "mp3_bitrate": 128,
            "chunk_length": 300,
            "normalize": True,
            "latency": "normal",
        }

        # Add prosody if speed != 1.0
        if speed != 1.0:
            payload["prosody"] = {
                "speed": max(0.5, min(2.0, speed)),
            }

        headers = {
            "Authorization": f"Bearer {self._fish_api_key}",
            "Content-Type": "application/json",
            "model": FISH_AUDIO_MODEL,
        }

        logger.info(f"Fish Audio TTS: text={len(text)} chars, voice={voice_id[:12]}...")

        async with httpx.AsyncClient(timeout=60) as client:
            response = await client.post(
                FISH_AUDIO_API_URL,
                json=payload,
                headers=headers,
            )

            if response.status_code == 401:
                return {"error": "Fish Audio: API key inválida. Verifica FISH_AUDIO_API_KEY en .env"}
            if response.status_code == 402:
                return {"error": "Fish Audio: créditos agotados. Recarga en fish.audio"}
            if response.status_code == 422:
                return {"error": f"Fish Audio: error de validación — {response.text[:200]}"}

            response.raise_for_status()

            # Write streamed audio to file
            with open(audio_path, "wb") as f:
                f.write(response.content)

        file_size_kb = round(audio_path.stat().st_size / 1024, 1)
        logger.info(f"Fish Audio TTS generated: {audio_path} ({file_size_kb} KB)")

        return {
            "spoken": True,
            "text_length": len(text),
            "voice": voice_id,
            "engine": "fish_audio",
            "model": FISH_AUDIO_MODEL,
            "audio_path": str(audio_path),
            "file_size_kb": file_size_kb,
            "temperature": temperature,
            "speed": speed,
        }

    # ── Edge TTS (fallback) ─────────────────────────────────

    async def _speak_edge_tts(self, text: str, voice: str, audio_path: Path, inputs: Dict[str, Any]) -> dict:
        """Genera audio via Edge TTS (gratuito, sin API key, soporta pitch + rate)."""
        import edge_tts

        rate = inputs.get("rate", self._edge_rate)
        volume = inputs.get("volume", self._edge_volume)
        pitch = inputs.get("pitch", self._edge_pitch)

        communicate = edge_tts.Communicate(
            text=text,
            voice=voice,
            rate=rate,
            volume=volume,
            pitch=pitch,
        )
        await communicate.save(str(audio_path))

        file_size_kb = round(audio_path.stat().st_size / 1024, 1)
        logger.info(f"Edge TTS generated: voice={voice} rate={rate} pitch={pitch} ({file_size_kb} KB)")

        return {
            "spoken": True,
            "text_length": len(text),
            "rate": rate,
            "pitch": pitch,
            "voice": voice,
            "engine": "edge_tts",
            "audio_path": str(audio_path),
            "file_size_kb": file_size_kb,
        }

    # ── Audio playback ──────────────────────────────────────

    @staticmethod
    def _validate_audio_path(path: str) -> bool:
        """Validates an audio file path is safe for playback.
        Prevents path injection and ensures file is within expected directory."""
        abs_path = os.path.abspath(path)
        audio_dir = os.path.abspath(str(AUDIO_DIR))
        if not abs_path.startswith(audio_dir):
            logger.warning(f"Audio path outside allowed directory: {abs_path}")
            return False
        if not abs_path.endswith(".mp3") or not os.path.isfile(abs_path):
            return False
        basename = os.path.basename(abs_path)
        if not re.match(r"^tts_\d{8}_\d{6}\.mp3$", basename):
            logger.warning(f"Audio filename doesn't match expected pattern: {basename}")
            return False
        return True

    def _play_audio(self, path: str):
        """Reproduce audio MP3 usando winsound (no shell)."""
        if not self._validate_audio_path(path):
            logger.error(f"Refused to play invalid audio path: {path}")
            return

        def _play():
            try:
                import winsound

                safe_path = os.path.abspath(path)
                winsound.PlaySound(safe_path, winsound.SND_FILENAME | winsound.SND_ASYNC)
            except Exception as e:
                logger.warning(f"Audio playback via winsound failed: {e}")
                try:
                    from win32api import ShellExecute

                    ShellExecute(0, "open", safe_path, None, "", 0)
                except Exception as e2:
                    logger.error(f"All audio playback methods failed: {e2}")

        thread = threading.Thread(target=_play, daemon=True)
        thread.start()

    def _cleanup_audio(self, keep: int = 10):
        """Elimina archivos de audio antiguos."""
        try:
            files = sorted(AUDIO_DIR.glob("tts_*.mp3"), key=lambda f: f.stat().st_mtime)
            if len(files) > keep:
                for f in files[:-keep]:
                    f.unlink()
                    logger.debug(f"Cleaned audio: {f.name}")
        except Exception as e:
            logger.debug(f"Audio cleanup error: {e}")

    # ── Voice List ───────────────────────────────────────────

    async def _list_voices(self, inputs: Dict[str, Any]) -> dict:
        """Lista voces disponibles (presets + ElevenLabs + Edge TTS filtradas)."""
        result = {
            "current_voice": self._current_voice,
            "current_engine": self._tts_engine,
            "elevenlabs_configured": self._eleven_configured,
            "elevenlabs_voices": ELEVENLABS_VOICES if self._eleven_configured else {},
            "fish_audio_configured": self._fish_configured,
            "presets": VOICE_PRESETS,
        }

        # Optionally list Edge TTS voices
        if inputs.get("include_edge", False):
            try:
                import edge_tts

                language = inputs.get("language", "es")
                voices = await edge_tts.list_voices()
                filtered = [
                    {
                        "name": v["ShortName"],
                        "gender": v["Gender"],
                        "locale": v["Locale"],
                        "engine": "edge_tts",
                    }
                    for v in voices
                    if v["Locale"].startswith(language)
                ]
                result["edge_tts_voices"] = filtered[:30]
                result["edge_tts_total"] = len(voices)
            except Exception as e:
                result["edge_tts_error"] = str(e)

        return result

    # ── Set Voice ────────────────────────────────────────────

    def _set_voice(self, inputs: Dict[str, Any]) -> dict:
        """Cambia la voz o el motor TTS por defecto."""
        old_voice = self._current_voice
        old_engine = self._tts_engine

        # Change TTS engine
        if inputs.get("engine"):
            engine = inputs["engine"]
            if engine == "elevenlabs":
                if not self._eleven_configured:
                    return {"error": "ElevenLabs no configurado. Añade ELEVENLABS_API_KEY al .env"}
                self._tts_engine = "elevenlabs"
                self._current_voice = ELEVENLABS_DEFAULT_VOICE
            elif engine == "fish_audio":
                if not self._fish_configured:
                    return {"error": "Fish Audio no configurado. Añade FISH_AUDIO_API_KEY al .env"}
                self._tts_engine = "fish_audio"
                self._current_voice = FISH_ORIGIN_VOICE_ID
            elif engine == "edge_tts":
                self._tts_engine = "edge_tts"
                self._current_voice = EDGE_FALLBACK_VOICE
            else:
                return {"error": f"Motor desconocido: '{engine}'. Válidos: elevenlabs, fish_audio, edge_tts"}
            return {
                "changed": True,
                "engine": {"from": old_engine, "to": self._tts_engine},
                "voice": {"from": old_voice, "to": self._current_voice},
            }

        # Change voice preset
        if inputs.get("preset"):
            preset = inputs["preset"]
            if preset in VOICE_PRESETS:
                voice = VOICE_PRESETS[preset]
                # Detect engine from voice prefix
                if isinstance(voice, str) and voice.startswith("eleven:"):
                    self._tts_engine = "elevenlabs" if self._eleven_configured else "edge_tts"
                    self._current_voice = voice[7:] if self._eleven_configured else EDGE_FALLBACK_VOICE
                elif isinstance(voice, str) and voice.startswith("edge:"):
                    self._tts_engine = "edge_tts"
                    self._current_voice = voice[5:]
                else:
                    self._tts_engine = "fish_audio" if self._fish_configured else "edge_tts"
                    self._current_voice = voice if self._fish_configured else EDGE_FALLBACK_VOICE
                self._current_preset = preset  # persist prosody association
                return {
                    "changed": True,
                    "preset": preset,
                    "engine": self._tts_engine,
                    "from": old_voice,
                    "to": self._current_voice,
                    "prosody": VOICE_PROSODY.get(preset, {}),
                }
            return {"error": f"Preset desconocido: '{preset}'. Válidos: {list(VOICE_PRESETS.keys())}"}

        # Change voice directly (ElevenLabs voice_id, Fish Audio reference_id, or Edge TTS voice name)
        voice = inputs.get("voice", "")
        if voice:
            if voice.startswith("eleven:"):
                self._tts_engine = "elevenlabs" if self._eleven_configured else "edge_tts"
                self._current_voice = voice[7:] if self._eleven_configured else EDGE_FALLBACK_VOICE
            elif voice.startswith("edge:"):
                self._tts_engine = "edge_tts"
                self._current_voice = voice[5:]
            else:
                self._tts_engine = "fish_audio" if self._fish_configured else "edge_tts"
                self._current_voice = voice
            return {"changed": True, "from": old_voice, "to": self._current_voice, "engine": self._tts_engine}

        return {"error": "Especifica 'voice', 'preset' o 'engine'"}

    # ── Status ───────────────────────────────────────────────

    def _get_status(self) -> dict:
        """Estado del sistema de voz."""
        mic_available = False
        try:
            import speech_recognition as sr

            mics = sr.Microphone.list_microphone_names()
            mic_available = len(mics) > 0
            mic_names = mics[:5]
        except Exception:
            mic_names = []

        return {
            "tts_engine": self._tts_engine,
            "current_voice": self._current_voice,
            "fallback_chain": "ElevenLabs → Fish Audio → Edge TTS",
            "elevenlabs": {
                "configured": self._eleven_configured,
                "model": ELEVENLABS_MODEL,
                "default_voice": ELEVENLABS_DEFAULT_VOICE,
                "voices": list(ELEVENLABS_VOICES.keys()),
                "stability": self._eleven_stability,
                "similarity_boost": self._eleven_similarity,
            },
            "fish_audio": {
                "configured": self._fish_configured,
                "model": FISH_AUDIO_MODEL,
                "voice_id": FISH_ORIGIN_VOICE_ID,
                "temperature": self._fish_temperature,
                "speed": self._fish_speed,
            },
            "edge_tts": {
                "fallback_voice": EDGE_FALLBACK_VOICE,
                "rate": self._edge_rate,
                "volume": self._edge_volume,
            },
            "presets": VOICE_PRESETS,
            "is_speaking": self._is_speaking,
            "is_listening": self._is_listening,
            "microphone_available": mic_available,
            "microphones": mic_names,
            "audio_dir": str(AUDIO_DIR),
            "audio_files": len(list(AUDIO_DIR.glob("tts_*.mp3"))),
        }

    def get_skill_docs(self) -> Dict[str, Any]:
        """Documentación para el LLM."""
        return {
            "description": self.description,
            "actions": ["listen", "transcribe", "speak", "voices", "set_voice", "status"],
            "tts_engines": "ElevenLabs (premium) → Fish Audio (premium) → Edge TTS (gratis)",
            "inputs_listen": {
                "action": "listen",
                "timeout": "segundos max esperando voz (default 8)",
                "language": "código idioma (default es-ES, también: en-US, fr-FR, etc)",
            },
            "inputs_speak": {
                "action": "speak",
                "text": "texto a convertir en voz",
                "preset": "(opcional) origin_eleven|origin_eleven_es|origin|origin_en|origin_fish|british|female_es|female_en",  # noqa: E501
                "engine": "(opcional) elevenlabs|fish_audio|edge_tts — override del motor TTS",
                "stability": "(opcional, ElevenLabs) 0-1, default 0.5 — más alto = más consistente",
                "similarity_boost": "(opcional, ElevenLabs) 0-1, default 0.75 — más alto = más parecido a voz original",
                "speed": "(opcional, Fish Audio) 0.5-2.0, default 1.0",
                "temperature": "(opcional, Fish Audio) 0-1, default 0.7",
                "rate": "(opcional, Edge TTS) velocidad: +10%, -5%, etc",
                "save_only": "(opcional) true para solo guardar sin reproducir",
            },
            "inputs_voices": {
                "action": "voices",
                "include_edge": "true para incluir lista Edge TTS",
            },
            "inputs_set_voice": {
                "action": "set_voice",
                "preset": "origin_eleven|origin|origin_en|origin_fish|british|female_es|female_en",
                "engine": "elevenlabs|fish_audio|edge_tts — cambiar motor TTS",
                "voice": "(alternativa) 'eleven:voice_id', Fish Audio reference_id, o 'edge:voice_name'",
            },
            "inputs_status": {
                "action": "status",
            },
            "CRITICO": (
                "Para 'speak' siempre pasar 'text'. "
                "ElevenLabs es máxima calidad (preset 'origin_eleven'). "
                "Edge TTS es el default (gratis). "
                "Fallback automático: ElevenLabs → Fish Audio → Edge TTS."
            ),
            "example_speak": {
                "skill": "voice",
                "inputs": {"action": "speak", "text": "Hola Vadim, ¿en qué puedo ayudarte?"},
            },
            "example_speak_eleven": {
                "skill": "voice",
                "inputs": {"action": "speak", "text": "Hello sir, at your service.", "preset": "origin_eleven"},
            },
            "example_speak_fast": {
                "skill": "voice",
                "inputs": {"action": "speak", "text": "Procesando...", "speed": 1.2},
            },
            "example_listen": {"skill": "voice", "inputs": {"action": "listen", "language": "es-ES"}},
            "example_set_voice": {"skill": "voice", "inputs": {"action": "set_voice", "preset": "origin_eleven"}},
            "example_switch_engine": {"skill": "voice", "inputs": {"action": "set_voice", "engine": "elevenlabs"}},
        }
