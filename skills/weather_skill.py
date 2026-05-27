"""
WeatherSkill — Opens weather information for a given city.

Actions:
  check → Opens weather search in the default browser
"""

import logging
import time
import webbrowser
from typing import Dict, Any
from urllib.parse import quote_plus
from datetime import datetime

from .base_skill import BaseSkill

logger = logging.getLogger("origin.skills")


class WeatherSkill(BaseSkill):

    def __init__(self):
        super().__init__(
            name="weather",
            description="Consulta el clima de una ciudad abriendo búsqueda en el navegador",
        )

    def validate_inputs(self, inputs: Dict[str, Any]) -> tuple[bool, str]:
        action = inputs.get("action", "check")
        if action not in ("check",):
            return False, f"Acción inválida: '{action}'. Válidas: check"
        city = inputs.get("city", "")
        if not city or not isinstance(city, str) or not city.strip():
            return False, "Se requiere el parámetro 'city'"
        return True, ""

    async def execute(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        is_valid, error_msg = self.validate_inputs(inputs)
        if not is_valid:
            return {"success": False, "result": None, "error": error_msg, "execution_time": 0}

        start = time.time()
        city = inputs["city"].strip()
        when = inputs.get("time", "today").strip()

        try:
            query = f"weather in {city} {when}"
            url = f"https://www.google.com/search?q={quote_plus(query)}"
            opened = webbrowser.open(url)
            if not opened:
                raise RuntimeError("webbrowser.open returned False")

            result = {
                "city": city,
                "time": when,
                "url": url,
                "message": f"Mostrando el clima para {city}, {when}.",
            }

            self.execution_count += 1
            self.last_execution = datetime.now()
            return {
                "success": True,
                "result": result,
                "error": None,
                "execution_time": round(time.time() - start, 3),
            }

        except Exception as e:
            logger.error(f"Weather skill error: {e}")
            return {
                "success": False,
                "result": None,
                "error": str(e),
                "execution_time": round(time.time() - start, 3),
            }
