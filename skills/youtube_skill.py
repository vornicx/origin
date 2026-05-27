"""
YouTubeSkill — Search and play YouTube videos, get video info.

Actions:
  play     → Search and open the first matching video
  search   → Return search URL for manual selection
  info     → Scrape basic video metadata
  trending → Scrape trending videos for a region
"""

import logging
import re
import time
import webbrowser
import platform
import subprocess
from typing import Dict, Any, Optional
from urllib.parse import quote_plus
from datetime import datetime

from .base_skill import BaseSkill

logger = logging.getLogger("origin.skills")

_YT_VIDEO_FILTER = "EgIQAQ%3D%3D"
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}


def _open_url(url: str) -> None:
    try:
        os_name = platform.system()
        if os_name == "Darwin":
            subprocess.Popen(["open", url])
        elif os_name == "Linux":
            subprocess.Popen(["xdg-open", url])
        else:
            subprocess.Popen(["cmd", "/c", "start", "", url], shell=False)
    except Exception:
        webbrowser.open(url)


def _scrape_first_video(query: str) -> Optional[str]:
    try:
        import requests
    except ImportError:
        return None

    search_url = (
        f"https://www.youtube.com/results"
        f"?search_query={quote_plus(query)}&sp={_YT_VIDEO_FILTER}"
    )
    try:
        r = requests.get(search_url, headers=_HEADERS, timeout=10)
        video_ids = re.findall(r'"videoId":"([A-Za-z0-9_-]{11})"', r.text)
        seen: set[str] = set()
        for vid in video_ids:
            if vid in seen:
                continue
            seen.add(vid)
            if f"/shorts/{vid}" in r.text:
                continue
            return f"https://www.youtube.com/watch?v={vid}"
    except Exception as e:
        logger.warning(f"YouTube scrape failed: {e}")
    return None


def _scrape_video_info(video_id: str) -> dict:
    try:
        import requests
    except ImportError:
        return {}
    url = f"https://www.youtube.com/watch?v={video_id}"
    try:
        r = requests.get(url, headers=_HEADERS, timeout=12)
        info: dict[str, str] = {}
        for key, pattern in [
            ("title", r'"title":\{"runs":\[\{"text":"([^"]+)"'),
            ("channel", r'"ownerChannelName":"([^"]+)"'),
            ("views", r'"viewCount":"(\d+)"'),
            ("duration", r'"lengthSeconds":"(\d+)"'),
        ]:
            match = re.search(pattern, r.text)
            if match:
                raw = match.group(1)
                if key == "views":
                    info[key] = f"{int(raw):,}"
                elif key == "duration":
                    secs = int(raw)
                    info[key] = f"{secs // 60}:{secs % 60:02d}"
                else:
                    info[key] = raw
        return info
    except Exception as e:
        logger.warning(f"YouTube info scrape failed: {e}")
        return {}


class YouTubeSkill(BaseSkill):

    def __init__(self):
        super().__init__(
            name="youtube",
            description="Busca y reproduce videos de YouTube, obtiene info y tendencias",
        )

    def validate_inputs(self, inputs: Dict[str, Any]) -> tuple[bool, str]:
        action = inputs.get("action", "play")
        valid = ("play", "search", "info", "trending")
        if action not in valid:
            return False, f"Acción inválida: '{action}'. Válidas: {valid}"
        if action in ("play", "search") and not inputs.get("query", "").strip():
            return False, "Se requiere 'query' para play/search"
        if action == "info" and not inputs.get("url", "").strip():
            return False, "Se requiere 'url' para info"
        return True, ""

    async def execute(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        is_valid, error_msg = self.validate_inputs(inputs)
        if not is_valid:
            return {"success": False, "result": None, "error": error_msg, "execution_time": 0}

        start = time.time()
        action = inputs.get("action", "play")

        try:
            if action == "play":
                result = self._play(inputs)
            elif action == "search":
                result = self._search(inputs)
            elif action == "info":
                result = self._info(inputs)
            elif action == "trending":
                result = self._trending(inputs)
            else:
                result = {"error": f"Unknown action: {action}"}

            self.execution_count += 1
            self.last_execution = datetime.now()
            success = "error" not in result
            return {
                "success": success,
                "result": result,
                "error": result.get("error"),
                "execution_time": round(time.time() - start, 3),
            }
        except Exception as e:
            logger.error(f"YouTube skill error: {e}")
            return {"success": False, "result": None, "error": str(e), "execution_time": round(time.time() - start, 3)}

    def _play(self, inputs: dict) -> dict:
        query = inputs["query"].strip()
        video_url = _scrape_first_video(query)
        if video_url:
            _open_url(video_url)
            return {"action": "play", "query": query, "url": video_url, "message": f"Reproduciendo: {query}"}
        fallback = f"https://www.youtube.com/results?search_query={quote_plus(query)}&sp={_YT_VIDEO_FILTER}"
        _open_url(fallback)
        return {"action": "play", "query": query, "url": fallback, "message": f"Búsqueda abierta para: {query}"}

    def _search(self, inputs: dict) -> dict:
        query = inputs["query"].strip()
        url = f"https://www.youtube.com/results?search_query={quote_plus(query)}&sp={_YT_VIDEO_FILTER}"
        _open_url(url)
        return {"action": "search", "query": query, "url": url}

    def _info(self, inputs: dict) -> dict:
        url = inputs["url"].strip()
        match = re.search(r"(?:v=|\/v\/|youtu\.be\/|\/embed\/|\/shorts\/)([A-Za-z0-9_-]{11})", url)
        if not match:
            return {"error": "No se pudo extraer el video ID de la URL"}
        info = _scrape_video_info(match.group(1))
        if not info:
            return {"error": "No se pudo obtener info del video"}
        return {"action": "info", "url": url, **info}

    def _trending(self, inputs: dict) -> dict:
        region = inputs.get("region", "US").upper()
        try:
            import requests
            url = f"https://www.youtube.com/feed/trending?gl={region}"
            r = requests.get(url, headers=_HEADERS, timeout=12)
            titles = re.findall(r'"title":\{"runs":\[\{"text":"([^"]+)"\}\]', r.text)
            channels = re.findall(r'"ownerText":\{"runs":\[\{"text":"([^"]+)"', r.text)
            results = []
            seen: set[str] = set()
            for i, title in enumerate(titles):
                if title in seen or len(title) < 5:
                    continue
                seen.add(title)
                channel = channels[i] if i < len(channels) else "Unknown"
                results.append({"rank": len(results) + 1, "title": title, "channel": channel})
                if len(results) >= 8:
                    break
            return {"action": "trending", "region": region, "videos": results}
        except ImportError:
            return {"error": "requests no está instalado"}
        except Exception as e:
            return {"error": f"Error obteniendo tendencias: {e}"}
