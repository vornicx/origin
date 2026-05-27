"""
DesktopSkill — Desktop management: wallpaper, organize files, list, stats.

Actions:
  wallpaper         → Set wallpaper from local path
  wallpaper_url     → Set wallpaper from a URL
  current_wallpaper → Get current wallpaper path
  organize          → Organize desktop files by type or date
  clean             → Archive all desktop files into a dated folder
  list              → List desktop contents
  stats             → Desktop file statistics
"""

import logging
import os
import platform
import shutil
import subprocess
import tempfile
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, Any

from .base_skill import BaseSkill

logger = logging.getLogger("origin.skills")
_OS = platform.system()

_FILE_TYPE_MAP = {
    "Images": {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".svg", ".ico", ".heic"},
    "Documents": {".pdf", ".doc", ".docx", ".txt", ".xls", ".xlsx", ".ppt", ".pptx", ".csv", ".odt"},
    "Videos": {".mp4", ".avi", ".mkv", ".mov", ".wmv", ".flv", ".webm"},
    "Music": {".mp3", ".wav", ".flac", ".aac", ".ogg", ".wma", ".m4a"},
    "Archives": {".zip", ".rar", ".7z", ".tar", ".gz", ".bz2"},
    "Code": {".py", ".js", ".ts", ".html", ".css", ".json", ".xml", ".cpp", ".java", ".go", ".rs"},
    "Executables": {".exe", ".msi", ".bat", ".cmd", ".sh", ".appimage", ".deb"},
}

_SKIP_EXT = {
    "Windows": {".lnk", ".url"},
    "Darwin": {".webloc"},
    "Linux": {".desktop"},
}


def _desktop_path() -> Path:
    if _OS == "Linux":
        xdg = os.environ.get("XDG_DESKTOP_DIR", "")
        if xdg and Path(xdg).exists():
            return Path(xdg)
    return Path.home() / "Desktop"


class DesktopSkill(BaseSkill):

    def __init__(self):
        super().__init__(
            name="desktop",
            description="Gestión del escritorio: wallpaper, organizar archivos, listar, estadísticas",
        )

    def validate_inputs(self, inputs: Dict[str, Any]) -> tuple[bool, str]:
        action = inputs.get("action", "")
        valid = ("wallpaper", "wallpaper_url", "current_wallpaper", "organize", "clean", "list", "stats")
        if action not in valid:
            return False, f"Acción inválida: '{action}'. Válidas: {valid}"
        if action == "wallpaper" and not inputs.get("path"):
            return False, "Se requiere 'path' para wallpaper"
        if action == "wallpaper_url" and not inputs.get("url"):
            return False, "Se requiere 'url' para wallpaper_url"
        return True, ""

    async def execute(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        is_valid, error_msg = self.validate_inputs(inputs)
        if not is_valid:
            return {"success": False, "result": None, "error": error_msg, "execution_time": 0}

        start = time.time()
        action = inputs["action"]

        try:
            if action == "wallpaper":
                result = self._set_wallpaper(inputs["path"])
            elif action == "wallpaper_url":
                result = self._set_wallpaper_url(inputs["url"])
            elif action == "current_wallpaper":
                result = self._get_wallpaper()
            elif action == "organize":
                result = self._organize(inputs.get("mode", "by_type"))
            elif action == "clean":
                result = self._clean()
            elif action == "list":
                result = self._list()
            elif action == "stats":
                result = self._stats()
            else:
                result = {"error": f"Unknown: {action}"}

            self.execution_count += 1
            self.last_execution = datetime.now()
            return {
                "success": "error" not in result,
                "result": result,
                "error": result.get("error"),
                "execution_time": round(time.time() - start, 3),
            }
        except Exception as e:
            logger.error(f"Desktop skill error: {e}")
            return {"success": False, "result": None, "error": str(e), "execution_time": round(time.time() - start, 3)}

    def _set_wallpaper(self, image_path: str) -> dict:
        path = Path(image_path).expanduser().resolve()
        if not path.exists():
            return {"error": f"Imagen no encontrada: {image_path}"}
        if path.suffix.lower() not in {".jpg", ".jpeg", ".png", ".bmp", ".webp"}:
            return {"error": f"Formato no soportado: {path.suffix}"}

        if _OS == "Windows":
            import ctypes
            ctypes.windll.user32.SystemParametersInfoW(20, 0, str(path), 3)
        elif _OS == "Darwin":
            script = f'tell application "System Events" to tell every desktop to set picture to POSIX file "{path}"'
            subprocess.run(["osascript", "-e", script], capture_output=True)
        else:
            desktop_env = os.environ.get("XDG_CURRENT_DESKTOP", "").lower()
            uri = f"file://{path}"
            if "gnome" in desktop_env or "unity" in desktop_env:
                subprocess.run(["gsettings", "set", "org.gnome.desktop.background", "picture-uri", uri], capture_output=True)
                subprocess.run(["gsettings", "set", "org.gnome.desktop.background", "picture-uri-dark", uri], capture_output=True)
            else:
                subprocess.run(["feh", "--bg-scale", str(path)], capture_output=True)

        return {"wallpaper": str(path), "message": f"Wallpaper configurado: {path.name}"}

    def _set_wallpaper_url(self, url: str) -> dict:
        import urllib.request
        suffix = Path(url.split("?")[0]).suffix or ".jpg"
        tmp = Path(tempfile.mktemp(suffix=suffix))
        urllib.request.urlretrieve(url, str(tmp))
        result = self._set_wallpaper(str(tmp))
        try:
            tmp.unlink()
        except Exception:
            pass
        return result

    def _get_wallpaper(self) -> dict:
        if _OS == "Windows":
            try:
                import winreg
                key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Control Panel\Desktop")
                val, _ = winreg.QueryValueEx(key, "Wallpaper")
                winreg.CloseKey(key)
                return {"wallpaper": val}
            except Exception as e:
                return {"error": str(e)}
        elif _OS == "Darwin":
            result = subprocess.run(
                ["osascript", "-e", 'tell application "System Events" to get picture of desktop 1'],
                capture_output=True, text=True,
            )
            return {"wallpaper": result.stdout.strip()}
        else:
            result = subprocess.run(
                ["gsettings", "get", "org.gnome.desktop.background", "picture-uri"],
                capture_output=True, text=True,
            )
            return {"wallpaper": result.stdout.strip()}

    def _organize(self, mode: str = "by_type") -> dict:
        desktop = _desktop_path()
        skip_exts = _SKIP_EXT.get(_OS, set())
        moved = []

        for item in desktop.iterdir():
            if item.is_dir() or item.name.startswith("."):
                continue
            if item.suffix.lower() in skip_exts:
                continue

            if mode == "by_date":
                mtime = datetime.fromtimestamp(item.stat().st_mtime)
                folder_name = mtime.strftime("%Y-%m")
            else:
                ext = item.suffix.lower()
                folder_name = "Others"
                for folder, exts in _FILE_TYPE_MAP.items():
                    if ext in exts:
                        folder_name = folder
                        break

            target_dir = desktop / folder_name
            target_dir.mkdir(exist_ok=True)
            new_path = target_dir / item.name
            if not new_path.exists():
                shutil.move(str(item), str(new_path))
                moved.append(f"{item.name} → {folder_name}/")

        return {"mode": mode, "moved": len(moved), "files": moved[:10], "message": f"Escritorio organizado ({mode}): {len(moved)} archivos movidos"}

    def _clean(self) -> dict:
        desktop = _desktop_path()
        skip_exts = _SKIP_EXT.get(_OS, set())
        today = datetime.now().strftime("%Y-%m-%d")
        archive_dir = desktop / f"Desktop Archive {today}"
        archive_dir.mkdir(exist_ok=True)

        moved = 0
        for item in desktop.iterdir():
            if item.is_dir() or item.name.startswith("."):
                continue
            if item.suffix.lower() in skip_exts:
                continue
            new_path = archive_dir / item.name
            if not new_path.exists():
                shutil.move(str(item), str(new_path))
                moved += 1

        return {"moved": moved, "archive": str(archive_dir), "message": f"Escritorio limpiado: {moved} archivos archivados"}

    def _list(self) -> dict:
        desktop = _desktop_path()
        items = []
        for item in sorted(desktop.iterdir()):
            if item.name.startswith("."):
                continue
            if item.is_dir():
                try:
                    count = len(list(item.iterdir()))
                except PermissionError:
                    count = 0
                items.append({"name": item.name, "type": "folder", "count": count})
            else:
                size = item.stat().st_size
                items.append({"name": item.name, "type": "file", "size": size})

        return {"count": len(items), "items": items}

    def _stats(self) -> dict:
        desktop = _desktop_path()
        files = [i for i in desktop.iterdir() if i.is_file()]
        folders = [i for i in desktop.iterdir() if i.is_dir()]
        total_size = sum(f.stat().st_size for f in files if f.exists())

        return {
            "files": len(files),
            "folders": len(folders),
            "total_size_bytes": total_size,
            "total_size_human": f"{total_size / (1024*1024):.1f} MB" if total_size >= 1024*1024 else f"{total_size / 1024:.1f} KB",
            "path": str(desktop),
        }
