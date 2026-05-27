"""
Web Scraper Skill para Origin.
Extracción estructurada de datos web: texto, enlaces, tablas, metadatos.
Inspirado en técnicas de scraping adaptativo, construido desde cero.
"""

import time
import re
import random
import hashlib
import logging
from typing import Dict, Any, Optional
from datetime import datetime
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup, Comment

from .base_skill import BaseSkill

logger = logging.getLogger("origin.skills.web_scraper")

# ── User-Agent pool realista ───────────────────────────────────────
_USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:128.0) Gecko/20100101 Firefox/128.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Safari/605.1.15",  # noqa: E501
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36 Edg/125.0.0.0",  # noqa: E501
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
]

# ── Tags basura que se eliminan antes de extraer contenido ─────────
_JUNK_TAGS = [
    "script",
    "style",
    "noscript",
    "iframe",
    "svg",
    "header",
    "footer",
    "nav",
    "aside",
    "form",
]

# ── Dominios bloqueados por seguridad ──────────────────────────────
_BLOCKED_PATTERNS = [
    r"^file://",
    r"^ftp://",
    r"localhost",
    r"127\.0\.0\.",
    r"0\.0\.0\.0",
    r"192\.168\.",
    r"10\.\d+\.",
    r"172\.(1[6-9]|2\d|3[01])\.",
    r"169\.254\.",
    r"::1",
]

# ── Tamaño máximo de respuesta (10 MB) ─────────────────────────────
MAX_RESPONSE_BYTES = 10 * 1024 * 1024


class WebScraperSkill(BaseSkill):
    """
    Skill: Extracción inteligente de contenido web.

    Acciones:
        extract  — Extracción completa (texto, links, imágenes, meta)
        text     — Solo texto limpio del contenido principal
        links    — Todos los enlaces de la página
        tables   — Extrae tablas como listas de diccionarios
        select   — Extracción vía selector CSS
        meta     — Solo metadatos (título, descripción, OG tags)

    Seguridad:
        - Bloquea URLs internas / localhost / file://
        - Limita tamaño de respuesta a 10 MB
        - Timeout configurable (default 20s)
        - No ejecuta JavaScript
    """

    VALID_ACTIONS = {"extract", "text", "links", "tables", "select", "meta"}

    def __init__(self):
        super().__init__(
            name="web_scraper",
            description="Extrae contenido estructurado de páginas web: texto, enlaces, tablas, metadatos",
        )

    # ── Validación ─────────────────────────────────────────────────

    def validate_inputs(self, inputs: Dict[str, Any]) -> tuple[bool, str]:
        url = inputs.get("url", "").strip()
        if not url:
            return False, "Se requiere 'url' no vacío"

        # Seguridad: bloquear ANTES de normalizar protocolo
        for pattern in _BLOCKED_PATTERNS:
            if re.search(pattern, url, re.IGNORECASE):
                return False, "URL bloqueada por seguridad: no se permiten direcciones internas"

        # Normalizar protocolo
        if not url.startswith(("http://", "https://")):
            url = f"https://{url}"

        action = inputs.get("action", "extract")
        if action not in self.VALID_ACTIONS:
            return False, f"Acción inválida '{action}'. Válidas: {self.VALID_ACTIONS}"

        if action == "select" and not inputs.get("selector"):
            return False, "La acción 'select' requiere un 'selector' CSS"

        return True, ""

    # ── Ejecución principal ────────────────────────────────────────

    async def execute(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        start = time.time()
        self.execution_count += 1

        is_valid, error = self.validate_inputs(inputs)
        if not is_valid:
            return {"success": False, "result": None, "error": error, "execution_time": 0}

        url = inputs["url"].strip()
        if not url.startswith(("http://", "https://")):
            url = f"https://{url}"

        action = inputs.get("action", "extract")
        timeout = min(inputs.get("timeout", 20), 60)  # Máximo 60s

        try:
            # ── Fetch HTML ─────────────────────────────────────
            html, final_url, status_code, content_type = await self._fetch(url, timeout)

            # ── Parsear con BeautifulSoup + lxml ───────────────
            soup = BeautifulSoup(html, "lxml")

            # ── Dispatch por acción ────────────────────────────
            if action == "extract":
                result = self._extract_full(soup, final_url)
            elif action == "text":
                result = self._extract_text(soup)
            elif action == "links":
                result = self._extract_links(soup, final_url)
            elif action == "tables":
                result = self._extract_tables(soup)
            elif action == "select":
                selector = inputs["selector"]
                attr = inputs.get("attribute")  # Opcional: extraer atributo específico
                result = self._extract_by_selector(soup, selector, attr, final_url)
            elif action == "meta":
                result = self._extract_metadata(soup, final_url)
            else:
                result = {}

            # Agregar info de la petición
            result["_request"] = {
                "url": url,
                "final_url": final_url,
                "status_code": status_code,
                "content_type": content_type,
                "redirected": url != final_url,
            }

            elapsed = round(time.time() - start, 3)
            self.last_execution = datetime.now()

            return {
                "success": True,
                "result": result,
                "error": None,
                "execution_time": elapsed,
            }

        except httpx.TimeoutException:
            elapsed = round(time.time() - start, 3)
            return {
                "success": False,
                "result": None,
                "error": f"Timeout ({timeout}s) al acceder a {url}",
                "execution_time": elapsed,
            }
        except httpx.HTTPStatusError as e:
            elapsed = round(time.time() - start, 3)
            return {
                "success": False,
                "result": None,
                "error": f"HTTP {e.response.status_code}: {url}",
                "execution_time": elapsed,
            }
        except Exception as e:
            elapsed = round(time.time() - start, 3)
            logger.error(f"Web scraper error for {url}: {e}")
            return {
                "success": False,
                "result": None,
                "error": f"Error scraping {url}: {str(e)}",
                "execution_time": elapsed,
            }

    # ── HTTP Fetch con headers realistas ───────────────────────────

    @staticmethod
    def _is_blocked_url(url: str) -> bool:
        """Check if a URL matches blocked patterns (internal/private IPs).
        Used for both initial URLs and redirect targets (SSRF protection)."""
        for pattern in _BLOCKED_PATTERNS:
            if re.search(pattern, url, re.IGNORECASE):
                return True
        return False

    async def _fetch(self, url: str, timeout: float) -> tuple[str, str, int, str]:
        """
        Descarga el HTML de una URL con headers que imitan un navegador real.
        Retorna (html, final_url, status_code, content_type).

        SECURITY: Validates redirect targets to prevent SSRF via open redirects.
        """
        headers = {
            "User-Agent": random.choice(_USER_AGENTS),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "es-ES,es;q=0.9,en;q=0.8",
            "DNT": "1",
            "Connection": "keep-alive",
            "Upgrade-Insecure-Requests": "1",
            "Cache-Control": "max-age=0",
        }

        async with httpx.AsyncClient(
            follow_redirects=True,
            max_redirects=5,
            timeout=timeout,
            verify=True,
        ) as client:
            response = await client.get(url, headers=headers)
            response.raise_for_status()

            # SECURITY: Check final URL after redirects (SSRF prevention)
            final_url = str(response.url)
            if self._is_blocked_url(final_url):
                raise ValueError("Redirect blocked: final URL targets internal/private address")

            # Limitar tamaño
            content_length = len(response.content)
            if content_length > MAX_RESPONSE_BYTES:
                raise ValueError(f"Respuesta demasiado grande: {content_length / 1024 / 1024:.1f} MB (máx 10 MB)")

            content_type = response.headers.get("content-type", "")
            html = response.text

        return html, final_url, response.status_code, content_type

    # ── Limpieza de HTML ──────────────────────────────────────────

    def _clean_soup(self, soup: BeautifulSoup) -> BeautifulSoup:
        """Elimina tags basura, comentarios y espacios excesivos."""
        # Clonar para no mutar el original

        clean = BeautifulSoup(str(soup), "lxml")

        # Eliminar comentarios HTML
        for comment in clean.find_all(string=lambda t: isinstance(t, Comment)):
            comment.extract()

        # Eliminar tags basura
        for tag_name in _JUNK_TAGS:
            for tag in clean.find_all(tag_name):
                tag.decompose()

        return clean

    def _get_main_content(self, soup: BeautifulSoup) -> Optional[Any]:
        """
        Intenta localizar el contenedor de contenido principal.
        Busca: <article>, <main>, [role=main], .content, #content, etc.
        """
        # Orden de prioridad para encontrar contenido principal
        selectors = [
            "article",
            "main",
            "[role='main']",
            ".post-content",
            ".article-content",
            ".entry-content",
            "#content",
            ".content",
            ".post-body",
            ".article-body",
        ]
        for selector in selectors:
            found = soup.select_one(selector)
            if found and len(found.get_text(strip=True)) > 100:
                return found

        # Fallback: el <body> entero
        return soup.find("body") or soup

    # ── Extractores ───────────────────────────────────────────────

    def _extract_full(self, soup: BeautifulSoup, base_url: str) -> Dict[str, Any]:
        """Extracción completa: meta + texto + links + imágenes."""
        meta = self._extract_metadata(soup, base_url)
        clean = self._clean_soup(soup)
        main = self._get_main_content(clean)

        # Texto principal
        raw_text = main.get_text(separator="\n", strip=True) if main else ""
        text = self._normalize_text(raw_text)

        # Links del contenido principal
        links = []
        if main:
            for a in main.find_all("a", href=True)[:50]:
                href = urljoin(base_url, a["href"])
                anchor = a.get_text(strip=True)
                if anchor and href.startswith("http"):
                    links.append({"text": anchor[:200], "url": href})

        # Imágenes
        images = []
        if main:
            for img in main.find_all("img", src=True)[:20]:
                src = urljoin(base_url, img["src"])
                alt = img.get("alt", "").strip()
                images.append({"src": src, "alt": alt[:200]})

        # Headings para estructura
        headings = []
        if main:
            for h in main.find_all(re.compile(r"^h[1-6]$"))[:30]:
                level = int(h.name[1])
                headings.append({"level": level, "text": h.get_text(strip=True)[:200]})

        return {
            **meta,
            "text": text[:15000],  # Limitar texto a 15k chars
            "text_length": len(text),
            "links": links,
            "images": images,
            "headings": headings,
            "word_count": len(text.split()),
        }

    def _extract_text(self, soup: BeautifulSoup) -> Dict[str, Any]:
        """Extrae solo el texto limpio del contenido principal."""
        clean = self._clean_soup(soup)
        main = self._get_main_content(clean)

        raw_text = main.get_text(separator="\n", strip=True) if main else ""
        text = self._normalize_text(raw_text)

        return {
            "text": text[:20000],
            "text_length": len(text),
            "word_count": len(text.split()),
            "content_hash": hashlib.sha256(text[:1000].encode()).hexdigest()[:12],
        }

    def _extract_links(self, soup: BeautifulSoup, base_url: str) -> Dict[str, Any]:
        """Extrae todos los enlaces de la página, clasificados."""
        parsed_base = urlparse(base_url)
        internal = []
        external = []

        seen_urls = set()
        for a in soup.find_all("a", href=True):
            href = urljoin(base_url, a["href"])
            if href in seen_urls or not href.startswith("http"):
                continue
            seen_urls.add(href)

            anchor = a.get_text(strip=True)
            parsed = urlparse(href)

            link_data = {
                "text": anchor[:200] if anchor else "[sin texto]",
                "url": href,
            }

            if parsed.netloc == parsed_base.netloc:
                internal.append(link_data)
            else:
                external.append(link_data)

        return {
            "internal_links": internal[:100],
            "external_links": external[:100],
            "total_internal": len(internal),
            "total_external": len(external),
        }

    def _extract_tables(self, soup: BeautifulSoup) -> Dict[str, Any]:
        """Extrae tablas HTML como listas de diccionarios."""
        tables = []

        for idx, table in enumerate(soup.find_all("table")[:10]):
            rows = table.find_all("tr")
            if not rows:
                continue

            # Detectar headers
            headers = []
            header_row = rows[0]
            for th in header_row.find_all(["th", "td"]):
                headers.append(th.get_text(strip=True))

            if not headers:
                continue

            # Extraer filas de datos
            data_rows = []
            for row in rows[1:50]:  # Máximo 50 filas por tabla
                cells = row.find_all(["td", "th"])
                if len(cells) == len(headers):
                    row_data = {}
                    for i, cell in enumerate(cells):
                        row_data[headers[i] or f"col_{i}"] = cell.get_text(strip=True)
                    data_rows.append(row_data)

            if data_rows:
                # Intentar capturar el caption o título cercano
                caption = table.find("caption")
                title = caption.get_text(strip=True) if caption else f"Tabla {idx + 1}"

                tables.append(
                    {
                        "title": title,
                        "headers": headers,
                        "rows": data_rows,
                        "row_count": len(data_rows),
                    }
                )

        return {
            "tables": tables,
            "total_tables": len(tables),
        }

    def _extract_by_selector(
        self,
        soup: BeautifulSoup,
        selector: str,
        attribute: Optional[str],
        base_url: str,
    ) -> Dict[str, Any]:
        """Extrae elementos usando un selector CSS."""
        try:
            elements = soup.select(selector)
        except Exception as e:
            return {"error": f"Selector CSS inválido: {e}", "matches": []}

        matches = []
        for el in elements[:50]:
            if attribute:
                val = el.get(attribute, "")
                if attribute in ("href", "src") and val:
                    val = urljoin(base_url, val)
                matches.append({"value": val})
            else:
                matches.append(
                    {
                        "tag": el.name,
                        "text": el.get_text(strip=True)[:500],
                        "attributes": {k: v for k, v in el.attrs.items() if isinstance(v, str) and len(v) < 200},
                    }
                )

        return {
            "selector": selector,
            "matches": matches,
            "total_matches": len(elements),
            "returned": len(matches),
        }

    def _extract_metadata(self, soup: BeautifulSoup, base_url: str) -> Dict[str, Any]:
        """Extrae metadatos: título, descripción, OG tags, favicon."""
        meta = {
            "title": "",
            "description": "",
            "language": "",
            "canonical_url": "",
            "favicon": "",
            "og": {},
        }

        # Título
        if soup.title and soup.title.string:
            meta["title"] = soup.title.string.strip()

        # Idioma
        html_tag = soup.find("html")
        if html_tag:
            meta["language"] = html_tag.get("lang", "")

        # Meta tags estándar
        desc = soup.find("meta", attrs={"name": "description"})
        if desc:
            meta["description"] = desc.get("content", "")[:500]

        # Canonical
        canonical = soup.find("link", attrs={"rel": "canonical"})
        if canonical:
            meta["canonical_url"] = canonical.get("href", "")

        # Favicon
        icon = soup.find("link", attrs={"rel": re.compile(r"icon", re.I)})
        if icon:
            meta["favicon"] = urljoin(base_url, icon.get("href", ""))

        # Open Graph tags
        for og in soup.find_all("meta", attrs={"property": re.compile(r"^og:")}):
            key = og.get("property", "").replace("og:", "")
            val = og.get("content", "")
            if key and val:
                meta["og"][key] = val[:500]

        # Twitter Card
        twitter = {}
        for tw in soup.find_all("meta", attrs={"name": re.compile(r"^twitter:")}):
            key = tw.get("name", "").replace("twitter:", "")
            val = tw.get("content", "")
            if key and val:
                twitter[key] = val[:500]
        if twitter:
            meta["twitter"] = twitter

        return meta

    # ── Helpers ────────────────────────────────────────────────────

    @staticmethod
    def _normalize_text(text: str) -> str:
        """Limpia whitespace excesivo manteniendo estructura de párrafos."""
        # Colapsar líneas vacías múltiples
        text = re.sub(r"\n{3,}", "\n\n", text)
        # Colapsar espacios horizontales
        text = re.sub(r"[^\S\n]+", " ", text)
        # Limpiar líneas que son solo espacios
        lines = [line.strip() for line in text.split("\n")]
        return "\n".join(lines).strip()
