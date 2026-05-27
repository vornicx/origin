# Load .env into os.environ BEFORE any skill or core module imports it,
# so os.getenv(...) calls in skills (voice, app_integrations, etc.) see the values.
from dotenv import load_dotenv

load_dotenv()

import asyncio  # noqa: E402
from contextlib import asynccontextmanager  # noqa: E402
from collections import defaultdict  # noqa: E402
import base64  # noqa: E402
import uuid  # noqa: E402
from pathlib import Path  # noqa: E402
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request, UploadFile, File, HTTPException  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
from fastapi.responses import JSONResponse, FileResponse  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402
from starlette.middleware.base import BaseHTTPMiddleware  # noqa: E402
import time  # noqa: E402
from datetime import datetime  # noqa: E402

from core.mind import Mind  # noqa: E402
from core.config import settings  # noqa: E402
from core.utils import setup_logging  # noqa: E402
from core.voice_session import VoiceSession  # noqa: E402

# Setup
logger = setup_logging(settings.log_level)
ORIGIN_ROOT = Path(__file__).resolve().parent.parent

# ── Security: Allowed origins (restrict CORS) ────────────────
import os as _os  # noqa: E402

ALLOWED_ORIGINS = [
    "http://localhost:3000",
    "http://localhost:5173",  # Vite dev
    "http://localhost:4173",  # Vite preview
    "http://localhost:9001",  # FastAPI direct
    "http://127.0.0.1:3000",
    "http://127.0.0.1:5173",
    "http://127.0.0.1:4173",
    "http://127.0.0.1:9001",
    # Tauri WebView2 origins (custom protocols)
    "tauri://localhost",
    "https://tauri.localhost",
    "http://tauri.localhost",
]

# ── Native host detection ────────────────────────────────────
# ORIGIN_HOST=tauri → tray, hotkeys, and auto-start are owned by Tauri,
# so we must suppress the Python equivalents to avoid double-registration.
ORIGIN_HOST = _os.getenv("ORIGIN_HOST", "").lower()
RUNNING_UNDER_TAURI = ORIGIN_HOST == "tauri"

# ── Rate Limiting ─────────────────────────────────────────────
MAX_REQUESTS_PER_MINUTE = 60
MAX_WS_MESSAGES_PER_MINUTE = 30
MAX_INPUT_LENGTH = 10_000  # Max chars per user input


class RateLimiter:
    """Simple in-memory rate limiter per IP."""

    def __init__(self, max_requests: int = MAX_REQUESTS_PER_MINUTE, window: int = 60):
        self._requests: dict[str, list[float]] = defaultdict(list)
        self._max = max_requests
        self._window = window

    def is_allowed(self, client_ip: str) -> bool:
        now = time.time()
        # Clean old entries
        self._requests[client_ip] = [t for t in self._requests[client_ip] if now - t < self._window]
        if len(self._requests[client_ip]) >= self._max:
            return False
        self._requests[client_ip].append(now)
        return True


rate_limiter = RateLimiter()
ws_rate_limiter = RateLimiter(max_requests=MAX_WS_MESSAGES_PER_MINUTE)


class RequestIDMiddleware(BaseHTTPMiddleware):
    """Attaches a unique X-Request-ID to every request/response for tracing."""

    async def dispatch(self, request: Request, call_next):
        req_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
        request.state.request_id = req_id
        response = await call_next(request)
        response.headers["X-Request-ID"] = req_id
        return response


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Adds security headers to all responses."""

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = "camera=(self), microphone=(self), geolocation=()"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            "script-src 'self' 'unsafe-inline'; "
            "style-src 'self' 'unsafe-inline'; "
            "connect-src 'self' ws://localhost:* wss://localhost:*; "
            "img-src 'self' data:; "
            "frame-ancestors 'none';"
        )
        # Don't expose server info — MutableHeaders supports del, not pop()
        if "server" in response.headers:
            del response.headers["server"]
        return response


class AccessLogMiddleware(BaseHTTPMiddleware):
    """Emits one structured log line per HTTP request with method, path, status, latency, and request ID."""

    async def dispatch(self, request: Request, call_next):
        t0 = time.perf_counter()
        response = await call_next(request)
        latency_ms = round((time.perf_counter() - t0) * 1000, 1)
        req_id = getattr(request.state, "request_id", "-")
        logger.info(
            "%s %s %s %.1fms req_id=%s",
            request.method,
            request.url.path,
            response.status_code,
            latency_ms,
            req_id,
        )
        return response


_LOCALHOST_IPS = frozenset({"127.0.0.1", "::1", "localhost"})


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Rate limits HTTP requests per client IP.

    Localhost (127.0.0.1 / ::1) is exempt because Origin is a local desktop app
    talking to itself — the dashboard and OS panel poll many endpoints by design.
    """

    async def dispatch(self, request: Request, call_next):
        client_ip = request.client.host if request.client else "unknown"
        if client_ip in _LOCALHOST_IPS:
            return await call_next(request)
        if not rate_limiter.is_allowed(client_ip):
            logger.warning(f"Rate limit exceeded for {client_ip}")
            return JSONResponse(
                {"error": "Rate limit exceeded. Try again later."},
                status_code=429,
            )
        return await call_next(request)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Origin API starting...")
    if RUNNING_UNDER_TAURI:
        logger.info("Running under Tauri host — tray and global hotkeys owned by Rust shell")
    logger.info(f"Available LLM providers: {mind.llm_router.get_available_providers()}")

    # ── Deep Windows integration: auto-start tray + active window tracking ──
    if not RUNNING_UNDER_TAURI:
        try:
            tray = mind.skill_executor.get("system_tray")
            wake = mind.skill_executor.get("wake_word")
            if tray:
                if wake:
                    tray.set_wake_word_skill(wake)
                tray_result = await tray.execute({"action": "start"})
                if tray_result.get("success"):
                    logger.info(f"System tray auto-started: {tray_result['result']}")
                else:
                    logger.warning(f"System tray auto-start failed: {tray_result.get('error')}")
        except Exception as e:
            logger.warning(f"Tray autostart skipped: {e}")
    else:
        logger.info("Skipping Python system_tray auto-start (Tauri owns tray)")

    try:
        aw = mind.skill_executor.get("active_window")
        if aw:
            r = await aw.execute({"action": "track_start", "interval": 2.0})
            if r.get("success"):
                logger.info("Active window tracking auto-started")
    except Exception as e:
        logger.debug(f"Active window tracking skipped: {e}")

    try:
        clip = mind.skill_executor.get("clipboard")
        if clip:
            r = await clip.execute({"action": "watch_start", "interval": 0.8})
            if r.get("success"):
                logger.info("Clipboard watcher auto-started")
    except Exception as e:
        logger.debug(f"Clipboard watch skipped: {e}")

    # ── Background intelligence systems ──────────────────────────
    # SubconsciousEngine: background reflection loop
    try:
        mind.subconscious.set_broadcast(manager.broadcast)
        mind.subconscious.start()
        logger.info("Subconscious engine started (background reflection active)")
    except Exception as e:
        logger.warning(f"Subconscious start failed: {e}")

    # ProactiveEngine: monitors system events (window changes, clipboard, CPU spikes)
    try:
        mind.proactive.set_broadcast(manager.broadcast)
        mind.proactive.start()
        logger.info("Proactive engine started (ambient event monitoring active)")
    except Exception as e:
        logger.warning(f"Proactive engine start failed: {e}")

    # ServicesGraph: wire WS broadcast + connect to ProactiveEngine
    try:
        mind.services_graph.set_broadcast(manager.broadcast)
        mind.proactive.set_services_graph(mind.services_graph)
        logger.info("ServicesGraph wired (WS broadcast + ProactiveEngine)")
    except Exception as e:
        logger.warning(f"ServicesGraph wiring failed: {e}")

    # Cron scheduler
    try:
        cron.start()
        cron.set_services_graph(mind.services_graph)
        logger.info("Cron scheduler started")
    except Exception as e:
        logger.warning(f"Cron start failed: {e}")

    # Auto-context bootstrap with memory injection + periodic refresh
    try:
        async def _bootstrap_ctx():
            try:
                await asyncio.sleep(1.5)  # let other systems finish initializing
                bundle = await mind.auto_context.bootstrap(inject_into_memory=True)
                logger.info(
                    f"AutoContext bootstrap: {bundle.sources_ok}/"
                    f"{bundle.sources_ok + bundle.sources_failed} sources, "
                    f"{bundle.total_chars} chars in {bundle.took_ms}ms"
                )
                mind.auto_context.start_periodic_refresh()
            except Exception as _e:
                logger.warning(f"AutoContext bootstrap error: {_e}")

        asyncio.create_task(_bootstrap_ctx())
        logger.info("Auto-context bootstrap scheduled (inject_into_memory=True)")
    except Exception as e:
        logger.warning(f"Auto-context bootstrap failed: {e}")

    # ── Auto-start wake word daemon (Hey Origin) ──
    try:
        if RUNNING_UNDER_TAURI:
            logger.info("Skipping Python wake-word auto-start (HUD voice stream owns microphone)")
            wake = None
        else:
            wake = mind.skill_executor.get("wake_word")
        voice = mind.skill_executor.get("voice")
        if wake and voice:
            import asyncio as _asyncio

            wake.set_dependencies(_asyncio.get_event_loop(), mind, voice)
            wake.set_broadcast(manager.broadcast)
            r = await wake.execute({"action": "start", "language": "es-ES"})
            if r.get("success"):
                logger.info("Wake word daemon auto-started — say 'Hey Origin' to activate")
            else:
                logger.warning(f"Wake word auto-start failed: {r.get('error')}")
    except Exception as e:
        logger.warning(f"Wake word autostart skipped: {e}")

    # ── Monitor WS broadcast + SelfImprovement + ServiceHub + Telegram ──
    monitor = mind.skill_executor.get("monitor")
    if monitor and hasattr(monitor, "set_ws_broadcast"):
        monitor.set_ws_broadcast(manager.broadcast, asyncio.get_event_loop())
        logger.info("Monitor → WebSocket broadcast connected")

    try:
        if _improvement_available and improvement is not None:
            improvement.start()
            logger.info("SelfImprovement loop auto-started (background)")
    except Exception as e:
        logger.warning(f"SelfImprovement start skipped: {e}")

    try:
        async def _initial_ping():
            try:
                results = await mind.service_hub.ping_all(parallel=True)
                ok = sum(1 for r in results.values() if r.get("ok"))
                logger.info(f"ServiceHub initial health check: {ok}/{len(results)} services up")
            except Exception as e:
                logger.debug(f"Initial service ping failed: {e}")

        asyncio.create_task(_initial_ping())
    except Exception as e:
        logger.debug(f"Service hub init skipped: {e}")

    try:
        if RUNNING_UNDER_TAURI:
            logger.info("Skipping Telegram auto-start during Tauri boot")
            tg = None
        else:
            tg = mind.skill_executor.get("telegram")
        if tg and tg._configured:
            tg.set_mind(mind)
            voice = mind.skill_executor.get("voice")
            if voice:
                tg.set_voice_skill(voice)
            r = await tg.execute({"action": "start"})
            if r.get("success"):
                logger.info(f"Telegram bot auto-started: {r['result']}")
            else:
                logger.warning(f"Telegram bot start failed: {r.get('error')}")
    except Exception as e:
        logger.warning(f"Telegram bot autostart skipped: {e}")

    yield

    # ── Cleanup ──
    logger.info("Origin API shutting down...")
    try:
        mind.subconscious.stop()
    except Exception:
        pass

    try:
        mind.proactive.stop()
    except Exception:
        pass

    try:
        mind.auto_context.stop_periodic_refresh()
    except Exception:
        pass

    try:
        tg = mind.skill_executor.get("telegram")
        if tg:
            await tg.close()
    except Exception:
        pass

    if not RUNNING_UNDER_TAURI:
        try:
            tray = mind.skill_executor.get("system_tray")
            if tray:
                await tray.execute({"action": "stop"})
        except Exception:
            pass

    try:
        wake = mind.skill_executor.get("wake_word")
        if wake:
            await wake.execute({"action": "stop"})
    except Exception:
        pass

    await mind.llm_router.close()
    logger.info("Origin API shutdown complete.")


app = FastAPI(
    title="Origin",
    version="1.0.0",
    description="Origin AI Assistant API",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)

# Security middleware (order matters: first added = outermost)
app.add_middleware(AccessLogMiddleware)
app.add_middleware(RequestIDMiddleware)
app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(RateLimitMiddleware)

# CORS — restricted to localhost only
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type", "Authorization"],
)

# Instancia global de Mind
mind = Mind()

# ── Auth setup ───────────────────────────────────────────────
from core.auth import setup_default_admin  # noqa: E402

setup_default_admin()

# ── MCP Server setup ─────────────────────────────────────────
from skills.mcp_server import MCPServer  # noqa: E402

mcp_server = MCPServer(mind.skill_executor)

# ── SelfImprovement setup ────────────────────────────────────
try:
    from skills.self_improvement import SelfImprovement  # noqa: E402

    improvement = SelfImprovement(mind.llm_router, mind.skill_executor)
    _improvement_available = True
except Exception as _imp_err:
    improvement = None
    _improvement_available = False
    logger.warning(f"SelfImprovement unavailable: {_imp_err}")


# ── Connection Manager (must be before lifespan) ─────────────
class ConnectionManager:
    """Gestor de conexiones WebSocket"""

    def __init__(self):
        self.active_connections: list[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)
        logger.info(f"Client connected. Total: {len(self.active_connections)}")

    def disconnect(self, websocket: WebSocket):
        try:
            self.active_connections.remove(websocket)
        except ValueError:
            pass
        logger.info(f"Client disconnected. Total: {len(self.active_connections)}")

    async def broadcast(self, message: dict):
        dead = []
        for connection in self.active_connections:
            try:
                await connection.send_json(message)
            except Exception:
                dead.append(connection)
        for conn in dead:
            try:
                self.active_connections.remove(conn)
            except ValueError:
                pass
            try:
                await conn.close()
            except Exception:
                pass
            logger.debug("Removed dead WebSocket connection during broadcast")


manager = ConnectionManager()

# ── Cron setup (needs manager) ───────────────────────────────
from skills.cron_skill import CronSkill  # noqa: E402

cron = CronSkill(mind.skill_executor)
cron.set_broadcast(manager.broadcast)


# =====================
# HTTP Routes
# =====================


@app.get("/health")
async def health_check():
    """Health check with subsystem status (cached 10s to reduce polling overhead)."""
    from core.cache import cache as _cache
    cached = await _cache.get("health:snapshot")
    if cached is not None:
        return cached

    skill_status = mind.skill_executor.get_skill_status()
    ok_skills = [n for n, s in skill_status.items() if s["status"] == "ok"]
    failed_skills = {n: s["error"] for n, s in skill_status.items() if s["status"] == "failed"}

    mem = mind.memory_mgr
    result = {
        "status": "ok",
        "service": "origin-api",
        "llm": {
            "providers_available": mind.llm_router.get_available_providers(),
            "health": mind.llm_router.health_stats(),
        },
        "skills": {
            "registered": len(ok_skills),
            "failed": list(failed_skills.keys()),
            "failed_reasons": failed_skills,
        },
        "subsystems": {
            "subconscious": "running" if mind.subconscious._running else "stopped",
            "memory_tree": "active",
            "services_graph": "active",
            "auto_context": "active",
            "self_improvement": "available" if _improvement_available else "unavailable",
        },
        "memory": {
            "memories": len(mem.memories),
            "preferences": len(mem.preferences),
            "conversations": len(mem.conversation_history),
        },
    }
    await _cache.set("health:snapshot", result, ttl=10)
    return result


@app.post("/mind/think")
async def think(request: dict):
    """
    Endpoint principal del reasoning loop

    Request:
    {
        "input": "Tu pregunta aquí"
    }

    Response:
    {
        "cycle_id": "...",
        "final_answer": "...",
        "reasoning_steps": {...},
        "timestamp": "..."
    }
    """
    try:
        user_input = request.get("input", "")
        if not user_input:
            return JSONResponse({"error": "Empty input"}, status_code=400)

        # SECURITY: Validate input length
        if len(user_input) > MAX_INPUT_LENGTH:
            return JSONResponse(
                {"error": f"Input too long (max {MAX_INPUT_LENGTH} chars)"},
                status_code=400,
            )

        # SECURITY: Prompt injection check
        guard_result = mind.injection_guard.analyze(user_input)
        if guard_result.is_blocked:
            logger.warning(f"HTTP input BLOCKED: score={guard_result.risk_score:.2f}")
            return JSONResponse(
                {
                    "error": "Input blocked by security filter.",
                    "risk_score": guard_result.risk_score,
                    "risk_level": guard_result.risk_level,
                },
                status_code=403,
            )

        # Ejecuta el reasoning loop
        logger.info(f"Processing: {user_input[:50]}...")
        result = await mind.think(user_input)

        return {
            "cycle_id": result.cycle_id,
            "final_answer": result.final_answer,
            "reasoning_steps": {k.value: v for k, v in result.steps.items()},
            "timestamp": result.timestamp.isoformat(),
            "memories_saved": len(result.memories_to_save),
        }

    except Exception as e:
        logger.error(f"Error in think: {str(e)}")
        # SECURITY: Don't expose internal error details to client
        return JSONResponse({"error": "Internal processing error. Check server logs."}, status_code=500)


@app.get("/mind/profile")
async def get_profile():
    """Obtiene el perfil del usuario y configuración de Origin"""
    try:
        return mind.profile_mgr.get_full_profile()
    except Exception as e:
        logger.error(f"Error in profile: {e}")
        return JSONResponse({"error": "Failed to load profile"}, status_code=500)


# =====================
# Auth Routes
# =====================


@app.post("/auth/register")
async def auth_register(request: dict):
    """Registra un nuevo usuario."""
    from core.auth import register_user, MIN_PASSWORD_LENGTH

    username = request.get("username", "")
    password = request.get("password", "")
    if not username or not password:
        return JSONResponse({"error": "username and password required"}, status_code=400)
    if len(password) < MIN_PASSWORD_LENGTH:
        return JSONResponse({"error": f"Password must be at least {MIN_PASSWORD_LENGTH} characters"}, status_code=400)
    try:
        return register_user(username, password)
    except HTTPException as e:
        return JSONResponse({"error": e.detail}, status_code=e.status_code)
    except Exception:
        return JSONResponse({"error": "Registration failed"}, status_code=400)


@app.post("/auth/login")
async def auth_login(request: dict):
    """Login: retorna token HMAC. Pass remember=true para token de 30 días."""
    from core.auth import authenticate_user

    username = request.get("username", "")
    password = request.get("password", "")
    remember = bool(request.get("remember", False))
    if not username or not password:
        return JSONResponse({"error": "username and password required"}, status_code=400)
    try:
        return authenticate_user(username, password, remember=remember)
    except Exception:
        return JSONResponse({"error": "Invalid credentials"}, status_code=401)


@app.post("/auth/logout")
async def auth_logout(request: Request):
    """Revoca el token actual."""
    from core.auth import revoke_token, _extract_bearer

    token = _extract_bearer(request)
    revoked = revoke_token(token) if token else False
    return {"revoked": revoked}


@app.get("/auth/check")
async def auth_check(request: Request):
    """Verifica si el token actual es válido."""
    from core.auth import get_current_user

    username = await get_current_user(request)
    return {"authenticated": username != "anonymous", "username": username}


@app.get("/auth/export")
async def auth_export_data(request: Request):
    """GDPR right to data portability: exports all data for the authenticated user."""
    from core.auth import require_user

    await require_user(request)
    export = mind.memory_mgr.export_memories()
    export["exported_at"] = datetime.utcnow().isoformat()
    return export


@app.delete("/auth/account")
async def auth_delete_account(request: Request):
    """GDPR right to erasure: deletes all user data and the account."""
    from core.auth import (
        require_user, _extract_bearer, revoke_token,
        _FILE_LOCK, AUTH_USERS_FILE, _save_json_atomic, _load_json,
    )

    username = await require_user(request)
    mind.memory_mgr.clear_all()
    token = _extract_bearer(request)
    if token:
        revoke_token(token)
    with _FILE_LOCK:
        users = _load_json(AUTH_USERS_FILE)
        users.pop(username, None)
        _save_json_atomic(AUTH_USERS_FILE, users)
    logger.info(f"Account and all data deleted for user: {username}")
    return {"deleted": True, "username": username}


# =====================
# Dashboard Skills
# =====================


@app.get("/dashboard/hooks")
async def dashboard_hooks():
    """Lista hooks/plugins registrados."""
    pm = mind.skill_executor.plugin_manager
    return {"hooks": pm.get_registered_hooks()}


@app.get("/dashboard/autoskill")
async def dashboard_autoskill():
    """Estado del sistema AutoSkill."""
    return mind.auto_skill.stats


@app.get("/dashboard/skills")
async def dashboard_skills():
    """Lista todas las skills con su estado de registro."""
    return mind.skill_executor.get_skill_status()


@app.get("/dashboard/improvement")
async def dashboard_improvement():
    """Estado del sistema SelfImprovement."""
    if not _improvement_available or improvement is None:
        return {"status": "unavailable", "reason": "SelfImprovement failed to load"}
    return improvement.stats


@app.post("/dashboard/improvement/cycle")
async def dashboard_improvement_cycle():
    """Ejecuta un ciclo de mejora manualmente."""
    if not _improvement_available or improvement is None:
        return {"status": "unavailable", "reason": "SelfImprovement failed to load"}
    report = await improvement._run_cycle()
    return report.to_dict() if report else {"error": "No report generated"}


@app.get("/dashboard/improvement/history")
async def dashboard_improvement_history(limit: int = 20, offset: int = 0):
    """Historial de reportes de automejora."""
    if not _improvement_available or improvement is None:
        return {"status": "unavailable", "reason": "SelfImprovement failed to load"}
    limit = min(max(1, limit), 100)
    offset = max(0, offset)
    all_reports = improvement.reports
    page = all_reports[offset:offset + limit]
    return {
        "total": len(all_reports),
        "offset": offset,
        "limit": limit,
        "reports": page,
    }


@app.get("/mind/memory/search")
async def search_memory(query: str, top_k: int = 5):
    """Busca en la memoria"""
    # SECURITY: Validate inputs
    if not query or len(query) > 1000:
        return JSONResponse({"error": "Invalid query"}, status_code=400)
    top_k = min(max(1, top_k), 50)  # Clamp 1-50

    try:
        results = mind.memory_mgr.search_memories(query, top_k=top_k)
        return {
            "query": query,
            "results": [
                {
                    "content": m.content,
                    "type": m.type,
                    "relevance": m.relevance_score,
                    "timestamp": m.timestamp.isoformat(),
                }
                for m in results
            ],
        }
    except Exception as e:
        logger.error(f"Error in memory search: {e}")
        return JSONResponse({"error": "Memory search failed"}, status_code=500)


@app.get("/mind/conversation")
async def get_conversation(n: int = 20):
    """Obtiene el historial reciente de conversación"""
    n = min(max(1, n), 100)  # Clamp 1-100
    try:
        history = mind.memory_mgr.get_recent_conversation(n=n)
        return {"count": len(history), "conversation": history}
    except Exception as e:
        logger.error(f"Error in conversation: {e}")
        return JSONResponse({"error": "Failed to load conversation"}, status_code=500)


# =====================
# WebSocket Routes
# =====================


@app.websocket("/ws/chat")
async def websocket_endpoint(websocket: WebSocket):
    """
    WebSocket para chat en tiempo real

    Messages:
    {
        "type": "message",
        "content": "User input"
    }
    """
    await manager.connect(websocket)
    client_ip = websocket.client.host if websocket.client else "unknown"
    consecutive_rate_violations = 0

    try:
        while True:
            data = await websocket.receive_json()

            # SECURITY: Rate limit WebSocket messages
            if not ws_rate_limiter.is_allowed(client_ip):
                consecutive_rate_violations += 1
                if consecutive_rate_violations >= 5:
                    logger.warning(f"WS rate limit: closing connection {client_ip} after {consecutive_rate_violations} consecutive violations")
                    await websocket.send_json({"type": "error", "message": "Rate limit exceeded. Connection closed."})
                    break
                await websocket.send_json({"type": "error", "message": "Rate limit exceeded. Slow down."})
                continue
            consecutive_rate_violations = 0

            if data.get("type") == "message":
                user_input = data.get("content", "")

                # SECURITY: Validate input
                if not user_input or not isinstance(user_input, str):
                    continue
                if len(user_input) > MAX_INPUT_LENGTH:
                    await websocket.send_json(
                        {"type": "error", "message": f"Input too long (max {MAX_INPUT_LENGTH} chars)"}
                    )
                    continue

                logger.info(f"WS Message: {user_input[:50]}")

                # SECURITY: Prompt injection check
                guard_result = mind.injection_guard.analyze(user_input)
                if guard_result.is_blocked:
                    logger.warning(f"WS input BLOCKED: score={guard_result.risk_score:.2f}")
                    await websocket.send_json(
                        {
                            "type": "answer",
                            "content": (
                                "Tu mensaje fue bloqueado por el sistema de seguridad. "
                                "Contiene patrones que se parecen a un intento de manipulacion."
                            ),
                            "blocked": True,
                            "risk_score": guard_result.risk_score,
                        }
                    )
                    continue

                # Envía status: thinking
                await websocket.send_json({"type": "status", "message": "Procesando...", "stage": "thinking"})

                # Step-by-step progress callback
                async def on_step(step: str, num: int, total: int, data: dict):
                    try:
                        await websocket.send_json(
                            {
                                "type": "reasoning_step",
                                "step": step,
                                "step_number": num,
                                "total_steps": total,
                                "data": data,
                            }
                        )
                    except Exception:
                        pass  # Client may have disconnected

                # Ejecuta reasoning loop con streaming de progreso
                import time as _time
                _t0 = _time.perf_counter()
                result = await mind.think(user_input, on_step=on_step)
                _latency_ms = (_time.perf_counter() - _t0) * 1000.0

                # Envía resultado final
                steps_data = {k.value: v for k, v in result.steps.items()}
                skills_used = steps_data.get("act", {}).get("skills_executed", [])
                llm_metrics = getattr(mind.responder, "last_llm_result", {}) or {}

                await websocket.send_json(
                    {
                        "type": "answer",
                        "content": result.final_answer,
                        "cycle_id": result.cycle_id,
                        "timestamp": result.timestamp.isoformat(),
                        "skills_used": skills_used,
                        "latency_ms": round(_latency_ms, 1),
                        "llm_latency_ms": llm_metrics.get("latency_ms"),
                        "completion_tokens": llm_metrics.get("completion_tokens"),
                        "tokens_per_s": llm_metrics.get("tokens_per_s"),
                        "provider": llm_metrics.get("provider"),
                    }
                )

    except WebSocketDisconnect:
        manager.disconnect(websocket)
    except Exception as e:
        logger.error(f"WebSocket error: {str(e)}")
        try:
            # SECURITY: Don't expose internal errors
            await websocket.send_json({"type": "error", "message": "Internal error. Check server logs."})
        except Exception:
            pass
        manager.disconnect(websocket)


# =====================
# Chat (HTTP) — used by QuickQuery, ClipboardAnalyzer, PowerShell CLI
# =====================

@app.post("/chat")
async def chat_http(request: dict):
    """Simple HTTP chat endpoint. Wraps the reasoning loop for non-WS clients."""
    message = request.get("message", "")
    if not message:
        return JSONResponse({"error": "message required"}, status_code=400)
    if len(message) > MAX_INPUT_LENGTH:
        return JSONResponse(
            {"error": f"Input too long (max {MAX_INPUT_LENGTH} chars)"},
            status_code=400,
        )
    guard_result = mind.injection_guard.analyze(message)
    if guard_result.is_blocked:
        logger.warning(f"HTTP /chat BLOCKED: score={guard_result.risk_score:.2f}")
        return JSONResponse(
            {"error": "Input blocked by security filter.", "risk_score": guard_result.risk_score},
            status_code=403,
        )
    try:
        result = await mind.think(message)
        return {"reply": result.final_answer, "cycle_id": result.cycle_id}
    except Exception as e:
        logger.error(f"Error in /chat: {e}")
        return JSONResponse({"error": "Internal processing error."}, status_code=500)


# =====================
# Dashboard API Routes
# =====================


@app.get("/dashboard/news")
async def dashboard_news():
    """Live news feed for the HUD. Uses RSS with httpx fallback to static data."""
    import xml.etree.ElementTree as ET

    feeds = [
        ("https://feeds.bbci.co.uk/news/world/rss.xml", "BBC"),
        ("https://rss.nytimes.com/services/xml/rss/nyt/World.xml", "NYT"),
        ("https://feeds.reuters.com/reuters/topNews", "Reuters"),
    ]
    articles = []
    try:
        import httpx

        async with httpx.AsyncClient(timeout=6.0) as client:
            for url, source in feeds:
                if len(articles) >= 8:
                    break
                try:
                    resp = await client.get(url)
                    if resp.status_code != 200:
                        continue
                    root = ET.fromstring(resp.text)
                    for item in root.iter("item"):
                        title_el = item.find("title")
                        link_el = item.find("link")
                        if title_el is None or not title_el.text:
                            continue
                        thumb = None
                        media = item.find("{http://search.yahoo.com/mrss/}thumbnail")
                        if media is not None:
                            thumb = media.get("url")
                        enclosure = item.find("enclosure")
                        if not thumb and enclosure is not None and "image" in (enclosure.get("type") or ""):
                            thumb = enclosure.get("url")
                        articles.append({
                            "title": title_el.text.strip(),
                            "source": source,
                            "url": link_el.text.strip() if link_el is not None and link_el.text else None,
                            "thumbnail": thumb,
                        })
                        if len(articles) >= 8:
                            break
                except Exception:
                    continue
    except Exception as e:
        logger.warning(f"News fetch failed: {e}")

    if not articles:
        articles = [
            {"title": "Origin online — all systems nominal.", "source": "Origin"},
        ]
    return {"articles": articles}


@app.get("/dashboard/market")
async def dashboard_market():
    """Market data for the HUD panel."""
    items = []
    chart = []
    try:
        import httpx

        async with httpx.AsyncClient(timeout=6.0) as client:
            symbols = {
                "^GSPC": "S&P 500",
                "^IXIC": "NASDAQ",
                "BTC-USD": "BTC/USD",
                "^DJI": "DOW",
            }
            for symbol, label in symbols.items():
                try:
                    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?range=1d&interval=5m"
                    resp = await client.get(url, headers={"User-Agent": "Mozilla/5.0"})
                    if resp.status_code != 200:
                        continue
                    data = resp.json()
                    result = data.get("chart", {}).get("result", [{}])[0]
                    meta = result.get("meta", {})
                    price = meta.get("regularMarketPrice", 0)
                    prev = meta.get("previousClose") or meta.get("chartPreviousClose") or price
                    change_pct = ((price - prev) / prev * 100) if prev else 0

                    if price >= 1000:
                        value_str = f"{price:,.2f}"
                    else:
                        value_str = f"{price:.2f}"

                    items.append({
                        "label": label,
                        "value": value_str,
                        "change": f"{change_pct:+.2f}%",
                        "positive": change_pct >= 0,
                    })

                    if label == "S&P 500":
                        closes = result.get("indicators", {}).get("quote", [{}])[0].get("close", [])
                        chart = [c for c in closes if c is not None]
                except Exception:
                    continue
    except Exception as e:
        logger.warning(f"Market fetch failed: {e}")

    if not items:
        import math, random

        items = [
            {"label": "S&P 500", "value": "5,304.72", "change": "+0.41%", "positive": True},
            {"label": "NASDAQ", "value": "16,831.48", "change": "+0.28%", "positive": True},
            {"label": "BTC/USD", "value": "104,210", "change": "-1.12%", "positive": False},
        ]
        chart = [50 + math.sin(i * 0.15) * 20 + random.random() * 10 for i in range(60)]

    return {"items": items, "chart": chart}


@app.get("/context/list")
async def context_list_alias():
    """Alias for /context — used by LeftPanel."""
    return {"contexts": mind.context_manager.list_contexts()}


@app.get("/llm/health")
async def llm_health():
    """LLM provider health stats for dashboard."""
    return mind.llm_router.health_stats()


@app.get("/dashboard/skills/list")
async def dashboard_skills_list():
    """Lista todas las skills con metadata."""
    skills = mind.skill_executor.list_skills()
    return {
        "count": len(skills),
        "skills": skills,
    }


@app.get("/dashboard/memory")
async def dashboard_memory():
    """Estado de la memoria: stats, preferencias, memorias recientes."""
    stats = mind.memory_mgr.get_stats()
    prefs = mind.memory_mgr.get_all_preferences()
    recent = mind.memory_mgr.get_recent_conversation(n=10)
    return {
        "stats": stats,
        "preferences": prefs,
        "recent_conversations": recent,
    }


@app.get("/dashboard/memory_tree")
async def dashboard_memory_tree():
    """Estado del Memory Tree: nodos por nivel, root identity, stats."""
    stats = mind.memory_tree.get_stats()
    layers = mind.memory_tree.get_context_layers()
    return {
        "stats": stats,
        "layers": layers,
    }


@app.get("/dashboard/subconscious")
async def dashboard_subconscious():
    """Estado del Subconscious Engine: thoughts, stats."""
    return {
        "stats": mind.subconscious.stats,
        "recent_thoughts": mind.subconscious.get_recent_thoughts(n=15),
    }


# =====================
# ServiceHub Routes
# =====================


@app.get("/services")
async def services_list(category: str = "", connected_only: bool = False):
    """Lista todos los servicios registrados, opcionalmente filtrados."""
    return {
        "stats": mind.service_hub.stats,
        "services": mind.service_hub.list(
            category=category or None,
            connected_only=connected_only,
        ),
    }


@app.get("/services/{name}")
async def services_get(name: str):
    """Detalle de un servicio especifico."""
    svc = mind.service_hub.get(name)
    if not svc:
        return JSONResponse({"error": f"Service '{name}' not found"}, status_code=404)
    return svc


@app.post("/services/{name}/connect")
async def services_connect(name: str, request: dict = None):
    """Conecta un servicio (refresca env + ping)."""
    opts = request or {}
    result = await mind.service_hub.connect(name, **opts)
    if not result.get("ok"):
        return JSONResponse(result, status_code=400)
    return result


@app.post("/services/{name}/disconnect")
async def services_disconnect(name: str):
    """Desconecta un servicio."""
    return mind.service_hub.disconnect(name)


@app.post("/services/{name}/ping")
async def services_ping(name: str):
    """Health check de un servicio especifico."""
    return await mind.service_hub.ping(name)


@app.post("/services/ping_all")
async def services_ping_all():
    """Health check de todos los servicios conectados (paralelo)."""
    results = await mind.service_hub.ping_all(parallel=True)
    return {
        "checked": len(results),
        "ok": sum(1 for r in results.values() if r.get("ok")),
        "results": results,
    }


@app.post("/services/{name}/permissions")
async def services_set_permissions(name: str, request: dict):
    """Actualiza permisos otorgados a un servicio."""
    perms = request.get("permissions", [])
    if not isinstance(perms, list):
        return JSONResponse({"error": "permissions must be a list"}, status_code=400)
    return mind.service_hub.set_permissions(name, perms)


# =====================
# ProactiveEngine Routes
# =====================


@app.get("/proactive/status")
async def proactive_status():
    """Estado del motor proactivo."""
    return mind.proactive.stats


@app.get("/proactive/events")
async def proactive_events(n: int = 30, kind: str = ""):
    """Lista eventos proactivos recientes."""
    n = min(max(1, n), 100)
    return {"events": mind.proactive.get_recent(n=n, kind=kind or None)}


@app.post("/proactive/dismiss/{event_id}")
async def proactive_dismiss(event_id: str):
    """Marca un evento como dismissado."""
    return {"dismissed": mind.proactive.dismiss(event_id)}


@app.post("/proactive/execute/{event_id}")
async def proactive_execute(event_id: str):
    """Ejecuta la accion sugerida de un evento."""
    return await mind.proactive.execute_suggestion(event_id)


@app.post("/proactive/focus_mode")
async def proactive_focus_mode(request: dict):
    """Activa o desactiva el modo enfoque (suprime sugerencias)."""
    enabled = bool(request.get("enabled", False))
    mind.proactive.set_focus_mode(enabled)
    return {"focus_mode": enabled}


@app.post("/proactive/start")
async def proactive_start():
    """Inicia el motor proactivo."""
    mind.proactive.set_broadcast(manager.broadcast)
    mind.proactive.start()
    return {"started": True}


@app.post("/proactive/stop")
async def proactive_stop():
    """Detiene el motor proactivo."""
    mind.proactive.stop()
    return {"stopped": True}


@app.post("/proactive/reload-rules")
async def proactive_reload_rules():
    """Recarga las reglas personalizadas desde data/proactive/rules.json sin reiniciar."""
    mind.proactive.reload_rules()
    return {
        "reloaded": True,
        "window_rules": len(mind.proactive._window_rules),
        "clipboard_rules": len(mind.proactive._clipboard_rules),
    }


# =====================
# Workflows (ServicesGraph) Routes
# =====================


@app.get("/workflows")
async def workflows_list(enabled_only: bool = False):
    """Lista todos los workflows."""
    return {
        "stats": mind.services_graph.stats,
        "workflows": mind.services_graph.list(enabled_only=enabled_only),
    }


@app.get("/workflows/{workflow_id}")
async def workflows_get(workflow_id: str):
    """Detalle de un workflow."""
    wf = mind.services_graph.get(workflow_id)
    if not wf:
        return JSONResponse({"error": f"Workflow '{workflow_id}' not found"}, status_code=404)
    return wf


@app.post("/workflows")
async def workflows_create(request: dict):
    """Crea un nuevo workflow."""
    result = mind.services_graph.create(request)
    if not result.get("ok"):
        return JSONResponse(result, status_code=400)
    return result


@app.put("/workflows/{workflow_id}")
async def workflows_update(workflow_id: str, request: dict):
    """Actualiza un workflow."""
    result = mind.services_graph.update(workflow_id, request)
    if not result.get("ok"):
        return JSONResponse(result, status_code=400)
    return result


@app.delete("/workflows/{workflow_id}")
async def workflows_delete(workflow_id: str):
    """Elimina un workflow."""
    return mind.services_graph.delete(workflow_id)


@app.post("/workflows/{workflow_id}/enable")
async def workflows_enable(workflow_id: str):
    return mind.services_graph.enable(workflow_id)


@app.post("/workflows/{workflow_id}/disable")
async def workflows_disable(workflow_id: str):
    return mind.services_graph.disable(workflow_id)


@app.post("/workflows/{workflow_id}/run")
async def workflows_run(workflow_id: str, request: dict = None):
    """Dispara un workflow manualmente."""
    return await mind.services_graph.run_manual(workflow_id, request or {})


@app.get("/workflows/runs/recent")
async def workflows_runs(n: int = 30, workflow_id: str = ""):
    """Historial de ejecuciones de workflows."""
    n = min(max(1, n), 100)
    return {"runs": mind.services_graph.get_runs(n=n, workflow_id=workflow_id or None)}


# =====================
# AutoContext Routes
# =====================


@app.get("/context/auto/status")
async def auto_context_status():
    """Estado del AutoContextLoader."""
    return mind.auto_context.stats


@app.get("/context/auto/sources")
async def auto_context_sources():
    """Lista las sources registradas."""
    return {"sources": mind.auto_context.list_sources()}


@app.post("/context/auto/bootstrap")
async def auto_context_bootstrap(request: dict = None):
    """Ejecuta bootstrap manualmente."""
    inject = bool((request or {}).get("inject_into_memory", True))
    bundle = await mind.auto_context.bootstrap(inject_into_memory=inject)
    return bundle.to_dict()


@app.get("/context/auto/last")
async def auto_context_last():
    """Ultimo bundle generado."""
    last = mind.auto_context.last_bundle
    if not last:
        return JSONResponse({"error": "no bundle yet"}, status_code=404)
    return last


@app.post("/context/auto/sources/{name}/enable")
async def auto_context_enable_source(name: str):
    ok = mind.auto_context.enable_source(name)
    return {"enabled": ok}


@app.post("/context/auto/sources/{name}/disable")
async def auto_context_disable_source(name: str):
    ok = mind.auto_context.disable_source(name)
    return {"disabled": ok}


@app.get("/dashboard/token_juice")
async def dashboard_token_juice():
    """Estado del TokenJuice: stats de compresion basada en reglas."""
    return mind.token_juice.stats


@app.get("/dashboard/injection_guard")
async def dashboard_injection_guard():
    """Estado del Injection Guard: stats, audit reciente."""
    return {
        "stats": mind.injection_guard.stats,
        "recent_audit": mind.injection_guard.get_audit_recent(n=20),
    }


@app.post("/dashboard/subconscious/reflect")
async def dashboard_subconscious_reflect():
    """Fuerza un ciclo de reflexión manualmente."""
    new_thoughts = await mind.subconscious.force_reflect()
    return {
        "new_thoughts": new_thoughts,
        "count": len(new_thoughts),
    }


@app.get("/dashboard/system")
async def dashboard_system():
    """Info del sistema en vivo."""
    result = await mind.skill_executor.execute("system_info", {"action": "overview"})
    return result.get("result", {})


@app.get("/dashboard/screenshot")
async def dashboard_screenshot():
    """Toma screenshot y retorna metadata + base64 thumbnail."""
    result = await mind.skill_executor.execute("vision", {"action": "screenshot"})
    if result.get("success"):
        # Leer thumbnail para enviar al frontend
        from PIL import Image
        import io

        path = result["result"]["path"]
        img = Image.open(path)
        img.thumbnail((640, 360))
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=60)
        b64 = base64.b64encode(buf.getvalue()).decode()
        return {
            **result["result"],
            "thumbnail_b64": b64,
        }
    return result


@app.get("/dashboard/windows")
async def dashboard_windows():
    """Lista ventanas abiertas."""
    result = await mind.skill_executor.execute("ui_automation", {"action": "window", "sub_action": "list"})
    return result.get("result", {})


@app.get("/dashboard/monitor")
async def dashboard_monitor():
    """Estado del monitor: métricas en vivo + alertas."""
    result = await mind.skill_executor.execute("monitor", {"action": "status"})
    return result.get("result", {})


@app.get("/dashboard/quick-metrics")
async def dashboard_quick_metrics():
    """Lightweight snapshot for HomePanel — no history, no top processes."""
    result = await mind.skill_executor.execute("monitor", {"action": "snapshot"})
    data = result.get("result", {})
    return {
        "cpu_percent": data.get("cpu_percent"),
        "memory_percent": data.get("ram_percent"),
        "disk_percent": data.get("disk_percent"),
        "gpu_percent": data.get("gpu_percent"),
        "cpu_temp": data.get("cpu_temp"),
        "net_speed_mbps": data.get("net_speed_mbps"),
        "process_count": data.get("process_count"),
        "uptime_seconds": data.get("uptime_seconds"),
    }


@app.post("/dashboard/monitor/start")
async def dashboard_monitor_start(config: dict = None):
    """Inicia el monitor desde el dashboard."""
    inputs = {"action": "start"}
    if config:
        if "interval" in config:
            inputs["interval"] = config["interval"]
        if "thresholds" in config:
            inputs["thresholds"] = config["thresholds"]
    result = await mind.skill_executor.execute("monitor", inputs)
    return result


@app.post("/dashboard/monitor/stop")
async def dashboard_monitor_stop():
    """Detiene el monitor."""
    result = await mind.skill_executor.execute("monitor", {"action": "stop"})
    return result


# =====================
# Voice API Routes
# =====================


@app.post("/voice/stt")
async def voice_stt(file: UploadFile = File(...)):
    """STT: transcribe audio subido (WAV/WebM/MP3) a texto.
    Envía un multipart/form-data con field 'file' conteniendo el audio."""
    ALLOWED_AUDIO_TYPES = {
        "audio/wav",
        "audio/webm",
        "audio/mp3",
        "audio/mpeg",
        "audio/ogg",
        "audio/x-wav",
        "audio/x-m4a",
        "audio/m4a",
        "audio/aac",
    }
    if file.content_type and file.content_type not in ALLOWED_AUDIO_TYPES:
        return JSONResponse({"error": f"Unsupported audio type: {file.content_type}"}, status_code=400)

    MAX_AUDIO_SIZE = 15 * 1024 * 1024
    contents = await file.read()
    if len(contents) > MAX_AUDIO_SIZE:
        return JSONResponse({"error": "Audio too large (max 15 MB)"}, status_code=413)

    uid = uuid.uuid4().hex
    ext = Path(file.filename).suffix if file.filename else ".wav"
    temp_path = ORIGIN_ROOT / "data" / "audio" / f"stt_{uid}{ext}"
    temp_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        temp_path.write_bytes(contents)
        result = await mind.skill_executor.execute(
            "voice",
            {
                "action": "transcribe",
                "audio_path": str(temp_path),
            },
        )
        if result.get("success"):
            return {
                "text": result["result"].get("text", ""),
                "confidence": result["result"].get("confidence", "medium"),
                "engine": result["result"].get("engine", "whisper"),
                "language": result["result"].get("language", "es"),
            }
        return JSONResponse({"error": result.get("error", "STT failed")}, status_code=500)
    except Exception as e:
        logger.error(f"STT error: {e}")
        return JSONResponse({"error": "STT processing failed"}, status_code=500)
    finally:
        if temp_path.exists():
            temp_path.unlink()


@app.post("/voice/speak")
async def voice_speak(request: dict):
    """TTS: genera audio a partir de texto."""
    text = request.get("text", "")
    if not text:
        return JSONResponse({"error": "text required"}, status_code=400)
    result = await mind.skill_executor.execute(
        "voice",
        {
            "action": "speak",
            "text": text,
            "preset": request.get("preset", "origin"),
            "save_only": True,  # Don't auto-play, let frontend handle
        },
    )
    if result.get("success"):
        audio_path = result["result"].get("audio_path")
        if audio_path:
            with open(audio_path, "rb") as f:
                audio_b64 = base64.b64encode(f.read()).decode()
            return {
                "audio_b64": audio_b64,
                "format": "mp3",
                "voice": result["result"].get("voice"),
                "text_length": len(text),
            }
    return JSONResponse({"error": result.get("error", "TTS failed")}, status_code=500)


@app.get("/voice/status")
async def voice_status():
    """Estado del sistema de voz."""
    result = await mind.skill_executor.execute("voice", {"action": "status"})
    return result.get("result", {})


@app.websocket("/ws/voice")
async def websocket_voice(websocket: WebSocket):
    """
    WebSocket para modo conversación por voz.

    Client envía: {"type": "voice_input", "text": "..."}  (texto del STT del browser)
    Server responde: {"type": "voice_answer", "text": "...", "audio_b64": "..."}
    """
    await websocket.accept()
    client_ip = websocket.client.host if websocket.client else "unknown"
    consecutive_rate_violations = 0
    logger.info("Voice WebSocket connected")

    try:
        while True:
            data = await websocket.receive_json()

            # SECURITY: Rate limit
            if not ws_rate_limiter.is_allowed(client_ip):
                consecutive_rate_violations += 1
                if consecutive_rate_violations >= 5:
                    logger.warning(f"Voice WS rate limit: closing {client_ip}")
                    await websocket.send_json({"type": "error", "message": "Rate limit exceeded. Connection closed."})
                    break
                await websocket.send_json({"type": "error", "message": "Rate limit exceeded. Slow down."})
                continue
            consecutive_rate_violations = 0

            if data.get("type") == "voice_input":
                user_text = data.get("text", "")
                if not user_text or not isinstance(user_text, str):
                    continue

                # SECURITY: Input length check
                if len(user_text) > MAX_INPUT_LENGTH:
                    await websocket.send_json({"type": "error", "message": "Input too long."})
                    continue

                logger.info(f"Voice input: {user_text[:50]}")

                # SECURITY: Prompt injection check (voice transcripts too)
                guard_result = mind.injection_guard.analyze(user_text)
                if guard_result.is_blocked:
                    logger.warning(f"Voice input BLOCKED: score={guard_result.risk_score:.2f}")
                    await websocket.send_json(
                        {
                            "type": "voice_answer",
                            "text": "Mensaje bloqueado por seguridad.",
                            "audio_b64": None,
                            "blocked": True,
                        }
                    )
                    continue

                # Send thinking status
                await websocket.send_json(
                    {
                        "type": "status",
                        "message": "Procesando...",
                    }
                )

                # Run reasoning loop
                import time as _time
                _t0 = _time.perf_counter()
                result = await mind.think(user_text)
                _latency_ms = (_time.perf_counter() - _t0) * 1000.0
                answer = result.final_answer
                llm_metrics = getattr(mind.responder, "last_llm_result", {}) or {}

                # Generate TTS audio
                tts_result = await mind.skill_executor.execute(
                    "voice",
                    {
                        "action": "speak",
                        "text": answer,
                        "save_only": True,
                    },
                )

                audio_b64 = None
                if tts_result.get("success"):
                    audio_path = tts_result["result"].get("audio_path")
                    if audio_path:
                        with open(audio_path, "rb") as f:
                            audio_b64 = base64.b64encode(f.read()).decode()

                # Send answer + audio
                await websocket.send_json(
                    {
                        "type": "voice_answer",
                        "text": answer,
                        "audio_b64": audio_b64,
                        "cycle_id": result.cycle_id,
                        "latency_ms": round(_latency_ms, 1),
                        "llm_latency_ms": llm_metrics.get("latency_ms"),
                        "completion_tokens": llm_metrics.get("completion_tokens"),
                        "tokens_per_s": llm_metrics.get("tokens_per_s"),
                        "provider": llm_metrics.get("provider"),
                    }
                )

    except WebSocketDisconnect:
        logger.info("Voice WebSocket disconnected")
    except Exception as e:
        logger.error(f"Voice WS error: {e}")
        try:
            # SECURITY: Don't expose internal errors
            await websocket.send_json({"type": "error", "message": "Internal error."})
        except Exception:
            pass


# =====================
# Voice Streaming WebSocket (real-time conversation)
# =====================


@app.websocket("/ws/voice/stream")
async def websocket_voice_stream(websocket: WebSocket):
    """Real-time bidirectional voice conversation.

    Protocolo:
      Client → Server (binary):  PCM 16kHz mono int16 audio chunks
      Client → Server (json):    {type: "mute"|"unmute"|"interrupt"|"set_language", ...}
      Server → Client (json):    voice_state, voice_transcript, voice_answer,
                                  voice_audio_start, voice_audio_chunk (b64),
                                  voice_audio_end, voice_audio_cancel, voice_error
    """
    await websocket.accept()
    client_ip = websocket.client.host if websocket.client else "unknown"
    logger.info(f"Voice streaming WS connected from {client_ip}")

    # Build session — send_fn writes JSON to this websocket
    async def _send(msg: dict):
        try:
            await websocket.send_json(msg)
        except Exception:
            pass

    session = VoiceSession(
        send_fn=_send,
        mind=mind,
        skill_executor=mind.skill_executor,
        language="es",
    )
    await session.start()

    try:
        while True:
            msg = await websocket.receive()
            mtype = msg.get("type")

            if mtype == "websocket.disconnect":
                break

            # Binary audio chunk — NOT rate-limited (streaming generates ~480 frames/min)
            if "bytes" in msg and msg["bytes"] is not None:
                await session.add_audio_chunk(msg["bytes"])
                continue

            # JSON control message — rate-limit control commands, not audio
            if "text" in msg and msg["text"] is not None:
                import json as _json

                try:
                    data = _json.loads(msg["text"])
                except Exception:
                    continue

                cmd = data.get("type", "")
                if cmd == "mute":
                    await session.set_muted(True)
                elif cmd == "unmute":
                    await session.set_muted(False)
                elif cmd == "interrupt":
                    await session.interrupt()
                elif cmd == "set_language":
                    lang = str(data.get("language", "es"))[:8]
                    await session.set_language(lang)
                elif cmd == "set_sensitivity":
                    sensitivity = str(data.get("sensitivity", "low"))[:12]
                    await session.set_sensitivity(sensitivity)
                elif cmd == "ping":
                    await websocket.send_json({"type": "pong", "state": session.state})
                elif cmd == "get_stats":
                    await websocket.send_json(
                        {
                            "type": "voice_stats",
                            "stats": session.stats.to_dict(),
                            "state": session.state,
                        }
                    )

    except WebSocketDisconnect:
        logger.info("Voice streaming WS disconnected")
    except Exception as e:
        logger.error(f"Voice streaming WS error: {e}")
        try:
            await websocket.send_json({"type": "voice_error", "message": "Internal error"})
        except Exception:
            pass
    finally:
        await session.close()


@app.get("/voice/stream/info")
async def voice_stream_info():
    """Info about the streaming voice protocol (config exposed to client)."""
    from core.voice_session import (
        SAMPLE_RATE,
        CHANNELS,
        FRAME_DURATION_MS,
        SILENCE_TIMEOUT_MS,
        MIN_UTTERANCE_MS,
        MAX_UTTERANCE_MS,
        ENERGY_THRESHOLD,
        ENERGY_FALLBACK_THRESHOLD,
        BARGE_IN_THRESHOLD,
        VAD_AGGRESSIVENESS,
    )

    return {
        "sample_rate": SAMPLE_RATE,
        "channels": CHANNELS,
        "frame_ms": FRAME_DURATION_MS,
        "silence_timeout_ms": SILENCE_TIMEOUT_MS,
        "min_utterance_ms": MIN_UTTERANCE_MS,
        "max_utterance_ms": MAX_UTTERANCE_MS,
        "vad_aggressiveness": VAD_AGGRESSIVENESS,
        "energy_threshold": ENERGY_THRESHOLD,
        "energy_fallback_threshold": ENERGY_FALLBACK_THRESHOLD,
        "barge_in_threshold": BARGE_IN_THRESHOLD,
        "sensitivity_default": "low",
        "endpoint": "/ws/voice/stream",
        "vad_enabled": True,
    }


# =====================
# Telegram API Routes
# =====================


@app.get("/telegram/status")
async def telegram_status():
    """Estado del bot de Telegram."""
    tg = mind.skill_executor.get("telegram")
    if not tg:
        return {"configured": False, "error": "TelegramSkill not available"}
    result = await tg.execute({"action": "status"})
    return result.get("result", {})


@app.post("/telegram/start")
async def telegram_start():
    """Inicia el bot de Telegram."""
    tg = mind.skill_executor.get("telegram")
    if not tg:
        return JSONResponse({"error": "TelegramSkill not available"}, status_code=503)
    tg.set_mind(mind)
    voice = mind.skill_executor.get("voice")
    if voice:
        tg.set_voice_skill(voice)
    result = await tg.execute({"action": "start"})
    return result


@app.post("/telegram/stop")
async def telegram_stop():
    """Detiene el bot de Telegram."""
    tg = mind.skill_executor.get("telegram")
    if not tg:
        return JSONResponse({"error": "TelegramSkill not available"}, status_code=503)
    result = await tg.execute({"action": "stop"})
    return result


@app.post("/telegram/send")
async def telegram_send(request: dict):
    """Envia un mensaje al usuario via Telegram."""
    tg = mind.skill_executor.get("telegram")
    if not tg:
        return JSONResponse({"error": "TelegramSkill not available"}, status_code=503)
    text = request.get("text", "")
    if not text:
        return JSONResponse({"error": "text required"}, status_code=400)
    result = await tg.execute({"action": "send", "text": text, "parse_mode": request.get("parse_mode")})
    return result


# =====================
# Wake Word API Routes
# =====================


@app.post("/voice/wake-word/start")
async def wake_word_start(request: dict = None):
    """Inicia el daemon de wake word (siempre-escuchando)."""
    import asyncio

    wake = mind.skill_executor.get("wake_word")
    if not wake:
        return JSONResponse({"error": "WakeWord skill not available"}, status_code=503)

    voice = mind.skill_executor.get("voice")
    loop = asyncio.get_event_loop()

    # Inject dependencies (idempotent)
    wake.set_dependencies(loop, mind, voice)
    wake.set_broadcast(manager.broadcast)

    inputs = {"action": "start"}
    if request:
        inputs.update({k: v for k, v in request.items() if k in ("language", "energy_threshold")})

    result = await wake.execute(inputs)
    return result


@app.post("/voice/wake-word/stop")
async def wake_word_stop():
    """Detiene el daemon de wake word."""
    wake = mind.skill_executor.get("wake_word")
    if not wake:
        return JSONResponse({"error": "WakeWord skill not available"}, status_code=503)
    result = await wake.execute({"action": "stop"})
    return result


@app.get("/voice/wake-word/status")
async def wake_word_status():
    """Estado del daemon de wake word."""
    wake = mind.skill_executor.get("wake_word")
    if not wake:
        return {"running": False, "error": "WakeWord skill not available"}
    result = await wake.execute({"action": "status"})
    return result.get("result", {})


# =====================
# Camera API Routes
# =====================


@app.post("/camera/analyze")
async def camera_analyze(request: dict):
    """
    Analiza un frame de cámara enviado desde el browser.
    Body: { "frame_b64": "...", "question": "..." }
    """
    frame_b64 = request.get("frame_b64", "")
    if not frame_b64:
        return JSONResponse({"error": "frame_b64 required"}, status_code=400)

    # SECURITY: validate base64 and size
    try:
        raw = base64.b64decode(frame_b64)
    except Exception:
        return JSONResponse({"error": "Invalid base64"}, status_code=400)

    if len(raw) > 15 * 1024 * 1024:
        return JSONResponse({"error": "Frame too large (max 15 MB)"}, status_code=413)

    result = await mind.skill_executor.execute(
        "camera",
        {
            "action": "analyze_frame",
            "frame_b64": frame_b64,
            "question": request.get("question", "¿Qué ves? Describe en detalle."),
        },
    )
    return result


@app.post("/camera/capture")
async def camera_capture():
    """Toma una foto con la webcam del servidor."""
    result = await mind.skill_executor.execute("camera", {"action": "capture"})
    # Remove heavy base64 from response unless explicitly requested
    if result.get("result"):
        result["result"].pop("frame_b64", None)
    return result


@app.get("/camera/status")
async def camera_status():
    """Estado del módulo de cámara."""
    result = await mind.skill_executor.execute("camera", {"action": "status"})
    return result.get("result", {})


# =====================
# System Tray API Routes
# =====================


@app.post("/system/tray/start")
async def tray_start():
    """Inicia el icono de bandeja del sistema y el hotkey global Ctrl+Alt+J."""
    tray = mind.skill_executor.get("system_tray")
    if not tray:
        return JSONResponse({"error": "SystemTray skill not available"}, status_code=503)

    wake = mind.skill_executor.get("wake_word")
    if wake:
        tray.set_wake_word_skill(wake)

    result = await tray.execute({"action": "start"})
    return result


@app.post("/system/tray/stop")
async def tray_stop():
    """Detiene el icono de bandeja."""
    tray = mind.skill_executor.get("system_tray")
    if not tray:
        return JSONResponse({"error": "SystemTray skill not available"}, status_code=503)
    result = await tray.execute({"action": "stop"})
    return result


@app.get("/system/tray/status")
async def tray_status():
    """Estado del tray."""
    tray = mind.skill_executor.get("system_tray")
    if not tray:
        return {"running": False}
    result = await tray.execute({"action": "status"})
    return result.get("result", {})


@app.post("/system/startup")
async def set_startup(request: dict):
    """Configura arranque automático con Windows."""
    tray = mind.skill_executor.get("system_tray")
    if not tray:
        return JSONResponse({"error": "SystemTray skill not available"}, status_code=503)
    result = await tray.execute({"action": "set_startup", "enable": request.get("enable", True)})
    return result


# =====================
# OS Control API Routes
# =====================


@app.post("/os/volume")
async def os_volume(request: dict):
    """Control de volumen: op = get|set|mute|unmute|toggle_mute|up|down"""
    inputs = {"action": "volume", **request}
    return await mind.skill_executor.execute("os_control", inputs)


@app.post("/os/brightness")
async def os_brightness(request: dict):
    """Control de brillo: op = get|set|up|down|list"""
    inputs = {"action": "brightness", **request}
    return await mind.skill_executor.execute("os_control", inputs)


@app.post("/os/power/{action}")
async def os_power(action: str, request: dict = None):
    """Energía: lock | sleep | hibernate | restart | shutdown."""
    valid = {"lock", "sleep", "hibernate", "restart", "shutdown"}
    if action not in valid:
        return JSONResponse({"error": f"Action must be one of {valid}"}, status_code=400)
    inputs = {"action": action}
    if request:
        inputs.update(request)
    return await mind.skill_executor.execute("os_control", inputs)


@app.get("/os/battery")
async def os_battery():
    """Estado de batería."""
    r = await mind.skill_executor.execute("os_control", {"action": "battery"})
    return r.get("result", {})


@app.get("/os/network")
async def os_network():
    """Info de red actual."""
    r = await mind.skill_executor.execute("os_control", {"action": "network"})
    return r.get("result", {})


@app.get("/os/wifi")
async def os_wifi_list():
    """Escanea redes WiFi disponibles."""
    r = await mind.skill_executor.execute("os_control", {"action": "wifi_list"})
    return r.get("result", {})


@app.post("/os/wifi/connect")
async def os_wifi_connect(request: dict):
    """Conecta a una red WiFi guardada por SSID."""
    inputs = {"action": "wifi_connect", **request}
    return await mind.skill_executor.execute("os_control", inputs)


@app.get("/os/displays")
async def os_displays():
    """Info de monitores."""
    r = await mind.skill_executor.execute("os_control", {"action": "displays"})
    return r.get("result", {})


@app.get("/os/audio")
async def os_audio_devices():
    """Sesiones de audio activas."""
    r = await mind.skill_executor.execute("os_control", {"action": "audio_devices"})
    return r.get("result", {})


@app.get("/os/idle")
async def os_idle():
    """Tiempo desde la última actividad del usuario."""
    r = await mind.skill_executor.execute("os_control", {"action": "idle_time"})
    return r.get("result", {})


# =====================
# Clipboard API Routes
# =====================


@app.get("/clipboard/read")
async def clipboard_read():
    """Lee el contenido actual del portapapeles."""
    r = await mind.skill_executor.execute("clipboard", {"action": "read"})
    return r.get("result", {})


@app.post("/clipboard/write")
async def clipboard_write(request: dict):
    """Escribe texto en el portapapeles."""
    inputs = {"action": "write", **request}
    return await mind.skill_executor.execute("clipboard", inputs)


@app.get("/clipboard/history")
async def clipboard_history(limit: int = 10):
    """Historial reciente de cosas copiadas."""
    r = await mind.skill_executor.execute("clipboard", {"action": "history", "limit": limit})
    return r.get("result", {})


# =====================
# Active Window API Routes
# =====================


@app.get("/window/current")
async def window_current():
    """Ventana en foco ahora mismo (proceso + título)."""
    r = await mind.skill_executor.execute("active_window", {"action": "current"})
    return r.get("result", {})


@app.get("/window/history")
async def window_history(limit: int = 20):
    """Historial de ventanas activas."""
    r = await mind.skill_executor.execute("active_window", {"action": "history", "limit": limit})
    return r.get("result", {})


@app.get("/window/time")
async def window_time_by_app():
    """Tiempo acumulado por aplicación en la sesión."""
    r = await mind.skill_executor.execute("active_window", {"action": "time_by_app"})
    return r.get("result", {})


# =====================
# MCP (Model Context Protocol) Routes
# =====================


@app.get("/mcp/tools/list")
async def mcp_tools_list():
    """Lista herramientas disponibles vía MCP."""
    return {"tools": mcp_server.list_tools()}


@app.post("/mcp/tools/call")
async def mcp_tools_call(request: dict):
    """Ejecuta una herramienta MCP."""
    name = request.get("name", "")
    arguments = request.get("arguments", {})
    if not name:
        return JSONResponse({"error": "tool name required"}, status_code=400)
    result = await mcp_server.call_tool(name, arguments)
    return result


# =====================
# Config Hot-Reload
# =====================


@app.post("/core/refresh")
async def core_refresh():
    """Recarga configuración y reinicia componentes sin detener el servidor."""
    try:
        from core.config import config_manager

        config_manager._load_configs()
        logger.info("Configuration reloaded")
        return {"status": "success", "message": "Configuration refreshed"}
    except Exception as e:
        logger.error(f"Config refresh failed: {e}")
        return JSONResponse({"error": "Configuration refresh failed"}, status_code=500)


@app.get("/core/status")
async def core_status():
    """Estado de todos los componentes del sistema."""
    return {
        "llm_router": mind.llm_router.get_available_providers(),
        "skills": len(mind.available_skills),
        "memories": len(mind.memory_mgr.memories),
        "memory_tree": mind.memory_tree.get_stats(),
        "subconscious": mind.subconscious.stats,
        "injection_guard": mind.injection_guard.stats,
        "token_juice": mind.token_juice.stats,
        "service_hub": mind.service_hub.stats,
        "proactive": mind.proactive.stats,
        "services_graph": mind.services_graph.stats,
        "auto_context": mind.auto_context.stats,
        "db_available": mind.persistence._db_available,
        "hooks": mind.skill_executor.plugin_manager.get_registered_hooks(),
        "cache": (lambda: __import__("core.cache", fromlist=["cache"]).cache.stats())(),
    }


@app.get("/core/version")
async def core_version():
    """Versión del sistema."""
    return {"version": "1.0.0", "name": "Origin"}


# =====================
# Agent Workspace Routes
# =====================

from skills.agent_workspace import AgentWorkspace  # noqa: E402

agent_workspace = AgentWorkspace()


@app.get("/workspace/tasks")
async def workspace_tasks(status: str = "", limit: int = 20):
    """Lista tareas del Agent Workspace."""
    return {
        "tasks": agent_workspace.list_tasks(limit=limit, status_filter=status or None),
        "stats": agent_workspace.get_stats(),
    }


@app.get("/workspace/tasks/{task_id}")
async def workspace_task(task_id: str):
    """Estado de una tarea específica."""
    task = agent_workspace.get_task(task_id)
    if not task:
        return JSONResponse({"error": "Task not found"}, status_code=404)
    return task.to_dict()


@app.post("/workspace/tasks/{task_id}/cancel")
async def workspace_cancel(task_id: str):
    """Cancela una tarea en ejecución."""
    cancelled = agent_workspace.cancel_task(task_id)
    return {"cancelled": cancelled}


# =====================
# Cron Routes
# =====================


@app.get("/cron/jobs")
async def cron_jobs():
    """Lista tareas programadas."""
    return {"jobs": cron.list_jobs()}


@app.post("/cron/jobs")
async def cron_add_job(request: dict):
    """Añade una tarea programada.
    Body: {"name": "...", "expression": "*/5 * * * *", "skill_name": "...", "inputs": {}}
    """
    name = request.get("name", "")
    expression = request.get("expression", "")
    skill_name = request.get("skill_name", "")
    inputs = request.get("inputs", {})
    if not all([name, expression, skill_name]):
        return JSONResponse({"error": "name, expression, y skill_name son requeridos"}, status_code=400)
    job = cron.add_job(name, expression, skill_name, inputs)
    if job:
        return job.to_dict()
    return JSONResponse({"error": "Expresión cron inválida"}, status_code=400)


@app.delete("/cron/jobs/{job_id}")
async def cron_remove_job(job_id: str):
    """Elimina una tarea programada."""
    removed = cron.remove_job(job_id)
    return {"removed": removed}


@app.post("/cron/jobs/{job_id}/pause")
async def cron_pause_job(job_id: str):
    """Pausa una tarea."""
    return {"paused": cron.pause_job(job_id)}


@app.post("/cron/jobs/{job_id}/resume")
async def cron_resume_job(job_id: str):
    """Reanuda una tarea."""
    return {"resumed": cron.resume_job(job_id)}


@app.get("/cron/history")
async def cron_history(limit: int = 20):
    """Historial de ejecuciones."""
    return {"history": cron.get_history(limit=limit)}


# =====================
# Context Routes
# =====================


@app.get("/context")
async def context_list():
    """Lista archivos de contexto."""
    return {"contexts": mind.context_manager.list_contexts()}


@app.get("/context/{filename}")
async def context_get(filename: str):
    """Obtiene contenido de un archivo de contexto."""
    content = mind.context_manager.get(filename)
    if content is None:
        return JSONResponse({"error": "Context not found"}, status_code=404)
    return {"filename": filename, "content": content}


@app.post("/context/{filename}")
async def context_set(filename: str, request: dict):
    """Crea o actualiza un archivo de contexto."""
    content = request.get("content", "")
    if not content:
        return JSONResponse({"error": "content required"}, status_code=400)
    safe = filename.replace("..", "").replace("/", "").replace("\\", "")
    if not safe.endswith((".md", ".txt")):
        safe += ".md"
    mind.context_manager.set_context(safe, content)
    return {"filename": safe, "saved": True, "chars": len(content)}


@app.delete("/context/{filename}")
async def context_delete(filename: str):
    """Elimina un archivo de contexto."""
    deleted = mind.context_manager.delete_context(filename)
    return {"deleted": deleted}


# =====================
# SubAgent Routes
# =====================


@app.post("/subagent/delegate")
async def subagent_delegate(request: dict):
    """Delega una tarea a un sub-agente."""
    instruction = request.get("instruction", "")
    if not instruction:
        return JSONResponse({"error": "instruction required"}, status_code=400)
    task = mind.sub_agent.delegate(instruction)
    return {"task_id": task.task_id, "instruction": instruction, "status": "pending"}


@app.post("/subagent/gather")
async def subagent_gather():
    """Ejecuta todas las tareas delegadas pendientes en paralelo."""

    tasks = mind.sub_agent.get_all_tasks()
    pending = [t for t in tasks if t.status == "pending"]
    if not pending:
        return {"message": "No pending tasks", "results": []}
    results = await mind.sub_agent.gather(pending)
    return {
        "completed": len(results),
        "results": [
            {
                "task_id": r.task_id,
                "instruction": r.instruction[:80],
                "status": r.status,
                "answer": r.result.get("final_answer", "")[:200] if r.result else None,
                "error": r.error,
            }
            for r in results
        ],
    }


@app.get("/subagent/tasks")
async def subagent_tasks():
    """Lista todas las tareas de sub-agentes."""
    tasks = mind.sub_agent.get_all_tasks()
    return {
        "tasks": [
            {
                "task_id": t.task_id,
                "instruction": t.instruction[:80],
                "status": t.status,
                "created_at": t.created_at,
                "completed_at": t.completed_at,
                "execution_time": t.execution_time,
            }
            for t in tasks
        ]
    }


# =====================
# API v1 versioned router (backward-compatible alias)
# All existing routes remain at their unversioned paths.
# New clients can use /v1/<path> for forward-compatibility.
# =====================

from fastapi import APIRouter as _APIRouter  # noqa: E402

_v1 = _APIRouter(prefix="/v1")


@_v1.get("/health")
async def v1_health():
    return await health_check()


@_v1.post("/mind/think")
async def v1_think(request: Request):
    return await think(request)


@_v1.get("/mind/memory/search")
async def v1_memory_search(query: str, top_k: int = 5):
    return await search_memory(query, top_k)


@_v1.post("/auth/register")
async def v1_register(request: dict):
    return await auth_register(request)


@_v1.post("/auth/login")
async def v1_login(request: dict):
    return await auth_login(request)


@_v1.get("/auth/export")
async def v1_export(request: Request):
    return await auth_export_data(request)


app.include_router(_v1)


# =====================
# Frontend (mounted last so API routes take precedence)
# =====================

_FRONTEND_DIST = Path(__file__).resolve().parent.parent / "frontend" / "dist"

if _FRONTEND_DIST.exists() and (_FRONTEND_DIST / "index.html").exists():
    # Serve static assets (CSS, JS) from /assets
    app.mount(
        "/assets",
        StaticFiles(directory=str(_FRONTEND_DIST / "assets")),
        name="assets",
    )

    @app.get("/")
    async def serve_frontend_root():
        return FileResponse(str(_FRONTEND_DIST / "index.html"))

    # SPA catch-all: any unknown route returns index.html (client-side routing)
    @app.get("/{full_path:path}")
    async def serve_spa(full_path: str):
        # API/WS routes already matched above; this is the fallback for SPA
        if full_path.startswith(
            (
                "api/",
                "ws/",
                "mind/",
                "dashboard/",
                "voice/",
                "camera/",
                "system/",
                "os/",
                "clipboard/",
                "window/",
                "health",
                "mcp/",
                "core/",
                "auth/",
                "workspace/",
                "cron/",
                "context/",
                "subagent/",
                "memory_tree/",
                "telegram/",
                "injection_guard/",
                "token_juice/",
                "services",
                "services/",
                "proactive/",
                "workflows",
                "workflows/",
            )
        ):
            return JSONResponse({"error": "Not found"}, status_code=404)
        index = _FRONTEND_DIST / "index.html"
        return FileResponse(str(index))

    logger.info(f"Frontend mounted from {_FRONTEND_DIST}")
else:
    logger.warning(
        f"Frontend dist not found at {_FRONTEND_DIST}. "
        "Run 'npm --prefix frontend run build' to enable the UI on the same port."
    )


if __name__ == "__main__":
    import uvicorn

    # Pass the app object directly (not as a string) so the module isn't
    # re-imported by uvicorn's import machinery. Avoids double initialization
    # and double tray/wake-word startup.
    uvicorn.run(
        app,
        host="127.0.0.1",  # SECURITY: localhost only
        port=9001,
        server_header=False,
    )
