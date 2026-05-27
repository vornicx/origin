import time
from datetime import datetime, timedelta, timezone
from typing import Dict, Any

try:
    from zoneinfo import ZoneInfo
except ImportError:
    ZoneInfo = None

from .base_skill import BaseSkill


class DateTimeSkill(BaseSkill):
    """
    Skill: Fecha, hora, zona horaria y cálculos temporales.
    No requiere API externa.
    """

    def __init__(self):
        super().__init__(
            name="datetime",
            description="Obtiene fecha/hora actual, convierte zonas horarias, calcula diferencias temporales",
        )

    def validate_inputs(self, inputs: Dict[str, Any]) -> tuple[bool, str]:
        action = inputs.get("action", "now")
        valid_actions = {"now", "convert", "diff", "add"}
        if action not in valid_actions:
            return False, f"Acción inválida. Válidas: {valid_actions}"
        return True, ""

    async def execute(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        start = time.time()
        self.execution_count += 1

        is_valid, error = self.validate_inputs(inputs)
        if not is_valid:
            return {"success": False, "result": None, "error": error, "execution_time": 0}

        try:
            action = inputs.get("action", "now")
            result = {}

            if action == "now":
                tz_name = inputs.get("timezone", "local")

                # Try to use ZoneInfo, fallback to UTC if not available
                try:
                    if ZoneInfo and tz_name.lower() != "local":
                        tz = ZoneInfo(tz_name)
                        now = datetime.now(tz)
                        tz_display = tz_name
                    elif tz_name.lower() == "utc":
                        now = datetime.now(timezone.utc)
                        tz_display = "UTC"
                    else:
                        # Use local time
                        now = datetime.now()
                        tz_display = "local"
                except Exception:
                    # Fallback: use UTC
                    now = datetime.now(timezone.utc)
                    tz_display = "UTC"

                result = {
                    "datetime": now.isoformat(),
                    "date": now.strftime("%Y-%m-%d"),
                    "time": now.strftime("%H:%M:%S"),
                    "day_of_week": now.strftime("%A"),
                    "timezone": tz_display,
                    "unix_timestamp": int(now.timestamp()),
                }

            elif action == "convert":
                from_tz_name = inputs.get("from_timezone", "UTC")
                to_tz_name = inputs.get("to_timezone", "UTC")
                dt_str = inputs.get("datetime_str", datetime.now(timezone.utc).isoformat())

                try:
                    if ZoneInfo:
                        from_tz = ZoneInfo(from_tz_name)
                        to_tz = ZoneInfo(to_tz_name)
                    else:
                        from_tz = timezone.utc
                        to_tz = timezone.utc
                except Exception:
                    from_tz = timezone.utc
                    to_tz = timezone.utc

                dt = datetime.fromisoformat(dt_str).replace(tzinfo=from_tz)
                converted = dt.astimezone(to_tz)
                result = {
                    "original": dt.isoformat(),
                    "converted": converted.isoformat(),
                    "from_timezone": from_tz_name,
                    "to_timezone": to_tz_name,
                }

            elif action == "diff":
                date1 = datetime.fromisoformat(inputs.get("date1", datetime.now().isoformat()))
                date2 = datetime.fromisoformat(inputs.get("date2", datetime.now().isoformat()))
                diff = abs(date2 - date1)
                result = {
                    "days": diff.days,
                    "hours": diff.seconds // 3600,
                    "minutes": (diff.seconds % 3600) // 60,
                    "total_seconds": int(diff.total_seconds()),
                }

            elif action == "add":
                base = datetime.fromisoformat(inputs.get("base_date", datetime.now().isoformat()))
                delta = timedelta(
                    days=inputs.get("days", 0),
                    hours=inputs.get("hours", 0),
                    minutes=inputs.get("minutes", 0),
                )
                new_date = base + delta
                result = {
                    "base": base.isoformat(),
                    "result": new_date.isoformat(),
                    "delta": str(delta),
                }

            elapsed = round(time.time() - start, 4)
            self.last_execution = datetime.now()
            return {"success": True, "result": result, "error": None, "execution_time": elapsed}

        except Exception as e:
            elapsed = round(time.time() - start, 4)
            return {"success": False, "result": None, "error": str(e), "execution_time": elapsed}
