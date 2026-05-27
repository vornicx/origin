"""
BrowserSkill — Automatización de navegador web (inspirado en Claude Computer Use).

Usa Playwright para controlar Chromium/Firefox/Edge:
  - Navegar a URLs
  - Hacer click en elementos por texto/selector
  - Extraer texto y HTML
  - Llenar formularios
  - Tomar screenshots
  - Ejecutar JavaScript

Requisito: pip install playwright
"""
import logging
import base64
from typing import Dict, Any
from datetime import datetime
from pathlib import Path

from .base_skill import BaseSkill

logger = logging.getLogger("origin.skills.browser")

PLAYWRIGHT_AVAILABLE = False
try:
    from playwright.async_api import async_playwright

    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    pass

SCREENSHOT_DIR = Path(__file__).parent.parent / "screenshots"
SCREENSHOT_DIR.mkdir(exist_ok=True)


class BrowserSkill(BaseSkill):
    """Control de navegador web via Playwright.

    Acciones:
        navigate     → Navega a URL, retorna título + screenshot
        click        → Click en elemento (por texto, selector, o coordenadas)
        type         → Escribe texto en un campo
        extract      → Extrae texto de la página
        screenshot   → Toma screenshot de la página actual
        javascript   → Ejecuta JS en la página
        close        → Cierra el navegador
    """

    VALID_ACTIONS = {"navigate", "click", "type", "extract", "screenshot", "javascript", "close"}

    def __init__(self):
        super().__init__(
            name="browser", description="Controla el navegador web: navega, clickea, escribe, extrae contenido."
        )
        self._playwright = None
        self._browser = None
        self._page = None
        self._headless = False

    async def _ensure_browser(self):
        if not PLAYWRIGHT_AVAILABLE:
            raise RuntimeError("Playwright no instalado. pip install playwright && playwright install chromium")
        if not self._browser:
            self._playwright = await async_playwright().start()
            self._browser = await self._playwright.chromium.launch(
                headless=self._headless,
                args=["--disable-blink-features=AutomationControlled"],
            )
            ctx = await self._browser.new_context(
                viewport={"width": 1280, "height": 720},
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0",
            )
            self._page = await ctx.new_page()
            logger.info("Browser: navegador iniciado")

    def validate_inputs(self, inputs: Dict[str, Any]) -> tuple:
        action = inputs.get("action", "")
        if action not in self.VALID_ACTIONS:
            return False, f"Acción inválida '{action}'. Válidas: {self.VALID_ACTIONS}"
        if action == "navigate" and not inputs.get("url"):
            return False, "Se requiere 'url' para navigate"
        if action == "click":
            if not inputs.get("text") and not inputs.get("selector"):
                return False, "Se requiere 'text' o 'selector' para click"
        if action == "type":
            if not inputs.get("text"):
                return False, "Se requiere 'text' para type"
            if not inputs.get("selector"):
                return False, "Se requiere 'selector' para type"
        return True, ""

    async def execute(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        import time

        start = time.time()
        self.execution_count += 1
        is_valid, error = self.validate_inputs(inputs)
        if not is_valid:
            return {"success": False, "result": None, "error": error, "execution_time": 0}
        try:
            action = inputs["action"]
            if action == "navigate":
                result = await self._navigate(inputs)
            elif action == "click":
                result = await self._click(inputs)
            elif action == "type":
                result = await self._type(inputs)
            elif action == "extract":
                result = await self._extract(inputs)
            elif action == "screenshot":
                result = await self._screenshot()
            elif action == "javascript":
                result = await self._javascript(inputs)
            elif action == "close":
                result = await self._close()
            else:
                result = {"error": f"Acción no implementada: {action}"}

            elapsed = round(time.time() - start, 3)
            self.last_execution = datetime.now()
            has_error = isinstance(result, dict) and result.get("error") is not None
            return {
                "success": not has_error,
                "result": result,
                "error": result.get("error") if has_error else None,
                "execution_time": elapsed,
            }
        except Exception as e:
            elapsed = round(time.time() - start, 3)
            logger.error(f"Browser error: {e}")
            return {"success": False, "result": None, "error": str(e), "execution_time": elapsed}

    async def _navigate(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        await self._ensure_browser()
        url = inputs["url"]
        if not url.startswith("http"):
            url = "https://" + url
        timeout = inputs.get("timeout", 30000)
        await self._page.goto(url, wait_until="domcontentloaded", timeout=timeout)
        title = await self._page.title()
        ss = await self._page.screenshot(type="jpeg", quality=60)
        ss_b64 = base64.b64encode(ss).decode()
        return {
            "url": self._page.url,
            "title": title,
            "screenshot_b64": ss_b64[:100000],
        }

    async def _click(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        await self._ensure_browser()
        text = inputs.get("text", "")
        selector = inputs.get("selector", "")
        if selector:
            await self._page.click(selector)
            return {"clicked": True, "selector": selector}
        if text:
            await self._page.click(f"text={text}")
            return {"clicked": True, "text": text}
        return {"error": "No se especificó target"}

    async def _type(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        await self._ensure_browser()
        selector = inputs["selector"]
        text = inputs["text"]
        delay = inputs.get("delay", 50)
        await self._page.fill(selector, "")
        await self._page.type(selector, text, delay=delay)
        return {"typed": True, "selector": selector, "length": len(text)}

    async def _extract(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        await self._ensure_browser()
        selector = inputs.get("selector", "body")
        try:
            elements = await self._page.query_selector_all(selector)
            texts = []
            for el in elements[:50]:
                t = await el.inner_text()
                if t.strip():
                    texts.append(t.strip()[:500])
            content = await self._page.inner_text("body") if not texts else "\n".join(texts)
            return {"text": content[:10000], "chars": len(content[:10000]), "elements": len(texts)}
        except Exception as e:
            return {"error": f"Error extrayendo: {e}"}

    async def _screenshot(self) -> Dict[str, Any]:
        await self._ensure_browser()
        path = SCREENSHOT_DIR / f"browser_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
        await self._page.screenshot(path=str(path), full_page=True)
        with open(path, "rb") as f:
            b64 = base64.b64encode(f.read()).decode()
        return {"screenshot_path": str(path), "screenshot_b64": b64}

    async def _javascript(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        await self._ensure_browser()
        code = inputs.get("code", "")
        if not code:
            return {"error": "Se requiere 'code'"}
        result = await self._page.evaluate(code)
        return {"result": str(result)[:5000]}

    async def _close(self) -> Dict[str, Any]:
        if self._browser:
            await self._browser.close()
            self._browser = None
            self._page = None
        if self._playwright:
            await self._playwright.stop()
            self._playwright = None
        return {"closed": True}
