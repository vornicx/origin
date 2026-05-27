"""
ReminderSkill — Schedule OS-level reminders with native notifications.

Actions:
  set    → Create a new reminder at a specific date/time
  list   → List pending reminders
  cancel → Cancel a pending reminder
"""

import json
import logging
import os
import platform
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, Any

from .base_skill import BaseSkill

logger = logging.getLogger("origin.skills")
_OS = platform.system()


def _reminders_dir() -> Path:
    d = Path.home() / ".origin" / "reminders"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _sanitize(text: str, max_len: int = 200) -> str:
    return (
        text.replace("\\", "").replace('"', "").replace("'", "")
        .replace("\n", " ").replace("\r", "").strip()
    )[:max_len]


def _write_notify_script(task_name: str, message: str) -> Path:
    script_path = _reminders_dir() / f"{task_name}.py"
    msg_literal = json.dumps(message)

    if _OS == "Windows":
        notify_block = f"""
message = {msg_literal}
notified = False
try:
    from plyer import notification
    notification.notify(title="Origin Reminder", message=message, timeout=15)
    notified = True
except Exception:
    pass
if not notified:
    try:
        import subprocess
        subprocess.run(["msg", "*", "/TIME:30", message], check=False)
    except Exception:
        pass
try:
    import winsound
    for freq in [800, 1000, 1200]:
        winsound.Beep(freq, 180)
        import time; time.sleep(0.08)
except Exception:
    pass
"""
    elif _OS == "Darwin":
        notify_block = f"""
message = {msg_literal}
try:
    import subprocess
    script = 'display notification "{{0}}" with title "Origin Reminder"'.format(message.replace('"', ''))
    subprocess.run(["osascript", "-e", script], check=False)
except Exception:
    pass
"""
    else:
        notify_block = f"""
message = {msg_literal}
try:
    import subprocess
    subprocess.run(["notify-send", "--urgency=normal", "--expire-time=15000", "Origin Reminder", message], check=False)
except Exception:
    pass
"""

    script_body = f"""import sys, os, pathlib
{notify_block}
try:
    pathlib.Path(__file__).unlink(missing_ok=True)
except Exception:
    pass
"""
    script_path.write_text(script_body, encoding="utf-8")
    return script_path


def _schedule_windows(target_dt: datetime, task_name: str, script_path: Path, message: str) -> str:
    python_exe = Path(sys.executable)
    pythonw = python_exe.parent / "pythonw.exe"
    if pythonw.exists():
        python_exe = pythonw

    xml_path = _reminders_dir() / f"{task_name}.xml"
    xml_content = (
        '<?xml version="1.0" encoding="UTF-16"?>\n'
        '<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">\n'
        '  <RegistrationInfo><Description>Origin Reminder</Description></RegistrationInfo>\n'
        '  <Triggers><TimeTrigger>\n'
        f'    <StartBoundary>{target_dt.strftime("%Y-%m-%dT%H:%M:%S")}</StartBoundary>\n'
        '    <Enabled>true</Enabled>\n'
        '  </TimeTrigger></Triggers>\n'
        '  <Actions><Exec>\n'
        f'    <Command>{python_exe}</Command>\n'
        f'    <Arguments>"{script_path}"</Arguments>\n'
        '  </Exec></Actions>\n'
        '  <Settings>\n'
        '    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>\n'
        '    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>\n'
        '    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>\n'
        '    <StartWhenAvailable>true</StartWhenAvailable>\n'
        '    <ExecutionTimeLimit>PT5M</ExecutionTimeLimit>\n'
        '    <Enabled>true</Enabled>\n'
        '  </Settings>\n'
        '  <Principals><Principal>\n'
        '    <LogonType>InteractiveToken</LogonType>\n'
        '    <RunLevel>LeastPrivilege</RunLevel>\n'
        '  </Principal></Principals>\n'
        '</Task>'
    )
    xml_path.write_text(xml_content, encoding="utf-16")
    result = subprocess.run(
        ["schtasks", "/Create", "/TN", task_name, "/XML", str(xml_path), "/F"],
        capture_output=True, text=True,
    )
    try:
        xml_path.unlink(missing_ok=True)
    except Exception:
        pass
    if result.returncode != 0:
        script_path.unlink(missing_ok=True)
        raise RuntimeError(f"schtasks failed: {(result.stderr or result.stdout).strip()}")
    return task_name


def _schedule_unix(target_dt: datetime, task_name: str, script_path: Path) -> str:
    if _OS == "Darwin":
        agents_dir = Path.home() / "Library" / "LaunchAgents"
        agents_dir.mkdir(parents=True, exist_ok=True)
        label = f"com.origin.reminder.{task_name}"
        plist_path = agents_dir / f"{label}.plist"
        plist_content = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>{label}</string>
  <key>ProgramArguments</key><array><string>{sys.executable}</string><string>{script_path}</string></array>
  <key>StartCalendarInterval</key><dict>
    <key>Year</key><integer>{target_dt.year}</integer>
    <key>Month</key><integer>{target_dt.month}</integer>
    <key>Day</key><integer>{target_dt.day}</integer>
    <key>Hour</key><integer>{target_dt.hour}</integer>
    <key>Minute</key><integer>{target_dt.minute}</integer>
  </dict>
  <key>RunAtLoad</key><false/>
</dict></plist>"""
        plist_path.write_text(plist_content, encoding="utf-8")
        result = subprocess.run(["launchctl", "load", str(plist_path)], capture_output=True, text=True)
        if result.returncode != 0:
            plist_path.unlink(missing_ok=True)
            script_path.unlink(missing_ok=True)
            raise RuntimeError(f"launchctl failed: {result.stderr.strip()}")
        return label

    if shutil.which("systemd-run"):
        on_calendar = target_dt.strftime("%Y-%m-%d %H:%M:00")
        result = subprocess.run(
            ["systemd-run", "--user", f"--on-calendar={on_calendar}", f"--unit={task_name}",
             "--", sys.executable, str(script_path)],
            capture_output=True, text=True,
        )
        if result.returncode == 0:
            return task_name

    if shutil.which("at"):
        at_time = target_dt.strftime("%H:%M %Y-%m-%d")
        result = subprocess.run(["at", at_time], input=f"{sys.executable} {script_path}\n",
                                capture_output=True, text=True)
        if result.returncode == 0:
            return task_name

    raise RuntimeError("No scheduler found (systemd-run or at)")


class ReminderSkill(BaseSkill):

    def __init__(self):
        super().__init__(
            name="reminder",
            description="Programa recordatorios con notificaciones nativas del OS",
        )

    def validate_inputs(self, inputs: Dict[str, Any]) -> tuple[bool, str]:
        action = inputs.get("action", "set")
        if action not in ("set", "list", "cancel"):
            return False, f"Acción inválida: '{action}'. Válidas: set, list, cancel"
        if action == "set":
            if not inputs.get("date") or not inputs.get("time"):
                return False, "Se requiere 'date' (YYYY-MM-DD) y 'time' (HH:MM)"
        if action == "cancel" and not inputs.get("task_name"):
            return False, "Se requiere 'task_name' para cancelar"
        return True, ""

    async def execute(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        is_valid, error_msg = self.validate_inputs(inputs)
        if not is_valid:
            return {"success": False, "result": None, "error": error_msg, "execution_time": 0}

        start = time.time()
        action = inputs.get("action", "set")

        try:
            if action == "set":
                result = self._set_reminder(inputs)
            elif action == "list":
                result = self._list_reminders()
            elif action == "cancel":
                result = self._cancel_reminder(inputs)
            else:
                result = {"error": f"Unknown action: {action}"}

            self.execution_count += 1
            self.last_execution = datetime.now()
            return {
                "success": "error" not in result,
                "result": result,
                "error": result.get("error"),
                "execution_time": round(time.time() - start, 3),
            }
        except Exception as e:
            logger.error(f"Reminder skill error: {e}")
            return {"success": False, "result": None, "error": str(e), "execution_time": round(time.time() - start, 3)}

    def _set_reminder(self, inputs: dict) -> dict:
        date_str = inputs["date"].strip()
        time_str = inputs["time"].strip()
        message = inputs.get("message", "Recordatorio de Origin").strip()

        try:
            target_dt = datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M")
        except ValueError:
            return {"error": "Formato de fecha/hora inválido. Usa YYYY-MM-DD y HH:MM"}

        if target_dt <= datetime.now():
            return {"error": "La fecha/hora ya pasó"}

        safe_msg = _sanitize(message)
        task_name = f"OriginReminder_{target_dt.strftime('%Y%m%d_%H%M%S')}"

        script_path = _write_notify_script(task_name, safe_msg)

        if _OS == "Windows":
            job_id = _schedule_windows(target_dt, task_name, script_path, safe_msg)
        else:
            job_id = _schedule_unix(target_dt, task_name, script_path)

        friendly = target_dt.strftime("%d de %B a las %H:%M")
        return {
            "task_name": job_id,
            "scheduled_for": target_dt.isoformat(),
            "message": safe_msg,
            "friendly": f"Recordatorio programado para {friendly}",
        }

    def _list_reminders(self) -> dict:
        scripts = list(_reminders_dir().glob("OriginReminder_*.py"))
        reminders = []
        for s in scripts:
            name = s.stem
            parts = name.replace("OriginReminder_", "").split("_")
            if len(parts) == 2:
                try:
                    dt = datetime.strptime(f"{parts[0]}_{parts[1]}", "%Y%m%d_%H%M%S")
                    reminders.append({"task_name": name, "scheduled_for": dt.isoformat()})
                except ValueError:
                    reminders.append({"task_name": name})
        return {"count": len(reminders), "reminders": reminders}

    def _cancel_reminder(self, inputs: dict) -> dict:
        task_name = inputs["task_name"].strip()
        script_path = _reminders_dir() / f"{task_name}.py"
        try:
            script_path.unlink(missing_ok=True)
        except Exception:
            pass

        if _OS == "Windows":
            subprocess.run(["schtasks", "/Delete", "/TN", task_name, "/F"],
                           capture_output=True, text=True)
        elif _OS == "Darwin":
            label = f"com.origin.reminder.{task_name}"
            plist = Path.home() / "Library" / "LaunchAgents" / f"{label}.plist"
            subprocess.run(["launchctl", "unload", str(plist)], capture_output=True, text=True)
            try:
                plist.unlink(missing_ok=True)
            except Exception:
                pass

        return {"cancelled": task_name}
