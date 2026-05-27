"""
ServiceHub — Registry centralizado de servicios externos.

Inspirado en el "OAuth de un click" de OpenHuman. Detecta automáticamente
qué servicios están disponibles en el entorno, lleva su estado de conexión
y salud, y expone una API uniforme para conectarlos/probarlos.

Categorías de servicios:
  llm        - Proveedores de modelos (DeepSeek, Groq, Gemini, NVIDIA, OpenCode, Ollama)
  tts        - Text-to-speech (ElevenLabs, Fish Audio, Edge TTS)
  messaging  - Telegram, Slack, Discord
  productivity - Email, GitHub, Notion, Google Drive
  media      - Spotify

API:
  hub.discover()                    # auto-descubre desde env + skills
  hub.list()                        # lista todos
  hub.get(name)                     # detalle
  await hub.ping(name)              # health check
  await hub.ping_all()              # health check de todos
  await hub.connect(name, **opts)   # conecta (OAuth si aplica)
  hub.disconnect(name)              # desconecta
  hub.set_permissions(name, perms)  # actualiza permisos

Persistencia: data/services/services.json
"""

import asyncio
import json
import logging
import os
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Awaitable

logger = logging.getLogger("origin.service_hub")

# ── Config ────────────────────────────────────────────────────
DATA_DIR = Path(__file__).parent.parent / "data" / "services"
STATE_FILE = DATA_DIR / "services.json"

# Estado posible: "connected" (configurado + ping OK), "configured" (env vars OK pero sin ping),
#                 "available" (soportado pero sin credenciales), "error" (fallo en ping/conexion).


@dataclass
class Service:
    """Un servicio externo registrado en el hub."""

    name: str
    category: str  # llm | tts | messaging | productivity | media
    display_name: str
    status: str = "available"  # connected | configured | available | error
    connected: bool = False
    description: str = ""
    required_env: List[str] = field(default_factory=list)
    optional_env: List[str] = field(default_factory=list)
    permissions: List[str] = field(default_factory=list)
    last_sync: Optional[str] = None
    last_error: Optional[str] = None
    last_ping_ms: Optional[float] = None
    docs_url: str = ""
    oauth_url: str = ""  # URL para iniciar OAuth (si aplica)
    skill_name: Optional[str] = None  # Skill asociada (si aplica)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# ── Service definitions ──────────────────────────────────────
# Catálogo de servicios soportados. Definidos declarativamente.

_SERVICE_CATALOG: List[Dict[str, Any]] = [
    # ── LLM Providers ─────────────────────────────────────────
    {
        "name": "deepseek",
        "category": "llm",
        "display_name": "DeepSeek",
        "description": "DeepSeek Chat — modelo principal para razonamiento general",
        "required_env": ["DEEPSEEK_API_KEY"],
        "docs_url": "https://platform.deepseek.com",
    },
    {
        "name": "groq",
        "category": "llm",
        "display_name": "Groq",
        "description": "Groq — modelos rápidos para tareas con baja latencia",
        "required_env": ["GROQ_API_KEY"],
        "docs_url": "https://console.groq.com",
    },
    {
        "name": "gemini",
        "category": "llm",
        "display_name": "Google Gemini",
        "description": "Gemini — modelos de Google con vision y multimodal",
        "required_env": ["GEMINI_API_KEY"],
        "docs_url": "https://aistudio.google.com",
    },
    {
        "name": "nvidia_nim",
        "category": "llm",
        "display_name": "NVIDIA NIM",
        "description": "NVIDIA NIM — modelos open-source acelerados",
        "required_env": ["NVIDIA_NIM_API_KEY"],
        "docs_url": "https://build.nvidia.com",
    },
    {
        "name": "opencode",
        "category": "llm",
        "display_name": "OpenCode",
        "description": "OpenCode — modelos especializados en codigo",
        "required_env": ["OPENCODE_API_KEY"],
        "docs_url": "https://opencode.ai",
    },
    {
        "name": "ollama",
        "category": "llm",
        "display_name": "Ollama (Local)",
        "description": "Ollama — modelos locales (gratis, offline)",
        "required_env": [],
        "optional_env": ["OLLAMA_BASE_URL"],
        "docs_url": "https://ollama.com",
    },
    # ── TTS Providers ─────────────────────────────────────────
    {
        "name": "elevenlabs",
        "category": "tts",
        "display_name": "ElevenLabs",
        "description": "ElevenLabs TTS — voz Origin premium (multilingue, alta calidad)",
        "required_env": ["ELEVENLABS_API_KEY"],
        "docs_url": "https://elevenlabs.io",
        "skill_name": "voice",
    },
    {
        "name": "fish_audio",
        "category": "tts",
        "display_name": "Fish Audio",
        "description": "Fish Audio TTS — clonacion de voz (fallback)",
        "required_env": ["FISH_AUDIO_API_KEY"],
        "docs_url": "https://fish.audio",
        "skill_name": "voice",
    },
    {
        "name": "edge_tts",
        "category": "tts",
        "display_name": "Edge TTS (Local)",
        "description": "Microsoft Edge TTS — TTS local gratis (ultimo fallback)",
        "required_env": [],
        "skill_name": "voice",
    },
    # ── Messaging ─────────────────────────────────────────────
    {
        "name": "telegram",
        "category": "messaging",
        "display_name": "Telegram",
        "description": "Bot de Telegram — acceso remoto a Origin (texto, voz, fotos)",
        "required_env": ["TELEGRAM_BOT_TOKEN"],
        "optional_env": ["TELEGRAM_CHAT_ID"],
        "docs_url": "https://core.telegram.org/bots",
        "skill_name": "telegram",
    },
    {
        "name": "slack",
        "category": "messaging",
        "display_name": "Slack",
        "description": "Slack — mensajes a canales/usuarios (no conectado aun)",
        "required_env": ["SLACK_BOT_TOKEN"],
        "docs_url": "https://api.slack.com/apps",
    },
    {
        "name": "discord",
        "category": "messaging",
        "display_name": "Discord",
        "description": "Discord — bot con webhooks/mensajes (no conectado aun)",
        "required_env": ["DISCORD_BOT_TOKEN"],
        "docs_url": "https://discord.com/developers/applications",
    },
    # ── Productivity ──────────────────────────────────────────
    {
        "name": "smtp_email",
        "category": "productivity",
        "display_name": "Email (SMTP)",
        "description": "Envio de emails via SMTP (Gmail, Outlook)",
        "required_env": ["SMTP_USER", "SMTP_PASS"],
        "optional_env": ["SMTP_SERVER", "SMTP_PORT"],
        "docs_url": "https://support.google.com/accounts/answer/185833",
        "skill_name": "app_integrations",
    },
    {
        "name": "github",
        "category": "productivity",
        "display_name": "GitHub",
        "description": "GitHub — repos, issues, PRs (no conectado aun)",
        "required_env": ["GITHUB_TOKEN"],
        "docs_url": "https://github.com/settings/tokens",
    },
    {
        "name": "notion",
        "category": "productivity",
        "display_name": "Notion",
        "description": "Notion — paginas y bases de datos (no conectado aun)",
        "required_env": ["NOTION_API_KEY"],
        "docs_url": "https://developers.notion.com",
    },
    {
        "name": "google_drive",
        "category": "productivity",
        "display_name": "Google Drive",
        "description": "Google Drive — archivos y documentos (no conectado aun)",
        "required_env": ["GOOGLE_DRIVE_CREDENTIALS"],
        "docs_url": "https://developers.google.com/drive",
    },
    {
        "name": "google_calendar",
        "category": "productivity",
        "display_name": "Google Calendar",
        "description": "Google Calendar — eventos y agenda (no conectado aun)",
        "required_env": ["GOOGLE_CALENDAR_CREDENTIALS"],
        "docs_url": "https://developers.google.com/calendar",
    },
    # ── Media ─────────────────────────────────────────────────
    {
        "name": "spotify",
        "category": "media",
        "display_name": "Spotify",
        "description": "Spotify — control de reproduccion, playlists, busqueda",
        "required_env": ["SPOTIPY_CLIENT_ID", "SPOTIPY_CLIENT_SECRET"],
        "optional_env": ["SPOTIPY_REDIRECT_URI"],
        "docs_url": "https://developer.spotify.com/dashboard",
        "skill_name": "app_integrations",
    },
]


# ── ServiceHub class ─────────────────────────────────────────


class ServiceHub:
    """Registry centralizado de servicios externos.

    Auto-descubre servicios desde env vars + skills disponibles.
    Provee health checks unificados y persiste estado.
    """

    def __init__(self, skill_executor=None):
        self._services: Dict[str, Service] = {}
        self._skill_executor = skill_executor
        self._ping_handlers: Dict[str, Callable[[], Awaitable[Dict[str, Any]]]] = {}

        DATA_DIR.mkdir(parents=True, exist_ok=True)
        self._load_state()
        self.discover()
        self._register_ping_handlers()

        connected = sum(1 for s in self._services.values() if s.connected)
        total = len(self._services)
        logger.info(f"ServiceHub initialized: {connected}/{total} services connected")

    # ── Discovery ─────────────────────────────────────────────

    def discover(self) -> None:
        """Re-descubre servicios basado en env vars actuales."""
        for entry in _SERVICE_CATALOG:
            name = entry["name"]
            required = entry.get("required_env", [])
            optional = entry.get("optional_env", [])

            # Check if all required env vars are set
            has_all_required = all(os.getenv(k, "").strip() for k in required) if required else True

            # Determine status
            if not required:
                # No required env (e.g., edge_tts, ollama) — assume available
                status = "configured"
                connected = True
            elif has_all_required:
                status = "configured"
                connected = True
            else:
                status = "available"
                connected = False

            # Preserve previous fields if service already known
            existing = self._services.get(name)

            service = Service(
                name=name,
                category=entry["category"],
                display_name=entry["display_name"],
                description=entry.get("description", ""),
                required_env=required,
                optional_env=optional,
                docs_url=entry.get("docs_url", ""),
                skill_name=entry.get("skill_name"),
                status=status,
                connected=connected,
                last_sync=existing.last_sync if existing else None,
                last_error=existing.last_error if existing else None,
                last_ping_ms=existing.last_ping_ms if existing else None,
                permissions=existing.permissions if existing else [],
            )
            self._services[name] = service

    # ── Listing ───────────────────────────────────────────────

    def list(self, category: Optional[str] = None, connected_only: bool = False) -> List[Dict[str, Any]]:
        """Lista todos los servicios, opcionalmente filtrados."""
        results = []
        for s in self._services.values():
            if category and s.category != category:
                continue
            if connected_only and not s.connected:
                continue
            results.append(s.to_dict())
        return results

    def get(self, name: str) -> Optional[Dict[str, Any]]:
        """Obtiene detalle de un servicio."""
        s = self._services.get(name)
        return s.to_dict() if s else None

    @property
    def categories(self) -> Dict[str, int]:
        """Conteo de servicios por categoria."""
        counts: Dict[str, int] = {}
        for s in self._services.values():
            counts[s.category] = counts.get(s.category, 0) + 1
        return counts

    @property
    def stats(self) -> Dict[str, Any]:
        total = len(self._services)
        connected = sum(1 for s in self._services.values() if s.connected)
        return {
            "total": total,
            "connected": connected,
            "available": total - connected,
            "categories": self.categories,
            "by_category": {
                cat: {
                    "total": sum(1 for s in self._services.values() if s.category == cat),
                    "connected": sum(1 for s in self._services.values() if s.category == cat and s.connected),
                }
                for cat in set(s.category for s in self._services.values())
            },
        }

    # ── Health checks ─────────────────────────────────────────

    def _register_ping_handlers(self):
        """Registra handlers de ping especificos por servicio."""
        self._ping_handlers = {
            "telegram": self._ping_telegram,
            "spotify": self._ping_spotify,
            "smtp_email": self._ping_smtp,
            "ollama": self._ping_ollama,
            "elevenlabs": self._ping_elevenlabs,
            "fish_audio": self._ping_fish_audio,
            "edge_tts": self._ping_edge_tts,
            "deepseek": lambda: self._ping_llm("deepseek"),
            "groq": lambda: self._ping_llm("groq"),
            "gemini": lambda: self._ping_llm("gemini"),
            "nvidia_nim": lambda: self._ping_llm("nvidia_nim"),
            "opencode": lambda: self._ping_llm("opencode"),
        }

    async def ping(self, name: str) -> Dict[str, Any]:
        """Health check de un servicio especifico."""
        service = self._services.get(name)
        if not service:
            return {"ok": False, "error": f"Service '{name}' not found"}

        if not service.connected:
            return {"ok": False, "error": "Service not configured", "status": service.status}

        handler = self._ping_handlers.get(name)
        if not handler:
            # No handler — solo marca como configured sin verificar
            service.last_sync = datetime.now().isoformat()
            self._save_state()
            return {"ok": True, "note": "no health check available, assumed configured"}

        t0 = time.perf_counter()
        try:
            result = await handler()
            elapsed = round((time.perf_counter() - t0) * 1000, 1)
            service.last_ping_ms = elapsed
            if result.get("ok"):
                service.status = "connected"
                service.last_sync = datetime.now().isoformat()
                service.last_error = None
            else:
                service.status = "error"
                service.last_error = result.get("error", "Unknown error")
            self._save_state()
            return {**result, "elapsed_ms": elapsed}
        except Exception as e:
            elapsed = round((time.perf_counter() - t0) * 1000, 1)
            service.status = "error"
            service.last_error = str(e)[:200]
            service.last_ping_ms = elapsed
            self._save_state()
            return {"ok": False, "error": str(e), "elapsed_ms": elapsed}

    async def ping_all(self, parallel: bool = True) -> Dict[str, Dict[str, Any]]:
        """Health check de todos los servicios conectados en paralelo."""
        connected = [s.name for s in self._services.values() if s.connected]
        if not connected:
            return {}

        if parallel:
            results = await asyncio.gather(
                *(self.ping(name) for name in connected),
                return_exceptions=True,
            )
            return {
                name: (res if isinstance(res, dict) else {"ok": False, "error": str(res)})
                for name, res in zip(connected, results)
            }
        else:
            return {name: await self.ping(name) for name in connected}

    # ── Service-specific pings ────────────────────────────────

    async def _ping_telegram(self) -> Dict[str, Any]:
        """Telegram: getMe."""
        if not self._skill_executor:
            return {"ok": False, "error": "skill_executor not injected"}
        tg = self._skill_executor.get("telegram")
        if not tg:
            return {"ok": False, "error": "telegram skill not found"}
        r = await tg.execute({"action": "status"})
        st = r.get("result", {})
        if st.get("configured"):
            return {
                "ok": True,
                "details": {
                    "bot": st.get("bot"),
                    "running": st.get("running"),
                    "messages": st.get("message_count", 0),
                },
            }
        return {"ok": False, "error": "telegram not configured"}

    async def _ping_spotify(self) -> Dict[str, Any]:
        """Spotify: status via app_integrations."""
        if not self._skill_executor:
            return {"ok": False, "error": "skill_executor not injected"}
        ai = self._skill_executor.get("app_integrations")
        if not ai:
            return {"ok": False, "error": "app_integrations skill not found"}
        r = await ai.execute({"action": "status"})
        st = r.get("result", {})
        return {"ok": bool(st.get("spotify_configured")), "details": st}

    async def _ping_smtp(self) -> Dict[str, Any]:
        """SMTP: validate config exists (no actual send)."""
        user = os.getenv("SMTP_USER", "")
        pwd = os.getenv("SMTP_PASS", "")
        if user and pwd:
            return {"ok": True, "details": {"user": user, "configured": True}}
        return {"ok": False, "error": "SMTP credentials missing"}

    async def _ping_ollama(self) -> Dict[str, Any]:
        """Ollama: HTTP GET /api/tags (lista modelos)."""
        try:
            import httpx

            base = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
            async with httpx.AsyncClient(timeout=3.0) as client:
                resp = await client.get(f"{base}/api/tags")
                if resp.status_code == 200:
                    data = resp.json()
                    models = [m.get("name") for m in data.get("models", [])]
                    return {"ok": True, "details": {"models": models[:5], "count": len(models)}}
                return {"ok": False, "error": f"HTTP {resp.status_code}"}
        except Exception as e:
            return {"ok": False, "error": f"Ollama not reachable: {str(e)[:100]}"}

    async def _ping_elevenlabs(self) -> Dict[str, Any]:
        """ElevenLabs: GET /v1/user (validates API key)."""
        key = os.getenv("ELEVENLABS_API_KEY", "")
        if not key:
            return {"ok": False, "error": "no api key"}
        try:
            import httpx

            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(
                    "https://api.elevenlabs.io/v1/user",
                    headers={"xi-api-key": key},
                )
                if resp.status_code == 200:
                    data = resp.json()
                    sub = data.get("subscription", {})
                    return {
                        "ok": True,
                        "details": {
                            "tier": sub.get("tier"),
                            "chars_used": sub.get("character_count"),
                            "chars_limit": sub.get("character_limit"),
                        },
                    }
                return {"ok": False, "error": f"HTTP {resp.status_code}"}
        except Exception as e:
            return {"ok": False, "error": str(e)[:100]}

    async def _ping_fish_audio(self) -> Dict[str, Any]:
        """Fish Audio: validate API key exists."""
        key = os.getenv("FISH_AUDIO_API_KEY", "")
        return {"ok": bool(key), "details": {"key_configured": bool(key)}}

    async def _ping_edge_tts(self) -> Dict[str, Any]:
        """Edge TTS: check if package is importable."""
        try:
            import edge_tts  # noqa: F401

            return {"ok": True, "details": {"package": "installed"}}
        except ImportError:
            return {"ok": False, "error": "edge-tts package not installed"}

    async def _ping_llm(self, provider_key: str) -> Dict[str, Any]:
        """Genericamente verifica un LLM provider via llm_router."""
        # Las llaves se mapean a nombres internos del router
        provider_map = {
            "deepseek": "deepseek",
            "groq": "groq",
            "gemini": "gemini",
            "nvidia_nim": "nvidia_nim",
            "opencode": "opencode",
        }
        provider = provider_map.get(provider_key, provider_key)
        env_map = {
            "deepseek": "DEEPSEEK_API_KEY",
            "groq": "GROQ_API_KEY",
            "gemini": "GEMINI_API_KEY",
            "nvidia_nim": "NVIDIA_NIM_API_KEY",
            "opencode": "OPENCODE_API_KEY",
        }
        env_key = env_map.get(provider, "")
        key = os.getenv(env_key, "") if env_key else ""
        if not key:
            return {"ok": False, "error": f"{env_key} not set"}
        return {"ok": True, "details": {"key_configured": True, "provider": provider}}

    # ── Connect / Disconnect ──────────────────────────────────

    async def connect(self, name: str, **opts) -> Dict[str, Any]:
        """Intenta conectar un servicio.

        Para servicios con credenciales en .env: solo redescubre.
        Para servicios OAuth (futuro): inicia flow.
        """
        service = self._services.get(name)
        if not service:
            return {"ok": False, "error": f"Service '{name}' not found"}

        # Re-discover (refresca env)
        self.discover()
        service = self._services.get(name)

        if not service or not service.connected:
            # No configurado — devuelve instrucciones de setup
            missing = [k for k in service.required_env if not os.getenv(k, "").strip()] if service else []
            return {
                "ok": False,
                "error": "Service not configured",
                "missing_env": missing,
                "docs_url": service.docs_url if service else "",
                "setup_hint": f"Add the following to .env: {', '.join(missing)}" if missing else "",
            }

        # Servicio ya configurado — hacer ping para confirmar
        ping_result = await self.ping(name)
        return {
            "ok": ping_result.get("ok", False),
            "service": service.to_dict(),
            "ping": ping_result,
        }

    def disconnect(self, name: str) -> Dict[str, Any]:
        """Marca un servicio como desconectado (no toca env)."""
        service = self._services.get(name)
        if not service:
            return {"ok": False, "error": f"Service '{name}' not found"}
        service.connected = False
        service.status = "available"
        service.last_sync = None
        self._save_state()
        return {"ok": True, "disconnected": name}

    # ── Permissions ───────────────────────────────────────────

    def set_permissions(self, name: str, permissions: List[str]) -> Dict[str, Any]:
        """Actualiza los permisos otorgados a un servicio."""
        service = self._services.get(name)
        if not service:
            return {"ok": False, "error": f"Service '{name}' not found"}
        service.permissions = list(permissions)
        self._save_state()
        return {"ok": True, "permissions": service.permissions}

    def has_permission(self, name: str, perm: str) -> bool:
        """Verifica si un servicio tiene un permiso."""
        service = self._services.get(name)
        if not service or not service.connected:
            return False
        return perm in service.permissions or "*" in service.permissions

    # ── Persistence ───────────────────────────────────────────

    def _load_state(self):
        if not STATE_FILE.exists():
            return
        try:
            data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
            for entry in data.get("services", []):
                try:
                    s = Service(**entry)
                    self._services[s.name] = s
                except Exception:
                    pass
        except Exception as e:
            logger.warning(f"Failed to load service hub state: {e}")

    def _save_state(self):
        try:
            data = {
                "saved_at": datetime.now().isoformat(),
                "services": [s.to_dict() for s in self._services.values()],
            }
            STATE_FILE.write_text(
                json.dumps(data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception as e:
            logger.debug(f"Failed to save service hub state: {e}")

    # ── Dependency injection ──────────────────────────────────

    def set_skill_executor(self, skill_executor):
        """Inject skill_executor para health checks."""
        self._skill_executor = skill_executor
