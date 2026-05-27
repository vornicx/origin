"""
AppIntegrationsSkill — Integración con aplicaciones externas.

Servicios soportados:
  spotify  → Control de Spotify (reproducir, pausar, buscar, playlist, now_playing)
  email    → Envío de emails via SMTP (Gmail, Outlook, etc)
  webhook  → Envía webhooks HTTP para integrar con cualquier servicio

Cada servicio requiere configuración previa (API keys, credenciales).
Se configuran via variables de entorno o el archivo .env de Origin.
"""

import logging
import time
import os
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from pathlib import Path
from typing import Dict, Any
from datetime import datetime

from .base_skill import BaseSkill

logger = logging.getLogger("origin.skills")
ORIGIN_ROOT = Path(__file__).resolve().parent.parent


class AppIntegrationsSkill(BaseSkill):
    """Integra Origin con Spotify, Email y webhooks."""

    def __init__(self):
        super().__init__(name="app_integrations", description="Integra con Spotify, Email y webhooks externos")
        # Spotify client (lazy init)
        self._spotify = None
        self._spotify_configured = False

        # Email config
        self._email_configured = False
        self._smtp_server = os.getenv("SMTP_SERVER", "smtp.gmail.com")
        self._smtp_port = int(os.getenv("SMTP_PORT", "587"))
        self._smtp_user = os.getenv("SMTP_USER", "")
        self._smtp_pass = os.getenv("SMTP_PASS", "")  # App password for Gmail

        self._check_configs()

    def _check_configs(self):
        """Verifica qué servicios están configurados."""
        # Spotify
        client_id = os.getenv("SPOTIPY_CLIENT_ID", "")
        client_secret = os.getenv("SPOTIPY_CLIENT_SECRET", "")
        if client_id and client_secret:
            try:
                import spotipy
                from spotipy.oauth2 import SpotifyOAuth

                scope = "user-read-playback-state user-modify-playback-state user-read-currently-playing playlist-modify-public playlist-modify-private user-library-read"  # noqa: E501
                self._spotify = spotipy.Spotify(
                    auth_manager=SpotifyOAuth(
                        client_id=client_id,
                        client_secret=client_secret,
                        redirect_uri=os.getenv("SPOTIPY_REDIRECT_URI", "http://localhost:8888/callback"),
                        scope=scope,
                        cache_path=str(ORIGIN_ROOT / "data" / ".spotify_cache"),
                    )
                )
                self._spotify_configured = True
                logger.info("Spotify integration: CONFIGURED")
            except Exception as e:
                logger.warning(f"Spotify init failed: {e}")
        else:
            logger.info("Spotify integration: NOT CONFIGURED (set SPOTIPY_CLIENT_ID/SECRET)")

        # Email
        if self._smtp_user and self._smtp_pass:
            self._email_configured = True
            logger.info(f"Email integration: CONFIGURED ({self._smtp_user})")
        else:
            logger.info("Email integration: NOT CONFIGURED (set SMTP_USER/SMTP_PASS)")

    def validate_inputs(self, inputs: Dict[str, Any]) -> tuple[bool, str]:
        action = inputs.get("action", "")
        valid_actions = (
            "spotify_play",
            "spotify_pause",
            "spotify_next",
            "spotify_prev",
            "spotify_search",
            "spotify_now_playing",
            "spotify_volume",
            "email_send",
            "webhook_send",
            "status",
        )
        if action not in valid_actions:
            return False, f"Acción inválida: '{action}'. Válidas: {valid_actions}"

        if action.startswith("spotify_") and action != "spotify_now_playing":
            if not self._spotify_configured and action != "spotify_search":
                return False, "Spotify no configurado. Configura SPOTIPY_CLIENT_ID y SPOTIPY_CLIENT_SECRET en .env"

        if action == "email_send":
            if not self._email_configured:
                return False, "Email no configurado. Configura SMTP_USER y SMTP_PASS en .env"
            if not inputs.get("to"):
                return False, "Se requiere 'to' (email destinatario)"
            if not inputs.get("subject") and not inputs.get("body"):
                return False, "Se requiere 'subject' o 'body'"

        if action == "webhook_send":
            if not inputs.get("url"):
                return False, "Se requiere 'url' para webhook"

        return True, ""

    async def execute(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        is_valid, error_msg = self.validate_inputs(inputs)
        if not is_valid:
            return {"success": False, "result": None, "error": error_msg, "execution_time": 0}

        start = time.time()
        action = inputs["action"]

        try:
            if action.startswith("spotify_"):
                result = self._handle_spotify(action, inputs)
            elif action == "email_send":
                result = self._send_email(inputs)
            elif action == "webhook_send":
                result = await self._send_webhook(inputs)
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
            logger.error(f"App integration error: {e}")
            return {
                "success": False,
                "result": None,
                "error": str(e),
                "execution_time": round(time.time() - start, 3),
            }

    # ── Spotify ──────────────────────────────────────────────

    def _handle_spotify(self, action: str, inputs: Dict[str, Any]) -> dict:
        """Maneja acciones de Spotify."""
        if not self._spotify:
            return {"error": "Spotify no configurado. Añade SPOTIPY_CLIENT_ID y SPOTIPY_CLIENT_SECRET al .env"}

        try:
            if action == "spotify_now_playing":
                return self._spotify_now_playing()
            elif action == "spotify_play":
                return self._spotify_play(inputs)
            elif action == "spotify_pause":
                self._spotify.pause_playback()
                return {"paused": True}
            elif action == "spotify_next":
                self._spotify.next_track()
                return {"skipped": True, "direction": "next"}
            elif action == "spotify_prev":
                self._spotify.previous_track()
                return {"skipped": True, "direction": "previous"}
            elif action == "spotify_search":
                return self._spotify_search(inputs)
            elif action == "spotify_volume":
                vol = max(0, min(100, int(inputs.get("volume", 50))))
                self._spotify.volume(vol)
                return {"volume_set": vol}
            else:
                return {"error": f"Unknown spotify action: {action}"}

        except Exception as e:
            error_str = str(e)
            if "NO_ACTIVE_DEVICE" in error_str or "Player command failed" in error_str:
                return {"error": "No hay dispositivo Spotify activo. Abre Spotify en algún dispositivo primero."}
            return {"error": f"Spotify error: {error_str}"}

    def _spotify_now_playing(self) -> dict:
        """Obtiene la canción que está sonando."""
        current = self._spotify.current_playback()
        if not current or not current.get("item"):
            return {"playing": False, "message": "No hay nada reproduciéndose en Spotify"}

        item = current["item"]
        return {
            "playing": current.get("is_playing", False),
            "track": item.get("name", "Unknown"),
            "artist": ", ".join(a["name"] for a in item.get("artists", [])),
            "album": item.get("album", {}).get("name", ""),
            "progress_ms": current.get("progress_ms", 0),
            "duration_ms": item.get("duration_ms", 0),
            "device": current.get("device", {}).get("name", "Unknown"),
            "volume": current.get("device", {}).get("volume_percent"),
            "shuffle": current.get("shuffle_state"),
            "repeat": current.get("repeat_state"),
        }

    def _spotify_play(self, inputs: Dict[str, Any]) -> dict:
        """Reproduce música. Puede buscar y reproducir automáticamente."""
        query = inputs.get("query", "")
        uri = inputs.get("uri", "")

        if uri:
            # Direct URI playback
            if "track" in uri:
                self._spotify.start_playback(uris=[uri])
            else:
                self._spotify.start_playback(context_uri=uri)
            return {"playing": True, "uri": uri}

        if query:
            # Search and play first result
            results = self._spotify.search(q=query, limit=1, type="track")
            tracks = results.get("tracks", {}).get("items", [])
            if tracks:
                track = tracks[0]
                self._spotify.start_playback(uris=[track["uri"]])
                return {
                    "playing": True,
                    "track": track["name"],
                    "artist": ", ".join(a["name"] for a in track.get("artists", [])),
                    "uri": track["uri"],
                }
            return {"error": f"No se encontró: '{query}'"}

        # Resume playback
        self._spotify.start_playback()
        return {"playing": True, "resumed": True}

    def _spotify_search(self, inputs: Dict[str, Any]) -> dict:
        """Busca en Spotify."""
        query = inputs.get("query", "")
        search_type = inputs.get("type", "track")
        limit = min(inputs.get("limit", 5), 10)

        if not query:
            return {"error": "Se requiere 'query' para buscar"}

        results = self._spotify.search(q=query, limit=limit, type=search_type)

        items = []
        key = f"{search_type}s"
        for item in results.get(key, {}).get("items", []):
            entry = {
                "name": item.get("name", ""),
                "uri": item.get("uri", ""),
                "type": search_type,
            }
            if search_type == "track":
                entry["artist"] = ", ".join(a["name"] for a in item.get("artists", []))
                entry["album"] = item.get("album", {}).get("name", "")
                entry["duration_ms"] = item.get("duration_ms", 0)
            elif search_type == "artist":
                entry["genres"] = item.get("genres", [])[:3]
                entry["followers"] = item.get("followers", {}).get("total", 0)
            elif search_type == "album":
                entry["artist"] = ", ".join(a["name"] for a in item.get("artists", []))
                entry["tracks_total"] = item.get("total_tracks", 0)
                entry["release_date"] = item.get("release_date", "")

            items.append(entry)

        return {
            "query": query,
            "type": search_type,
            "count": len(items),
            "results": items,
        }

    # ── Email ────────────────────────────────────────────────

    def _send_email(self, inputs: Dict[str, Any]) -> dict:
        """Envía un email via SMTP."""
        to = inputs["to"]
        subject = inputs.get("subject", "(Sin asunto)")
        body = inputs.get("body", "")
        html = inputs.get("html", False)

        try:
            msg = MIMEMultipart("alternative")
            msg["From"] = self._smtp_user
            msg["To"] = to
            msg["Subject"] = subject

            if html:
                msg.attach(MIMEText(body, "html"))
            else:
                msg.attach(MIMEText(body, "plain"))

            with smtplib.SMTP(self._smtp_server, self._smtp_port) as server:
                server.starttls()
                server.login(self._smtp_user, self._smtp_pass)
                server.send_message(msg)

            logger.info(f"Email sent to {to}: {subject}")
            return {
                "sent": True,
                "to": to,
                "subject": subject,
                "body_length": len(body),
            }

        except smtplib.SMTPAuthenticationError:
            return {
                "error": "Error de autenticación SMTP. Verifica SMTP_USER y SMTP_PASS (usa App Password para Gmail)"
            }
        except Exception as e:
            return {"error": f"Error enviando email: {str(e)}"}

    # ── Webhook ──────────────────────────────────────────────

    async def _send_webhook(self, inputs: Dict[str, Any]) -> dict:
        """Envía un webhook HTTP (POST/GET)."""
        import aiohttp

        url = inputs["url"]
        method = inputs.get("method", "POST").upper()
        payload = inputs.get("payload", {})
        headers = inputs.get("headers", {"Content-Type": "application/json"})

        try:
            async with aiohttp.ClientSession() as session:
                if method == "POST":
                    async with session.post(
                        url, json=payload, headers=headers, timeout=aiohttp.ClientTimeout(total=15)
                    ) as resp:
                        status = resp.status
                        body = await resp.text()
                elif method == "GET":
                    async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                        status = resp.status
                        body = await resp.text()
                else:
                    return {"error": f"Method not supported: {method}"}

            return {
                "sent": True,
                "url": url,
                "method": method,
                "status_code": status,
                "response_preview": body[:500],
            }

        except Exception as e:
            return {"error": f"Webhook error: {str(e)}"}

    # ── Status ───────────────────────────────────────────────

    def _get_status(self) -> dict:
        """Estado de todas las integraciones."""
        spotify_status = "configured" if self._spotify_configured else "not_configured"
        email_status = "configured" if self._email_configured else "not_configured"

        config_help = {}
        if not self._spotify_configured:
            config_help["spotify"] = "Añade SPOTIPY_CLIENT_ID, SPOTIPY_CLIENT_SECRET, SPOTIPY_REDIRECT_URI al .env"
        if not self._email_configured:
            config_help["email"] = "Añade SMTP_USER (tu email) y SMTP_PASS (app password) al .env"

        return {
            "integrations": {
                "spotify": spotify_status,
                "email": email_status,
                "webhook": "always_available",
            },
            "config_help": config_help if config_help else "All services configured",
            "smtp_server": self._smtp_server if self._email_configured else None,
        }

    def get_skill_docs(self) -> Dict[str, Any]:
        """Documentación para el LLM."""
        return {
            "description": self.description,
            "actions": [
                "spotify_play",
                "spotify_pause",
                "spotify_next",
                "spotify_prev",
                "spotify_search",
                "spotify_now_playing",
                "spotify_volume",
                "email_send",
                "webhook_send",
                "status",
            ],
            "inputs_spotify_play": {
                "action": "spotify_play",
                "query": "(opcional) buscar y reproducir, ej: 'Bohemian Rhapsody'",
                "uri": "(opcional) URI directo de Spotify",
            },
            "inputs_spotify_pause": {"action": "spotify_pause"},
            "inputs_spotify_next": {"action": "spotify_next"},
            "inputs_spotify_prev": {"action": "spotify_prev"},
            "inputs_spotify_search": {
                "action": "spotify_search",
                "query": "término de búsqueda",
                "type": "track|artist|album|playlist (default: track)",
                "limit": "número resultados (default 5, max 10)",
            },
            "inputs_spotify_now_playing": {"action": "spotify_now_playing"},
            "inputs_spotify_volume": {"action": "spotify_volume", "volume": "0-100"},
            "inputs_email_send": {
                "action": "email_send",
                "to": "email destinatario",
                "subject": "asunto",
                "body": "cuerpo del mensaje",
                "html": "true si body es HTML (default false)",
            },
            "inputs_webhook_send": {
                "action": "webhook_send",
                "url": "URL destino",
                "method": "POST|GET (default POST)",
                "payload": "dict con datos",
                "headers": "(opcional) dict headers",
            },
            "inputs_status": {"action": "status"},
            "CRITICO": "Spotify requiere SPOTIPY_CLIENT_ID/SECRET en .env. Email requiere SMTP_USER/PASS. Webhooks siempre disponibles.",  # noqa: E501
            "example_spotify_play": {
                "skill": "app_integrations",
                "inputs": {"action": "spotify_play", "query": "Bohemian Rhapsody"},
            },
            "example_spotify_search": {
                "skill": "app_integrations",
                "inputs": {"action": "spotify_search", "query": "Queen", "type": "artist"},
            },
            "example_email": {
                "skill": "app_integrations",
                "inputs": {
                    "action": "email_send",
                    "to": "user@email.com",
                    "subject": "Test",
                    "body": "Hola desde Origin",
                },
            },
            "example_webhook": {
                "skill": "app_integrations",
                "inputs": {
                    "action": "webhook_send",
                    "url": "https://hooks.example.com/endpoint",
                    "payload": {"text": "Origin alert"},
                },
            },
        }
