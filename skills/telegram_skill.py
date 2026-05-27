"""
TelegramSkill — Canal remoto de acceso a Origin vía Telegram Bot.

Usa la API de Telegram directamente via httpx (sin dependencias extra).

Acciones:
  start     → Inicia el bot (long-polling loop en background)
  stop      → Detiene el bot
  send      → Envía un mensaje al usuario autorizado
  send_audio→ Envía un archivo de audio
  status    → Estado del bot

Seguridad:
  - Solo responde al TELEGRAM_CHAT_ID autorizado.
  - Si no hay chat_id configurado, el primer /start lo registra automáticamente.
"""

import asyncio
import json
import logging
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, Optional

from .base_skill import BaseSkill
from core.injection_guard import InjectionGuard

logger = logging.getLogger("origin.skills.telegram")

# ── Config ────────────────────────────────────────────────────
DATA_DIR = Path(__file__).parent.parent / "data" / "telegram"
STATE_FILE = DATA_DIR / "telegram_state.json"

TELEGRAM_API = "https://api.telegram.org/bot{token}"
POLL_TIMEOUT = 30  # Long-polling timeout (seconds)
POLL_INTERVAL = 1  # Delay between polls on error
MAX_MESSAGE_LEN = 4096  # Telegram max message length


class TelegramSkill(BaseSkill):
    """Bot de Telegram para acceso remoto a Origin.

    Permite interactuar con Origin desde el teléfono:
    mensajes de texto, notas de voz y fotos.
    """

    def __init__(self):
        super().__init__(
            name="telegram", description="Bot de Telegram para acceso remoto a Origin. Soporta texto, voz y fotos."
        )
        self._token = os.getenv("TELEGRAM_BOT_TOKEN", "")
        self._chat_id = os.getenv("TELEGRAM_CHAT_ID", "")
        self._configured = bool(self._token)

        self._running = False
        self._task: Optional[asyncio.Task] = None
        self._offset = 0  # Telegram update offset
        self._http = None  # httpx.AsyncClient (lazy)
        self._mind = None  # Mind reference (injected)
        self._voice_skill = None  # Voice skill reference (injected)
        self._message_count = 0
        self._last_message_at: Optional[str] = None
        self._bot_info: Optional[Dict] = None

        self._guard = InjectionGuard()

        DATA_DIR.mkdir(parents=True, exist_ok=True)
        self._load_state()

        if self._configured:
            logger.info(
                "Telegram bot configured"
                + (f" — chat_id={self._chat_id}" if self._chat_id else " — awaiting first /start")
            )
        else:
            logger.info("Telegram bot not configured (set TELEGRAM_BOT_TOKEN in .env)")

    # ── Dependencies injection ─────────────────────────────────

    def set_mind(self, mind):
        """Inject Mind reference for routing messages through reasoning loop."""
        self._mind = mind

    def set_voice_skill(self, voice_skill):
        """Inject VoiceSkill for TTS on voice responses."""
        self._voice_skill = voice_skill

    # ── State persistence ──────────────────────────────────────

    def _load_state(self):
        if not STATE_FILE.exists():
            return
        try:
            data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
            self._offset = data.get("offset", 0)
            self._message_count = data.get("message_count", 0)
            # Restore chat_id if it was auto-discovered
            if not self._chat_id and data.get("chat_id"):
                self._chat_id = str(data["chat_id"])
        except Exception as e:
            logger.warning(f"Failed to load telegram state: {e}")

    def _save_state(self):
        try:
            data = {
                "offset": self._offset,
                "chat_id": self._chat_id,
                "message_count": self._message_count,
                "saved_at": datetime.now().isoformat(),
            }
            STATE_FILE.write_text(
                json.dumps(data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception as e:
            logger.debug(f"Failed to save telegram state: {e}")

    # ── HTTP client ────────────────────────────────────────────

    @property
    def _api_url(self) -> str:
        return TELEGRAM_API.format(token=self._token)

    async def _get_http(self):
        import httpx

        if self._http is None or self._http.is_closed:
            self._http = httpx.AsyncClient(timeout=POLL_TIMEOUT + 10)
        return self._http

    async def _api(self, method: str, **kwargs) -> Dict:
        """Call Telegram Bot API method."""
        http = await self._get_http()
        url = f"{self._api_url}/{method}"

        # Separate files from params
        files = kwargs.pop("_files", None)
        if files:
            resp = await http.post(url, data=kwargs, files=files)
        else:
            resp = await http.post(url, json=kwargs)

        result = resp.json()
        if not result.get("ok"):
            err = result.get("description", "Unknown Telegram API error")
            logger.warning(f"Telegram API error ({method}): {err}")
            raise Exception(err)
        return result.get("result", {})

    # ── Skill interface ────────────────────────────────────────

    def validate_inputs(self, inputs: Dict[str, Any]) -> tuple[bool, str]:
        action = inputs.get("action", "")
        valid = ("start", "stop", "send", "send_audio", "status")
        if action not in valid:
            return False, f"Accion invalida: '{action}'. Validas: {valid}"
        if action == "send" and not inputs.get("text"):
            return False, "Se requiere 'text' para send"
        if action in ("start", "send", "send_audio") and not self._configured:
            return False, "Telegram no configurado. Agrega TELEGRAM_BOT_TOKEN al .env"
        return True, ""

    async def execute(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        is_valid, error_msg = self.validate_inputs(inputs)
        if not is_valid:
            return {"success": False, "result": None, "error": error_msg, "execution_time": 0}

        start = time.time()
        action = inputs["action"]

        try:
            if action == "start":
                result = await self._start_bot()
            elif action == "stop":
                result = self._stop_bot()
            elif action == "send":
                result = await self._send_message(inputs["text"], inputs.get("parse_mode"))
            elif action == "send_audio":
                result = await self._send_audio(inputs.get("audio_path", ""))
            elif action == "status":
                result = self._get_status()
            else:
                result = {"error": f"Unknown action: {action}"}

            elapsed = round(time.time() - start, 3)
            self.execution_count += 1
            self.last_execution = datetime.now()
            success = "error" not in result
            return {"success": success, "result": result, "error": result.get("error"), "execution_time": elapsed}

        except Exception as e:
            logger.error(f"Telegram skill error: {e}")
            return {"success": False, "result": None, "error": str(e), "execution_time": round(time.time() - start, 3)}

    # ── Bot lifecycle ──────────────────────────────────────────

    async def _start_bot(self) -> dict:
        if self._running:
            return {"started": False, "reason": "already running"}

        # Verify token
        try:
            self._bot_info = await self._api("getMe")
            bot_name = self._bot_info.get("username", "unknown")
            logger.info(f"Telegram bot connected: @{bot_name}")
        except Exception as e:
            return {"error": f"Token invalido o sin conexion: {e}"}

        self._running = True
        self._task = asyncio.create_task(self._poll_loop())
        return {
            "started": True,
            "bot": f"@{bot_name}",
            "chat_id": self._chat_id or "(esperando primer /start)",
        }

    def _stop_bot(self) -> dict:
        self._running = False
        if self._task:
            self._task.cancel()
            self._task = None
        return {"stopped": True}

    # ── Polling loop ───────────────────────────────────────────

    async def _poll_loop(self):
        """Long-polling loop: fetch updates from Telegram."""
        logger.info("Telegram polling started")
        consecutive_errors = 0

        while self._running:
            try:
                updates = await self._api(
                    "getUpdates",
                    offset=self._offset,
                    timeout=POLL_TIMEOUT,
                    allowed_updates=["message"],
                )

                if updates:
                    consecutive_errors = 0
                    for update in updates:
                        update_id = update.get("update_id", 0)
                        self._offset = update_id + 1
                        try:
                            await self._handle_update(update)
                        except Exception as e:
                            logger.warning(f"Error handling update {update_id}: {e}")
                    self._save_state()

                consecutive_errors = 0

            except asyncio.CancelledError:
                break
            except Exception as e:
                consecutive_errors += 1
                delay = min(30, POLL_INTERVAL * (2 ** min(consecutive_errors, 5)))
                logger.warning(f"Telegram poll error (retry in {delay}s): {e}")
                await asyncio.sleep(delay)

        logger.info("Telegram polling stopped")

    # ── Update handling ────────────────────────────────────────

    async def _handle_update(self, update: Dict):
        """Route incoming Telegram update to the appropriate handler."""
        message = update.get("message")
        if not message:
            return

        chat_id = str(message.get("chat", {}).get("id", ""))
        text = message.get("text", "")
        voice = message.get("voice")
        photo = message.get("photo")

        # Security: auto-register first user or validate chat_id
        if not self._chat_id:
            if text == "/start":
                self._chat_id = chat_id
                self._save_state()
                logger.info(f"Telegram chat_id auto-registered: {chat_id}")
                await self._reply(
                    chat_id, "Origin conectado. Soy tu asistente personal. Envía cualquier mensaje para empezar."
                )
                return
            else:
                # Ignore messages until /start
                return

        if chat_id != self._chat_id:
            logger.warning(f"Unauthorized Telegram message from chat_id={chat_id}")
            await self._reply(chat_id, "No autorizado. Este bot es privado.")
            return

        self._message_count += 1
        self._last_message_at = datetime.now().isoformat()

        # Route by content type
        if text:
            await self._handle_text(chat_id, text)
        elif voice:
            await self._handle_voice(chat_id, voice)
        elif photo:
            caption = message.get("caption", "Describe lo que ves en esta imagen.")
            await self._handle_photo(chat_id, photo, caption)

    async def _handle_text(self, chat_id: str, text: str):
        """Handle incoming text message — route through reasoning loop."""
        # Built-in commands
        if text == "/start":
            await self._reply(chat_id, "Origin activo. Envía cualquier mensaje.")
            return

        if text == "/status":
            status = self._get_status()
            lines = ["*Origin Status*"]
            lines.append(f"Bot: running={status['running']}")
            lines.append(f"Messages: {status['message_count']}")
            lines.append(f"Mind: {'connected' if self._mind else 'disconnected'}")
            await self._reply(chat_id, "\n".join(lines), parse_mode="Markdown")
            return

        if text == "/help":
            await self._reply(
                chat_id,
                (
                    "*Comandos:*\n"
                    "/status — Estado de Origin\n"
                    "/help — Esta ayuda\n\n"
                    "Tambien puedes:\n"
                    "- Enviar texto → Origin responde\n"
                    "- Enviar nota de voz → Transcribe y responde\n"
                    "- Enviar foto → Analiza y responde"
                ),
                parse_mode="Markdown",
            )
            return

        # Route through Mind reasoning loop
        if not self._mind:
            await self._reply(chat_id, "[Origin Mind no conectada. Reinicia el servidor.]")
            return

        # SECURITY: Prompt injection check before routing to Mind
        guard_result = self._guard.analyze(text)
        if guard_result.is_blocked:
            logger.warning(f"Telegram input BLOCKED: score={guard_result.risk_score:.2f} from chat={chat_id}")
            await self._reply(chat_id, "Mensaje bloqueado por seguridad. Reformula tu mensaje.")
            return

        # Send typing indicator
        try:
            await self._api("sendChatAction", chat_id=int(chat_id), action="typing")
        except Exception:
            pass

        try:
            result = await self._mind.think(text)
            answer = result.final_answer or "Sin respuesta."
            await self._reply(chat_id, answer)
        except Exception as e:
            logger.error(f"Telegram reasoning error: {e}")
            await self._reply(chat_id, f"Error procesando: {str(e)[:200]}")

    async def _handle_voice(self, chat_id: str, voice: Dict):
        """Handle voice note: download → transcribe → reason → reply."""
        if not self._mind:
            await self._reply(chat_id, "[Mind no conectada]")
            return

        try:
            await self._api("sendChatAction", chat_id=int(chat_id), action="typing")
        except Exception:
            pass

        try:
            # Download voice file
            file_id = voice.get("file_id", "")
            file_info = await self._api("getFile", file_id=file_id)
            file_path = file_info.get("file_path", "")

            if not file_path:
                await self._reply(chat_id, "No pude descargar el audio.")
                return

            # Download the actual file
            http = await self._get_http()
            download_url = f"https://api.telegram.org/file/bot{self._token}/{file_path}"
            resp = await http.get(download_url)

            # Save to temp file
            audio_dir = Path(__file__).resolve().parent.parent / "data" / "audio"
            audio_dir.mkdir(parents=True, exist_ok=True)
            temp_path = audio_dir / f"tg_voice_{int(time.time())}.ogg"
            temp_path.write_bytes(resp.content)

            # Transcribe using voice skill
            if self._voice_skill:
                stt_result = await self._voice_skill.execute(
                    {
                        "action": "transcribe",
                        "audio_path": str(temp_path),
                        "engine": "whisper",
                    }
                )
                text = stt_result.get("result", {}).get("text", "")
            else:
                text = ""

            # Cleanup temp file
            try:
                temp_path.unlink()
            except Exception:
                pass

            if not text:
                await self._reply(chat_id, "No pude transcribir el audio. Intenta de nuevo.")
                return

            # Notify user what was heard
            await self._reply(chat_id, f"_Escuche: {text}_", parse_mode="Markdown")

            # Route through reasoning loop
            result = await self._mind.think(text)
            answer = result.final_answer or "Sin respuesta."
            await self._reply(chat_id, answer)

        except Exception as e:
            logger.error(f"Telegram voice error: {e}")
            await self._reply(chat_id, f"Error con audio: {str(e)[:200]}")

    async def _handle_photo(self, chat_id: str, photos: list, caption: str):
        """Handle photo: download → analyze with vision → reply."""
        if not self._mind:
            await self._reply(chat_id, "[Mind no conectada]")
            return

        try:
            await self._api("sendChatAction", chat_id=int(chat_id), action="typing")
        except Exception:
            pass

        try:
            # Get highest resolution photo
            best = photos[-1] if photos else None
            if not best:
                await self._reply(chat_id, "No pude procesar la foto.")
                return

            file_info = await self._api("getFile", file_id=best.get("file_id", ""))
            file_path = file_info.get("file_path", "")

            if not file_path:
                await self._reply(chat_id, "No pude descargar la foto.")
                return

            # Download photo
            http = await self._get_http()
            download_url = f"https://api.telegram.org/file/bot{self._token}/{file_path}"
            resp = await http.get(download_url)

            # Save temporarily
            import base64

            base64.b64encode(resp.content).decode()

            # Route the question + image through reasoning
            # Use a combined prompt since the vision skill needs base64
            question = caption or "Describe lo que ves en esta imagen."
            result = await self._mind.think(f"[Foto recibida por Telegram] {question}")
            answer = result.final_answer or "Sin respuesta."
            await self._reply(chat_id, answer)

        except Exception as e:
            logger.error(f"Telegram photo error: {e}")
            await self._reply(chat_id, f"Error con foto: {str(e)[:200]}")

    # ── Sending messages ───────────────────────────────────────

    async def _reply(self, chat_id: str, text: str, parse_mode: Optional[str] = None):
        """Send a text message, splitting if too long."""
        # Split long messages
        chunks = self._split_message(text)
        for chunk in chunks:
            params: Dict[str, Any] = {
                "chat_id": int(chat_id),
                "text": chunk,
            }
            if parse_mode:
                params["parse_mode"] = parse_mode

            try:
                await self._api("sendMessage", **params)
            except Exception as e:
                # If markdown parsing fails, retry without parse_mode
                if parse_mode:
                    params.pop("parse_mode")
                    try:
                        await self._api("sendMessage", **params)
                    except Exception as e2:
                        logger.error(f"Failed to send message: {e2}")
                else:
                    logger.error(f"Failed to send message: {e}")

    async def _send_message(self, text: str, parse_mode: Optional[str] = None) -> dict:
        """Public action: send message to authorized user."""
        if not self._chat_id:
            return {"error": "No chat_id configurado. Envia /start al bot primero."}

        await self._reply(self._chat_id, text, parse_mode)
        return {"sent": True, "chat_id": self._chat_id, "length": len(text)}

    async def _send_audio(self, audio_path: str) -> dict:
        """Send audio file to authorized user."""
        if not self._chat_id:
            return {"error": "No chat_id configurado."}

        path = Path(audio_path)
        if not path.exists():
            return {"error": f"Audio file not found: {audio_path}"}

        try:
            with open(path, "rb") as f:
                await self._api(
                    "sendAudio",
                    _files={"audio": (path.name, f, "audio/mpeg")},
                    chat_id=int(self._chat_id),
                )
            return {"sent": True, "file": path.name}
        except Exception as e:
            return {"error": f"Failed to send audio: {e}"}

    @staticmethod
    def _split_message(text: str) -> list:
        """Split text into chunks that fit Telegram's max message length."""
        if len(text) <= MAX_MESSAGE_LEN:
            return [text]

        chunks = []
        while text:
            if len(text) <= MAX_MESSAGE_LEN:
                chunks.append(text)
                break
            # Try to split at a newline near the limit
            split_at = text.rfind("\n", 0, MAX_MESSAGE_LEN)
            if split_at < MAX_MESSAGE_LEN // 2:
                split_at = MAX_MESSAGE_LEN
            chunks.append(text[:split_at])
            text = text[split_at:].lstrip("\n")
        return chunks

    # ── Status ─────────────────────────────────────────────────

    def _get_status(self) -> dict:
        return {
            "configured": self._configured,
            "running": self._running,
            "bot": self._bot_info.get("username") if self._bot_info else None,
            "chat_id": self._chat_id or None,
            "message_count": self._message_count,
            "last_message_at": self._last_message_at,
            "mind_connected": self._mind is not None,
            "voice_connected": self._voice_skill is not None,
        }

    def get_skill_docs(self) -> Dict[str, Any]:
        return {
            "description": self.description,
            "actions": ["start", "stop", "send", "send_audio", "status"],
            "inputs_start": {"action": "start"},
            "inputs_stop": {"action": "stop"},
            "inputs_send": {
                "action": "send",
                "text": "mensaje a enviar al usuario via Telegram",
                "parse_mode": "(opcional) Markdown|HTML",
            },
            "inputs_send_audio": {
                "action": "send_audio",
                "audio_path": "ruta al archivo de audio (.mp3)",
            },
            "inputs_status": {"action": "status"},
            "setup": (
                "1. Crea un bot con @BotFather en Telegram\n"
                "2. Agrega TELEGRAM_BOT_TOKEN al .env\n"
                "3. (Opcional) Agrega TELEGRAM_CHAT_ID al .env\n"
                "4. Si no pones chat_id, el primer /start lo registra automaticamente"
            ),
            "example_send": {
                "skill": "telegram",
                "inputs": {"action": "send", "text": "Hola desde Origin!"},
            },
        }

    # ── Cleanup ────────────────────────────────────────────────

    async def close(self):
        """Cleanup on shutdown."""
        self._stop_bot()
        if self._http and not self._http.is_closed:
            await self._http.aclose()
            self._http = None
