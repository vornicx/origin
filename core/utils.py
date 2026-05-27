"""Utilidades del Core"""

import json
import logging
import os
import traceback
from typing import Any, Dict
from datetime import datetime, timezone


class _JSONFormatter(logging.Formatter):
    """Compact JSON log formatter. One JSON object per line."""

    def format(self, record: logging.LogRecord) -> str:
        entry: Dict[str, Any] = {
            "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z",
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if record.exc_info:
            entry["exc"] = traceback.format_exception(*record.exc_info)[-1].strip()
        if extra := {k: v for k, v in record.__dict__.items()
                     if k not in logging.LogRecord.__dict__ and not k.startswith("_")}:
            entry["extra"] = extra
        return json.dumps(entry, ensure_ascii=False)


def setup_logging(level: str = "INFO") -> logging.Logger:
    """Configura logging para el core.

    Set LOG_FORMAT=json in env for structured JSON output (recommended in prod).
    Default is human-readable text for local dev.
    """
    root = logging.getLogger()
    if root.handlers:
        root.handlers.clear()

    use_json = os.getenv("LOG_FORMAT", "").lower() == "json"
    if use_json:
        handler = logging.StreamHandler()
        handler.setFormatter(_JSONFormatter())
    else:
        handler = logging.StreamHandler()
        handler.setFormatter(
            logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
        )

    root.addHandler(handler)
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
    return logging.getLogger("origin.core")


def safe_json_serialize(obj: Any) -> str:
    """Serializa objetos a JSON de forma segura"""
    return json.dumps(obj, default=str, indent=2)


def extract_json_from_text(text: str) -> Dict[str, Any]:
    """Extrae JSON objeto de texto. Soporta bloques ```json ... ``` y texto libre."""
    # Intenta parsear directo
    try:
        return json.loads(text)
    except Exception:
        pass
    # Busca bloque markdown ```json
    if "```" in text:
        start = text.find("```")
        end = text.rfind("```")
        if start != end:
            inner = text[start + 3 : end].strip()
            if inner.startswith("json"):
                inner = inner[4:].strip()
            try:
                return json.loads(inner)
            except Exception:
                pass
    # Busca { ... }
    try:
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end > start:
            return json.loads(text[start : end + 1])
    except Exception:
        pass
    return {}


def extract_json_array_from_text(text: str) -> list:
    """Extrae JSON array de texto. Soporta bloques ```json ... ``` y texto libre."""
    try:
        return json.loads(text)
    except Exception:
        pass
    if "```" in text:
        start = text.find("```")
        end = text.rfind("```")
        if start != end:
            inner = text[start + 3 : end].strip()
            if inner.startswith("json"):
                inner = inner[4:].strip()
            try:
                parsed = json.loads(inner)
                return parsed if isinstance(parsed, list) else []
            except Exception:
                pass
    try:
        start = text.find("[")
        end = text.rfind("]")
        if start != -1 and end > start:
            return json.loads(text[start : end + 1])
    except Exception:
        pass
    return []


def format_timestamp(dt: datetime = None) -> str:
    """Formatea timestamp"""
    if dt is None:
        dt = datetime.now()
    return dt.isoformat()
