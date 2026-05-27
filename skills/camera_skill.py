"""
Camera Skill para Origin.

Accede a la cámara web para captura y análisis visual en tiempo real.
Permite a Origin reconocer objetos, documentos, personas y escenas
que el usuario le muestra físicamente.

Modos de operación:
  1. Server-side capture: OpenCV accede a la webcam del servidor (mismo PC)
  2. Browser frame analysis: acepta frame base64 enviado desde el frontend
     (useful para análisis de lo que el usuario tiene en cámara del browser)

Análisis visual con Gemini 1.5 Flash (multimodal).
"""

import asyncio
import base64
import logging
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from .base_skill import BaseSkill

logger = logging.getLogger("origin.skills.camera")

CAMERA_DIR = Path(__file__).resolve().parent.parent / "data" / "camera"
MAX_FRAMES_KEPT = 30
JPEG_QUALITY = 80
MAX_DIMENSION = 1280


def _cv2_available() -> bool:
    try:
        import cv2  # noqa: F401

        return True
    except ImportError:
        return False


class CameraSkill(BaseSkill):
    """
    Skill de cámara: captura y análisis visual con la webcam.

    Acciones:
        capture        → Toma una foto con la webcam
        analyze        → Captura + análisis con Gemini (qué ve la cámara)
        analyze_frame  → Analiza un frame base64 enviado desde el browser
        list_cameras   → Lista cámaras disponibles en el sistema
        status         → Estado del módulo de cámara
    """

    VALID_ACTIONS = frozenset({"capture", "analyze", "analyze_frame", "list_cameras", "status"})

    def __init__(self):
        super().__init__(
            name="camera",
            description=(
                "Accede a la webcam para ver y reconocer objetos, documentos y escenas "
                "que el usuario muestra físicamente. Análisis con Gemini vision."
            ),
        )
        self._llm_router = None
        self._camera_index = 0
        self._last_frame_path: Optional[str] = None
        CAMERA_DIR.mkdir(parents=True, exist_ok=True)

    def set_llm_router(self, router) -> None:
        """Inject LLM router for Gemini vision analysis."""
        self._llm_router = router

    # ── BaseSkill interface ───────────────────────────────────────

    def validate_inputs(self, inputs: Dict[str, Any]) -> tuple[bool, str]:
        action = inputs.get("action", "status")
        if action not in self.VALID_ACTIONS:
            return False, f"Acción inválida: '{action}'. Válidas: {self.VALID_ACTIONS}"
        if action == "analyze_frame":
            if not inputs.get("frame_b64"):
                return False, "Se requiere 'frame_b64' (imagen en base64)"
        if action in ("capture", "analyze") and not _cv2_available():
            return False, "OpenCV no instalado: pip install opencv-python"
        return True, ""

    async def execute(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        start = time.time()
        action = inputs.get("action", "status")

        is_valid, error = self.validate_inputs(inputs)
        if not is_valid:
            return {"success": False, "result": None, "error": error, "execution_time": 0}

        try:
            if action == "capture":
                result = await asyncio.get_event_loop().run_in_executor(None, self._capture_frame, inputs)
            elif action == "analyze":
                result = await self._analyze_camera(inputs)
            elif action == "analyze_frame":
                result = await self._analyze_frame(inputs)
            elif action == "list_cameras":
                result = await asyncio.get_event_loop().run_in_executor(None, self._list_cameras)
            elif action == "status":
                result = self._get_status()
            else:
                result = {"error": f"Acción no implementada: {action}"}

            elapsed = round(time.time() - start, 3)
            self.last_execution = datetime.now()
            self.execution_count += 1

            has_error = isinstance(result, dict) and "error" in result
            return {
                "success": not has_error,
                "result": result,
                "error": result.get("error") if has_error else None,
                "execution_time": elapsed,
            }

        except Exception as e:
            logger.error(f"Camera skill error: {e}")
            return {
                "success": False,
                "result": None,
                "error": str(e),
                "execution_time": round(time.time() - start, 3),
            }

    # ── Capture ──────────────────────────────────────────────────

    def _capture_frame(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        """Capture a single frame from the webcam (blocking, runs in executor)."""
        import cv2

        camera_idx = inputs.get("camera_index", self._camera_index)

        cap = cv2.VideoCapture(camera_idx, cv2.CAP_DSHOW)
        if not cap.isOpened():
            # Try without backend hint
            cap = cv2.VideoCapture(camera_idx)
        if not cap.isOpened():
            return {"error": f"No se pudo abrir la cámara {camera_idx}"}

        try:
            # Set resolution
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)

            # Warm up the camera (discard first few frames)
            for _ in range(3):
                cap.read()

            ret, frame = cap.read()
            if not ret or frame is None:
                return {"error": "No se pudo capturar frame de la cámara"}

        finally:
            cap.release()

        # Convert BGR → RGB
        frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)

        # Resize if needed
        h, w = frame_rgb.shape[:2]
        if w > MAX_DIMENSION or h > MAX_DIMENSION:
            scale = MAX_DIMENSION / max(w, h)
            new_w, new_h = int(w * scale), int(h * scale)
            frame_rgb = cv2.resize(frame_rgb, (new_w, new_h), interpolation=cv2.INTER_AREA)
            h, w = new_h, new_w

        # Encode to JPEG
        success, buf = cv2.imencode(
            ".jpg", cv2.cvtColor(frame_rgb, cv2.COLOR_RGB2BGR), [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY]
        )
        if not success:
            return {"error": "Error codificando frame como JPEG"}

        # Save to disk
        self._cleanup_old_frames()
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:22]
        filename = f"cam_{timestamp}.jpg"
        filepath = str(CAMERA_DIR / filename)
        with open(filepath, "wb") as f:
            f.write(buf.tobytes())

        self._last_frame_path = filepath

        # Base64 for API response
        b64 = base64.b64encode(buf.tobytes()).decode("utf-8")

        return {
            "path": filepath,
            "filename": filename,
            "width": w,
            "height": h,
            "camera_index": camera_idx,
            "file_size_kb": round(len(buf) / 1024, 1),
            "frame_b64": b64,
            "mime_type": "image/jpeg",
        }

    # ── Analyze camera ────────────────────────────────────────────

    async def _analyze_camera(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        """Capture from webcam then analyze with Gemini."""
        capture = await asyncio.get_event_loop().run_in_executor(None, self._capture_frame, inputs)
        if "error" in capture:
            return capture

        question = inputs.get(
            "question",
            "Describe detalladamente lo que ves. ¿Qué objetos, texto, personas o escenas hay? "
            "¿Qué está haciendo o mostrando el usuario?",
        )

        analysis = await self._call_vision_gemini(capture["frame_b64"], question)
        return {
            "capture": {k: v for k, v in capture.items() if k != "frame_b64"},
            "analysis": analysis,
            "question": question,
        }

    # ── Analyze browser frame ─────────────────────────────────────

    async def _analyze_frame(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        """Analyze a base64 frame sent from the browser camera."""
        frame_b64 = inputs["frame_b64"]
        question = inputs.get(
            "question",
            "Describe detalladamente lo que ves. ¿Qué objetos, texto, personas o escenas hay?",
        )

        # Validate and optionally resize
        try:
            raw = base64.b64decode(frame_b64)
            size_kb = len(raw) / 1024
        except Exception:
            return {"error": "frame_b64 inválido: no es base64 válido"}

        # Gemini has 20MB image limit; warn but continue
        if size_kb > 15_000:
            return {"error": f"Frame demasiado grande: {size_kb:.0f} KB (máx ~15 MB)"}

        analysis = await self._call_vision_gemini(frame_b64, question)

        # Optionally save to disk
        if inputs.get("save", False):
            self._cleanup_old_frames()
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filepath = str(CAMERA_DIR / f"browser_{timestamp}.jpg")
            with open(filepath, "wb") as f:
                f.write(raw)
        else:
            filepath = None

        return {
            "analysis": analysis,
            "question": question,
            "frame_size_kb": round(size_kb, 1),
            "saved_path": filepath,
            "source": "browser",
        }

    # ── Gemini vision ─────────────────────────────────────────────

    async def _call_vision_gemini(self, b64_image: str, prompt: str) -> str:
        """Send image + prompt to Gemini 2.5 Flash (multimodal)."""
        try:
            import google.generativeai as genai

            model = genai.GenerativeModel("gemini-2.5-flash")
            image_part = {"mime_type": "image/jpeg", "data": b64_image}
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

            # Fallback: text-only LLM
            if self._llm_router:
                result = await self._llm_router.call_llm(
                    prompt=(
                        "[El usuario ha mostrado algo a la cámara pero no puedo ver la imagen directamente.]\n"
                        f"{prompt}\n\n"
                        "Explica que no tienes acceso visual directo y pide al usuario que describa qué está mostrando."
                    ),
                    temperature=0.3,
                    task_type="vision_fallback",
                )
                return result.get("content", "No se pudo analizar la imagen.")
            return f"Error en visión: {gemini_err}"

    # ── List cameras ──────────────────────────────────────────────

    def _list_cameras(self) -> Dict[str, Any]:
        """Enumerate available cameras using OpenCV."""
        if not _cv2_available():
            return {"error": "OpenCV no instalado", "cameras": []}

        import cv2

        cameras: List[Dict[str, Any]] = []
        for idx in range(5):
            cap = cv2.VideoCapture(idx, cv2.CAP_DSHOW)
            if cap.isOpened():
                w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
                h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
                fps = cap.get(cv2.CAP_PROP_FPS)
                cameras.append(
                    {
                        "index": idx,
                        "resolution": f"{w}x{h}",
                        "fps": round(fps, 1),
                        "available": True,
                    }
                )
                cap.release()

        return {
            "cameras": cameras,
            "count": len(cameras),
            "current_index": self._camera_index,
            "opencv_available": True,
        }

    # ── Status ───────────────────────────────────────────────────

    def _get_status(self) -> Dict[str, Any]:
        return {
            "opencv_available": _cv2_available(),
            "camera_index": self._camera_index,
            "llm_vision_ready": self._llm_router is not None,
            "last_frame": self._last_frame_path,
            "frames_dir": str(CAMERA_DIR),
            "frames_saved": len(list(CAMERA_DIR.glob("*.jpg"))),
        }

    # ── Cleanup ───────────────────────────────────────────────────

    def _cleanup_old_frames(self) -> None:
        try:
            files = sorted(CAMERA_DIR.glob("*.jpg"), key=lambda f: f.stat().st_mtime)
            for f in files[:-MAX_FRAMES_KEPT]:
                f.unlink()
        except Exception:
            pass
