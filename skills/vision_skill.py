"""
Vision Skill para Origin.
Captura screenshots de la pantalla y los analiza usando un LLM con vision.
Permite a Origin "ver" lo que hay en el escritorio y tomar decisiones.

Requiere: mss, Pillow
LLM vision: Gemini (multimodal) via API
"""

import time
import base64
import io
import os
import logging
from typing import Dict, Any, Optional
from datetime import datetime

try:
    import mss
    import mss.tools

    MSS_AVAILABLE = True
except ImportError:
    MSS_AVAILABLE = False

try:
    from PIL import Image

    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False

from .base_skill import BaseSkill

logger = logging.getLogger("origin.skills.vision")

# ── Config ────────────────────────────────────────────────────────
SCREENSHOT_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "screenshots")
MAX_IMAGE_SIZE = (1920, 1080)  # Redimensionar si es mayor
JPEG_QUALITY = 75  # Calidad de compresion JPEG
MAX_SCREENSHOTS_KEPT = 20  # Limitar archivos guardados


class VisionSkill(BaseSkill):
    """
    Skill: Captura y analisis visual de la pantalla.

    Acciones:
        screenshot  - Captura screenshot, retorna path + base64 thumbnail
        analyze     - Captura + analiza con LLM vision (que hay en pantalla)
        find        - Busca un elemento visual en la pantalla por descripcion
        read_text   - Intenta leer texto visible en la pantalla
        compare     - Compara dos screenshots para detectar cambios
        monitor     - Captura una region especifica de la pantalla

    Captura con mss (rapido, no bloquea), analisis con Gemini multimodal.
    """

    VALID_ACTIONS = {"screenshot", "analyze", "find", "read_text", "compare", "monitor"}

    def __init__(self):
        super().__init__(
            name="vision",
            description="Captura y analiza la pantalla: screenshot, OCR, buscar elementos, detectar cambios",
        )
        self._last_screenshot: Optional[str] = None  # Path del ultimo screenshot
        self._llm_router = None  # Lazy init - se inyecta desde Mind

        # Crear directorio de screenshots
        os.makedirs(SCREENSHOT_DIR, exist_ok=True)

    def set_llm_router(self, router):
        """Inyecta el LLM router para analisis vision."""
        self._llm_router = router

    # ── Validacion ─────────────────────────────────────────────────

    def validate_inputs(self, inputs: Dict[str, Any]) -> tuple[bool, str]:
        action = inputs.get("action", "screenshot")
        if action not in self.VALID_ACTIONS:
            return False, f"Accion invalida '{action}'. Validas: {self.VALID_ACTIONS}"

        if not MSS_AVAILABLE:
            return False, "Libreria 'mss' no instalada. Ejecutar: pip install mss"

        if not PIL_AVAILABLE:
            return False, "Libreria 'Pillow' no instalada. Ejecutar: pip install Pillow"

        if action == "analyze" and not self._llm_router:
            return False, "LLM Router no configurado. Vision analyze requiere un LLM con vision."

        if action == "find" and not inputs.get("description"):
            return False, "Se requiere 'description' para buscar un elemento"

        if action == "monitor":
            region = inputs.get("region")
            if region and (not isinstance(region, (list, tuple)) or len(region) != 4):
                return False, "Region debe ser [x, y, width, height]"

        return True, ""

    # ── Ejecucion principal ────────────────────────────────────────

    async def execute(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        start = time.time()
        self.execution_count += 1

        is_valid, error = self.validate_inputs(inputs)
        if not is_valid:
            return {"success": False, "result": None, "error": error, "execution_time": 0}

        try:
            action = inputs.get("action", "screenshot")

            if action == "screenshot":
                result = self._take_screenshot(inputs)
            elif action == "analyze":
                result = await self._analyze_screen(inputs)
            elif action == "find":
                result = await self._find_element(inputs)
            elif action == "read_text":
                result = await self._read_text(inputs)
            elif action == "compare":
                result = self._compare_screenshots(inputs)
            elif action == "monitor":
                result = self._monitor_region(inputs)
            else:
                result = {"error": f"Accion no implementada: {action}"}

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
            import traceback

            logger.error(f"Vision skill error: {e}\n{traceback.format_exc()}")
            return {
                "success": False,
                "result": None,
                "error": str(e) if str(e) else type(e).__name__,
                "execution_time": elapsed,
            }

    # ── screenshot: captura pantalla ───────────────────────────────

    def _take_screenshot(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        """Captura screenshot completo o de region especifica."""
        region = inputs.get("region")  # [x, y, w, h] optional
        monitor_idx = inputs.get("monitor", 0)  # 0 = all monitors

        with mss.mss() as sct:
            if region:
                monitor = {
                    "left": region[0],
                    "top": region[1],
                    "width": region[2],
                    "height": region[3],
                }
            else:
                monitors = sct.monitors
                if monitor_idx < len(monitors):
                    monitor = monitors[monitor_idx]
                else:
                    monitor = monitors[0]  # All monitors combined

            # Capturar
            sct_img = sct.grab(monitor)
            img = Image.frombytes("RGB", sct_img.size, sct_img.bgra, "raw", "BGRX")

        # Redimensionar si es muy grande
        original_size = img.size
        if img.width > MAX_IMAGE_SIZE[0] or img.height > MAX_IMAGE_SIZE[1]:
            img.thumbnail(MAX_IMAGE_SIZE, Image.LANCZOS)

        # Guardar archivo
        self._cleanup_old_screenshots()
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"screen_{timestamp}.jpg"
        filepath = os.path.join(SCREENSHOT_DIR, filename)
        img.save(filepath, "JPEG", quality=JPEG_QUALITY)

        # Generar base64 thumbnail (para enviar al LLM)
        thumb = img.copy()
        thumb.thumbnail((800, 600), Image.LANCZOS)
        buffer = io.BytesIO()
        thumb.save(buffer, format="JPEG", quality=60)
        b64_thumb = base64.b64encode(buffer.getvalue()).decode("utf-8")

        self._last_screenshot = filepath

        return {
            "path": filepath,
            "filename": filename,
            "original_size": list(original_size),
            "saved_size": list(img.size),
            "file_size_kb": round(os.path.getsize(filepath) / 1024, 1),
            "thumbnail_b64_length": len(b64_thumb),
            "monitor": monitor_idx,
            "region": region,
        }

    # ── analyze: captura + LLM vision ──────────────────────────────

    async def _analyze_screen(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        """Captura screenshot y lo analiza con LLM vision."""
        # Primero tomar screenshot
        screenshot = self._take_screenshot(inputs)
        if "error" in screenshot:
            return screenshot

        # Leer imagen para enviar al LLM
        img_path = screenshot["path"]
        img = Image.open(img_path)

        # Reducir para el LLM
        img.thumbnail((1280, 720), Image.LANCZOS)
        buffer = io.BytesIO()
        img.save(buffer, format="JPEG", quality=70)
        img_bytes = buffer.getvalue()
        b64_image = base64.b64encode(img_bytes).decode("utf-8")

        # Preparar prompt
        question = inputs.get(
            "question",
            "Describe detalladamente lo que ves en esta pantalla. Incluye aplicaciones abiertas, contenido visible, y cualquier informacion relevante.",  # noqa: E501
        )

        # Llamar al LLM con vision (Gemini multimodal)
        try:
            analysis = await self._call_vision_llm(b64_image, question)
        except Exception as e:
            return {
                "screenshot": screenshot,
                "analysis": None,
                "error": f"Error en analisis LLM: {str(e)}",
            }

        return {
            "screenshot": screenshot,
            "analysis": analysis,
            "question": question,
        }

    # ── find: buscar elemento visual ───────────────────────────────

    async def _find_element(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        """Busca un elemento en la pantalla por descripcion natural."""
        description = inputs.get("description", "")

        # Tomar screenshot
        screenshot = self._take_screenshot(inputs)
        if "error" in screenshot:
            return screenshot

        img_path = screenshot["path"]
        img = Image.open(img_path)
        img.thumbnail((1280, 720), Image.LANCZOS)
        buffer = io.BytesIO()
        img.save(buffer, format="JPEG", quality=70)
        b64_image = base64.b64encode(buffer.getvalue()).decode("utf-8")

        prompt = f"""Busca el siguiente elemento en la pantalla: "{description}"

Si lo encuentras, responde en JSON puro:
{{
  "found": true,
  "location": {{"x": <porcentaje_horizontal_0_100>, "y": <porcentaje_vertical_0_100>}},
  "description": "<que es exactamente>",
  "confidence": <0.0 a 1.0>
}}

Si NO lo encuentras:
{{
  "found": false,
  "description": "<que hay en la pantalla en su lugar>",
  "suggestions": ["<donde podria estar>"]
}}

Responde SOLO JSON, sin markdown."""

        try:
            response = await self._call_vision_llm(b64_image, prompt)

            # Intentar parsear JSON
            from core.utils import extract_json_from_text

            result = extract_json_from_text(response)

            if result and result.get("found"):
                # Convertir porcentajes a coordenadas absolutas
                orig_w, orig_h = screenshot["original_size"]
                loc = result.get("location", {})
                abs_x = int(loc.get("x", 50) * orig_w / 100)
                abs_y = int(loc.get("y", 50) * orig_h / 100)
                result["absolute_location"] = {"x": abs_x, "y": abs_y}

            return {
                "screenshot": screenshot,
                "search": description,
                "result": result or {"found": False, "raw_response": response},
            }

        except Exception as e:
            return {
                "screenshot": screenshot,
                "search": description,
                "error": f"Error buscando elemento: {str(e)}",
            }

    # ── read_text: leer texto en pantalla ──────────────────────────

    async def _read_text(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        """Lee todo el texto visible en la pantalla usando LLM vision."""
        screenshot = self._take_screenshot(inputs)
        if "error" in screenshot:
            return screenshot

        img_path = screenshot["path"]
        img = Image.open(img_path)
        img.thumbnail((1280, 720), Image.LANCZOS)
        buffer = io.BytesIO()
        img.save(buffer, format="JPEG", quality=75)
        b64_image = base64.b64encode(buffer.getvalue()).decode("utf-8")

        prompt = """Lee TODO el texto visible en esta pantalla. Incluye:
- Titulos de ventanas
- Contenido de documentos/paginas web
- Menus, botones y labels
- Texto en barras de estado o notificaciones
- URLs visibles

Organiza el texto por secciones segun la aplicacion o area de la pantalla.
Responde en texto plano, no JSON."""

        try:
            text = await self._call_vision_llm(b64_image, prompt)
            return {
                "screenshot": screenshot,
                "text": text,
            }
        except Exception as e:
            return {
                "screenshot": screenshot,
                "error": f"Error leyendo texto: {str(e)}",
            }

    # ── compare: comparar screenshots ──────────────────────────────

    def _compare_screenshots(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        """Compara el screenshot actual con el anterior para detectar cambios."""
        if not self._last_screenshot or not os.path.exists(self._last_screenshot):
            # No hay screenshot previo, tomar uno nuevo
            new_shot = self._take_screenshot(inputs)
            return {
                "previous": None,
                "current": new_shot,
                "note": "No habia screenshot previo. Este es el primero. Ejecuta de nuevo para comparar.",
            }

        previous_path = self._last_screenshot

        # Tomar nuevo screenshot
        new_shot = self._take_screenshot(inputs)
        if "error" in new_shot:
            return new_shot

        # Comparar imagenes usando diferencia de pixels
        try:
            img_prev = Image.open(previous_path).convert("L")  # Grayscale
            img_curr = Image.open(new_shot["path"]).convert("L")

            # Redimensionar a mismo tamano para comparar
            size = (640, 360)
            img_prev = img_prev.resize(size)
            img_curr = img_curr.resize(size)

            # Calcular diferencia pixel a pixel
            import numpy as np

            arr_prev = np.array(img_prev, dtype=float)
            arr_curr = np.array(img_curr, dtype=float)
            diff = np.abs(arr_prev - arr_curr)

            # Estadisticas
            mean_diff = float(np.mean(diff))
            max_diff = float(np.max(diff))
            changed_pixels = int(np.sum(diff > 15))  # Threshold de 15/255
            total_pixels = diff.size
            change_pct = round(changed_pixels / total_pixels * 100, 2)

            return {
                "previous": previous_path,
                "current": new_shot,
                "comparison": {
                    "mean_difference": round(mean_diff, 2),
                    "max_difference": round(max_diff, 2),
                    "changed_pixels": changed_pixels,
                    "total_pixels": total_pixels,
                    "change_percentage": change_pct,
                    "significant_change": change_pct > 5.0,
                },
            }
        except ImportError:
            return {
                "previous": previous_path,
                "current": new_shot,
                "comparison": None,
                "note": "numpy no disponible para comparacion detallada",
            }

    # ── monitor: capturar region ───────────────────────────────────

    def _monitor_region(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        """Captura una region especifica de la pantalla."""
        region = inputs.get("region")
        if not region:
            return {"error": "Se requiere 'region': [x, y, width, height]"}

        return self._take_screenshot({"region": region})

    # ── LLM Vision Helper ──────────────────────────────────────────

    async def _call_vision_llm(self, b64_image: str, prompt: str) -> str:
        """
        Envia imagen + prompt a un LLM con capacidad vision.
        Usa Gemini multimodal por defecto.
        """
        if not self._llm_router:
            raise Exception("LLM Router no configurado para vision")

        # Intentar con Gemini (tiene vision nativa)
        try:
            import google.generativeai as genai

            model = genai.GenerativeModel("gemini-1.5-flash")

            # Crear parte de imagen
            image_part = {
                "mime_type": "image/jpeg",
                "data": b64_image,
            }

            response = await model.generate_content_async(
                [prompt, image_part],
                generation_config={
                    "temperature": 0.3,
                    "max_output_tokens": 2048,
                },
            )
            return response.text

        except Exception as gemini_err:
            logger.warning(f"Gemini vision failed: {gemini_err}")

            # Fallback: DeepSeek con descripcion textual del screenshot
            logger.info("Falling back to text-only LLM (no vision)")
            fallback_prompt = f"""[NOTA: No puedo ver la imagen directamente, pero el usuario ha tomado un screenshot]

{prompt}

Responde basandote en lo que tipicamente se veria en una pantalla de Windows,
explicando que no tienes acceso visual directo al screenshot."""

            result = await self._llm_router.call_llm(
                fallback_prompt,
                system_message="Eres Origin. El usuario ha tomado un screenshot pero no puedes verlo directamente.",
                temperature=0.3,
            )
            return result.get("content", "No se pudo analizar la pantalla")

    # ── Helpers ────────────────────────────────────────────────────

    def _cleanup_old_screenshots(self):
        """Elimina screenshots antiguos si hay demasiados."""
        try:
            files = sorted(
                [f for f in os.listdir(SCREENSHOT_DIR) if f.endswith((".jpg", ".png"))],
                key=lambda f: os.path.getmtime(os.path.join(SCREENSHOT_DIR, f)),
            )
            while len(files) > MAX_SCREENSHOTS_KEPT:
                old_file = files.pop(0)
                os.remove(os.path.join(SCREENSHOT_DIR, old_file))
        except Exception:
            pass
