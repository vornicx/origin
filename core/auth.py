"""Autenticación para Origin API.

Tokens HMAC-SHA256 con almacenamiento seguro de contraseñas (bcrypt).
Usuarios persistentes en JSON en data/auth/users.json.
Tokens persistentes en data/auth/tokens.json con expiración.

Mejoras:
- Escritura atómica (tmp + os.replace) para evitar corrupción concurrente.
- Lock en proceso para serializar lecturas/escrituras al JSON.
- HMAC completo (64 hex) con comparación constant-time.
- Garbage collection ligera de tokens expirados en cada verificación.
- Parámetro `remember` propagado desde el login.
- Aviso si admin/origin por defecto sigue activo en producción.
"""

from typing import Dict, Any, Optional
from datetime import datetime, timedelta
import hashlib
import hmac
import json
import logging
import os
import secrets
import threading
import time
import base64

from fastapi import Request, HTTPException, status
from fastapi.security import HTTPBearer

logger = logging.getLogger("origin.auth")

AUTH_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "auth")
AUTH_USERS_FILE = os.path.join(AUTH_DIR, "users.json")
AUTH_TOKENS_FILE = os.path.join(AUTH_DIR, "tokens.json")
TOKEN_EXPIRE_HOURS = 24
TOKEN_EXPIRE_REMEMBER = 720
HMAC_SIG_LEN = 64  # full SHA-256 hex digest
MIN_PASSWORD_LENGTH = 12

os.makedirs(AUTH_DIR, exist_ok=True)

# Cached auto-generated secret (persists for process lifetime)
_AUTO_SECRET: Optional[str] = None
# Serialize concurrent file access from FastAPI workers
_FILE_LOCK = threading.Lock()
# Token cache to avoid hammering disk on every request
_TOKEN_CACHE: Dict[str, Dict[str, Any]] = {}
_TOKEN_CACHE_LOADED = False
# GC throttle: prune expired tokens at most every 60s
_LAST_GC = 0.0
_GC_INTERVAL = 60.0


def _load_json(path: str) -> dict:
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def _save_json_atomic(path: str, data: dict) -> None:
    """Write atomically via tmp + os.replace to avoid partial-write corruption."""
    tmp = f"{path}.tmp.{os.getpid()}"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass


def _load_tokens() -> Dict[str, Dict[str, Any]]:
    global _TOKEN_CACHE, _TOKEN_CACHE_LOADED
    if not _TOKEN_CACHE_LOADED:
        _TOKEN_CACHE = _load_json(AUTH_TOKENS_FILE)
        _TOKEN_CACHE_LOADED = True
    return _TOKEN_CACHE


def _persist_tokens() -> None:
    _save_json_atomic(AUTH_TOKENS_FILE, _TOKEN_CACHE)


def _maybe_gc_tokens() -> None:
    """Drop expired tokens at most every _GC_INTERVAL seconds.

    Tolerates legacy token entries where expiry was stored as ISO string instead
    of unix timestamp (those get pruned as a side effect of normalization).
    """
    global _LAST_GC
    now = time.time()
    if now - _LAST_GC < _GC_INTERVAL:
        return
    _LAST_GC = now
    tokens = _load_tokens()
    now_i = int(now)
    expired = []
    for t, info in tokens.items():
        exp = info.get("expiry", 0)
        if isinstance(exp, str):
            # Legacy ISO-string format — treat as expired so the new flow can rewrite it.
            expired.append(t)
            continue
        try:
            if int(exp) < now_i:
                expired.append(t)
        except (TypeError, ValueError):
            expired.append(t)
    if expired:
        for t in expired:
            tokens.pop(t, None)
        _persist_tokens()
        logger.info(f"Auth GC: pruned {len(expired)} expired/legacy token(s)")


def _hash_password(password: str) -> str:
    """Hash seguro con bcrypt si está disponible, fallback a pbkdf2-sha256."""
    try:
        import bcrypt

        return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
    except ImportError:
        salt = secrets.token_hex(16)
        return hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 600000).hex() + ":" + salt


def _verify_password(password: str, stored: str) -> bool:
    """Verifica contraseña contra hash bcrypt, pbkdf2, o legacy SHA256."""
    if not stored:
        return False
    if stored.startswith("$2b$") or stored.startswith("$2a$"):
        try:
            import bcrypt

            return bcrypt.checkpw(password.encode(), stored.encode())
        except ImportError:
            return False
    if ":" in stored:
        _hash, salt = stored.split(":", 1)
        derived = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 600000).hex()
        return hmac.compare_digest(derived, _hash)
    # Legacy SHA256 (no salt) — constant-time compare to avoid timing leak.
    return hmac.compare_digest(hashlib.sha256(password.encode()).hexdigest(), stored)


def _get_secret() -> str:
    global _AUTO_SECRET
    secret = os.getenv("ORIGIN_JWT_SECRET", "")
    if not secret:
        if _AUTO_SECRET is None:
            _AUTO_SECRET = secrets.token_hex(32)
            logger.warning(
                "ORIGIN_JWT_SECRET not set — using ephemeral in-process secret. "
                "All tokens invalidate on restart. Set ORIGIN_JWT_SECRET in .env for persistence."
            )
        secret = _AUTO_SECRET
    return secret


def _sign(payload: str) -> str:
    return hmac.new(_get_secret().encode(), payload.encode(), hashlib.sha256).hexdigest()


def _create_token(username: str, remember: bool = False) -> str:
    expire = TOKEN_EXPIRE_REMEMBER if remember else TOKEN_EXPIRE_HOURS
    expiry_ts = int((datetime.utcnow() + timedelta(hours=expire)).timestamp())
    payload = f"{username}|{expiry_ts}"
    signature = _sign(payload)  # full 64-char HMAC, not truncated
    raw = f"{payload}|{signature}".encode()
    token_b64 = base64.urlsafe_b64encode(raw).decode().rstrip("=")
    with _FILE_LOCK:
        tokens = _load_tokens()
        tokens[token_b64] = {
            "username": username,
            "expiry": expiry_ts,
            "created": datetime.utcnow().isoformat(),
            "remember": remember,
        }
        _persist_tokens()
    return token_b64


def _verify_token(token_b64: str) -> Optional[str]:
    if not token_b64:
        return None
    try:
        padded = token_b64 + "=" * (-len(token_b64) % 4)
        token = base64.urlsafe_b64decode(padded.encode()).decode()
    except Exception:
        return None
    parts = token.split("|")
    if len(parts) != 3:
        return None
    username, expiry_ts_s, signature = parts
    try:
        expiry_ts = int(expiry_ts_s)
    except ValueError:
        return None
    expected = _sign(f"{username}|{expiry_ts}")
    if not hmac.compare_digest(signature, expected):
        return None
    if int(time.time()) > expiry_ts:
        with _FILE_LOCK:
            tokens = _load_tokens()
            if tokens.pop(token_b64, None) is not None:
                _persist_tokens()
        return None
    _maybe_gc_tokens()
    return username


# ── Public API ──────────────────────────────────────────────

security = HTTPBearer(auto_error=False)


def setup_default_admin():
    """Create admin/origin only if no users exist and explicitly enabled.

    Set ORIGIN_ALLOW_DEFAULT_ADMIN=1 to opt in (dev only). In production,
    register an admin via /auth/register and leave this disabled.
    """
    with _FILE_LOCK:
        users = _load_json(AUTH_USERS_FILE)
    if users:
        return  # Never overwrite real users
    if os.getenv("ORIGIN_ALLOW_DEFAULT_ADMIN", "0") not in ("1", "true", "yes"):
        logger.info(
            "No users registered. Set ORIGIN_ALLOW_DEFAULT_ADMIN=1 to auto-create "
            "admin/origin (dev only) or POST /auth/register to create the first user."
        )
        return
    with _FILE_LOCK:
        users["admin"] = {
            "password": _hash_password("origin"),
            "role": "admin",
            "created": datetime.utcnow().isoformat(),
        }
        _save_json_atomic(AUTH_USERS_FILE, users)
    logger.warning("Default admin user created (admin/origin) — change password immediately.")


def register_user(username: str, password: str, role: str = "user") -> Dict[str, Any]:
    if not username or not password:
        raise HTTPException(status_code=400, detail="Username and password required")
    if len(password) < MIN_PASSWORD_LENGTH:
        raise HTTPException(
            status_code=400,
            detail=f"Password must be at least {MIN_PASSWORD_LENGTH} characters",
        )
    with _FILE_LOCK:
        users = _load_json(AUTH_USERS_FILE)
        if username in users:
            raise HTTPException(status_code=400, detail="Username already exists")
        users[username] = {
            "password": _hash_password(password),
            "role": role,
            "created": datetime.utcnow().isoformat(),
        }
        _save_json_atomic(AUTH_USERS_FILE, users)
    logger.info(f"User '{username}' registered")
    return {"username": username, "role": role}


def authenticate_user(username: str, password: str, remember: bool = False) -> Dict[str, Any]:
    with _FILE_LOCK:
        users = _load_json(AUTH_USERS_FILE)
    user = users.get(username)
    # Always run verify (with a dummy hash if user missing) to avoid timing oracle.
    stored = user["password"] if user else "$2b$12$" + "x" * 53
    valid = _verify_password(password, stored) and user is not None
    if not valid:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
        )
    token = _create_token(username, remember=remember)
    return {
        "access_token": token,
        "token_type": "bearer",
        "username": username,
        "role": user["role"],
        "expires_in_hours": TOKEN_EXPIRE_REMEMBER if remember else TOKEN_EXPIRE_HOURS,
    }


def _extract_bearer(request: Request) -> str:
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        return auth[7:].strip()
    return ""


async def get_current_user(request: Request):
    token = _extract_bearer(request)
    if not token:
        return "anonymous"
    username = _verify_token(token)
    return username if username else "anonymous"


async def require_user(request: Request):
    token = _extract_bearer(request)
    if not token:
        raise HTTPException(status_code=401, detail="Authorization header required")
    username = _verify_token(token)
    if not username:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    return username


def revoke_token(token_b64: str) -> bool:
    """Explicit logout — remove token from store."""
    with _FILE_LOCK:
        tokens = _load_tokens()
        if tokens.pop(token_b64, None) is not None:
            _persist_tokens()
            return True
    return False
