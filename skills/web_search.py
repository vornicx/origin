import httpx
import re
import time
from typing import Dict, Any, List, Optional
from .base_skill import BaseSkill

try:
    from bs4 import BeautifulSoup

    _BS4_AVAILABLE = True
except ImportError:
    _BS4_AVAILABLE = False


class WebSearchSkill(BaseSkill):
    """
    Skill: Búsqueda web con múltiples motores en orden de calidad.

    Prioridad:
      1. Brave Search API (si BRAVE_SEARCH_API_KEY configurado) — mejores resultados
      2. SearXNG (si SEARXNG_URL configurado) — instancia propia, sin límites
      3. DuckDuckGo HTML scraping — fallback sin API key (requiere bs4)
      4. DuckDuckGo Instant Answer API — último recurso (solo respuestas directas)
    """

    DDGO_URL = "https://api.duckduckgo.com/"
    DDGO_HTML_URL = "https://html.duckduckgo.com/html/"
    BRAVE_URL = "https://api.search.brave.com/res/v1/web/search"

    _USER_AGENTS = [
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
    ]
    _ua_idx = 0

    def __init__(self):
        super().__init__(name="web_search", description="Busca información en la web")
        # Import lazily to avoid circular import
        try:
            from core.config import settings as _s

            self._brave_key: str = _s.brave_search_api_key
            self._searxng_url: str = _s.searxng_url
        except Exception:
            self._brave_key = ""
            self._searxng_url = ""

    def _next_ua(self) -> str:
        ua = self._USER_AGENTS[self._ua_idx % len(self._USER_AGENTS)]
        self._ua_idx += 1
        return ua

    def validate_inputs(self, inputs: Dict[str, Any]) -> tuple[bool, str]:
        query = inputs.get("query", "").strip()
        if not query:
            return False, "Se requiere 'query' no vacío"
        if len(query) > 500:
            return False, "Query demasiado largo (máx 500 chars)"
        return True, ""

    async def execute(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        start = time.time()
        self.execution_count += 1

        is_valid, error = self.validate_inputs(inputs)
        if not is_valid:
            return {"success": False, "result": None, "error": error, "execution_time": 0}

        query = inputs["query"].strip()
        max_results = min(inputs.get("max_results", 5), 10)

        results: List[Dict[str, str]] = []
        source_used = "unknown"
        last_error: Optional[str] = None

        # 1. Brave Search API
        if self._brave_key and not results:
            try:
                results = await self._search_brave(query, max_results)
                source_used = "brave"
            except Exception as e:
                last_error = f"Brave: {e}"

        # 2. SearXNG
        if self._searxng_url and not results:
            try:
                results = await self._search_searxng(query, max_results)
                source_used = "searxng"
            except Exception as e:
                last_error = f"SearXNG: {e}"

        # 3. DDG HTML scraping
        if not results and _BS4_AVAILABLE:
            try:
                results = await self._search_ddgo_html(query, max_results)
                source_used = "duckduckgo_html"
            except Exception as e:
                last_error = f"DDG-HTML: {e}"

        # 4. DDG Instant Answer API (last resort)
        if not results:
            try:
                results = await self._search_ddgo_instant(query, max_results)
                source_used = "duckduckgo_instant"
            except Exception as e:
                last_error = f"DDG-Instant: {e}"

        elapsed = round(time.time() - start, 2)
        self.last_execution = __import__("datetime").datetime.now()

        if results:
            return {
                "success": True,
                "result": {
                    "query": query,
                    "results": results,
                    "total_found": len(results),
                    "source": source_used,
                },
                "error": None,
                "execution_time": elapsed,
            }

        return {
            "success": False,
            "result": None,
            "error": last_error or "No results from any search engine",
            "execution_time": elapsed,
        }

    async def _search_brave(self, query: str, max_results: int) -> List[Dict[str, str]]:
        """Brave Search API — best quality, requires API key."""
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.get(
                self.BRAVE_URL,
                params={"q": query, "count": max_results, "safesearch": "moderate"},
                headers={
                    "Accept": "application/json",
                    "Accept-Encoding": "gzip",
                    "X-Subscription-Token": self._brave_key,
                },
            )
            response.raise_for_status()
            data = response.json()

        results = []
        for item in data.get("web", {}).get("results", [])[:max_results]:
            results.append(
                {
                    "type": "result",
                    "title": item.get("title", ""),
                    "snippet": item.get("description", ""),
                    "url": item.get("url", ""),
                    "source": "brave",
                }
            )
        return results

    async def _search_searxng(self, query: str, max_results: int) -> List[Dict[str, str]]:
        """SearXNG — self-hosted, no rate limits."""
        base = self._searxng_url.rstrip("/")
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.get(
                f"{base}/search",
                params={"q": query, "format": "json", "categories": "general"},
                headers={"User-Agent": self._next_ua()},
            )
            response.raise_for_status()
            data = response.json()

        results = []
        for item in data.get("results", [])[:max_results]:
            results.append(
                {
                    "type": "result",
                    "title": item.get("title", ""),
                    "snippet": item.get("content", ""),
                    "url": item.get("url", ""),
                    "source": "searxng",
                }
            )
        return results

    async def _search_ddgo_html(self, query: str, max_results: int) -> List[Dict[str, str]]:
        """DuckDuckGo HTML scraping — no API key needed, works for general queries."""
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
            response = await client.post(
                self.DDGO_HTML_URL,
                data={"q": query, "b": ""},
                headers={
                    "User-Agent": self._next_ua(),
                    "Content-Type": "application/x-www-form-urlencoded",
                    "Accept": "text/html",
                },
            )
            response.raise_for_status()
            html = response.text

        soup = BeautifulSoup(html, "html.parser")
        results = []

        for result_div in soup.select(".result__body, .result")[:max_results * 2]:
            title_tag = result_div.select_one(".result__title a, .result__a")
            snippet_tag = result_div.select_one(".result__snippet")
            if not title_tag:
                continue

            title = title_tag.get_text(strip=True)
            href = title_tag.get("href", "")
            # DDG wraps URLs — extract real URL
            url = self._extract_ddg_url(href)
            snippet = snippet_tag.get_text(strip=True) if snippet_tag else ""

            if url and title:
                results.append(
                    {
                        "type": "result",
                        "title": title,
                        "snippet": snippet,
                        "url": url,
                        "source": "duckduckgo",
                    }
                )
            if len(results) >= max_results:
                break

        return results

    def _extract_ddg_url(self, href: str) -> str:
        """Extract real URL from DDG redirect URL."""
        if not href:
            return ""
        # DDG uses //duckduckgo.com/l/?uddg=<encoded_url>
        match = re.search(r"uddg=([^&]+)", href)
        if match:
            from urllib.parse import unquote

            return unquote(match.group(1))
        # Direct URL
        if href.startswith("http"):
            return href
        return ""

    async def _search_ddgo_instant(self, query: str, max_results: int) -> List[Dict[str, str]]:
        """DuckDuckGo Instant Answer API — only works for factual queries."""
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.get(
                self.DDGO_URL,
                params={"q": query, "format": "json", "no_redirect": "1", "no_html": "1", "skip_disambig": "1"},
                headers={"User-Agent": "Origin/1.0"},
            )
            response.raise_for_status()
            data = response.json()

        results = []
        if data.get("Abstract"):
            results.append(
                {
                    "type": "abstract",
                    "title": data.get("Heading", ""),
                    "snippet": data["Abstract"],
                    "url": data.get("AbstractURL", ""),
                    "source": "duckduckgo_instant",
                }
            )
        for topic in data.get("RelatedTopics", [])[:max_results]:
            if isinstance(topic, dict) and topic.get("Text"):
                results.append(
                    {
                        "type": "topic",
                        "title": topic.get("Text", "").split(" - ")[0],
                        "snippet": topic.get("Text", ""),
                        "url": topic.get("FirstURL", ""),
                        "source": "duckduckgo_instant",
                    }
                )
        for item in data.get("Results", [])[:max_results]:
            results.append(
                {
                    "type": "result",
                    "title": item.get("Text", ""),
                    "snippet": item.get("Text", ""),
                    "url": item.get("FirstURL", ""),
                    "source": "duckduckgo_instant",
                }
            )
        return results[:max_results]

    def get_skill_docs(self) -> Dict[str, Any]:
        engines = ["duckduckgo_html"]
        try:
            from core.config import settings as _s

            if _s.brave_search_api_key:
                engines.insert(0, "brave")
            if _s.searxng_url:
                engines.insert(0, "searxng")
        except Exception:
            pass
        return {
            "description": "Busca información en la web",
            "actions": ["search"],
            "inputs_search": {"query": "string (requerido)", "max_results": "int (default 5, max 10)"},
            "example": {"skill": "web_search", "inputs": {"query": "últimas noticias sobre IA", "max_results": 5}},
            "engines_available": engines,
        }
