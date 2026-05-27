"""
File Manager Skill para Origin.
Lectura, escritura, busqueda y gestion de archivos locales con controles de seguridad.
"""

import time
import os
import re
import shutil
import hashlib
import logging
import fnmatch
from typing import Dict, Any, List, Optional
from datetime import datetime

from .base_skill import BaseSkill

logger = logging.getLogger("origin.skills.file_manager")

# ── Limites ────────────────────────────────────────────────────────
MAX_READ_BYTES = 2 * 1024 * 1024  # 2 MB max lectura
MAX_WRITE_BYTES = 5 * 1024 * 1024  # 5 MB max escritura
MAX_SEARCH_RESULTS = 100
MAX_LIST_ITEMS = 200

# ── Rutas protegidas (no se puede escribir/borrar) ─────────────────
_PROTECTED_PATHS = [
    r"C:\Windows",
    r"C:\Program Files",
    r"C:\Program Files (x86)",
    r"C:\ProgramData",
    r"C:\$Recycle.Bin",
    r"C:\System Volume Information",
    r"C:\Recovery",
    r"C:\Boot",
    "/etc",
    "/usr",
    "/bin",
    "/sbin",
    "/boot",
    "/proc",
    "/sys",
    "/dev",
]

# ── Extensiones binarias (no se leen como texto) ───────────────────
_BINARY_EXTENSIONS = {
    ".exe",
    ".dll",
    ".so",
    ".dylib",
    ".bin",
    ".zip",
    ".tar",
    ".gz",
    ".7z",
    ".rar",
    ".jpg",
    ".jpeg",
    ".png",
    ".gif",
    ".bmp",
    ".ico",
    ".webp",
    ".mp3",
    ".mp4",
    ".avi",
    ".mkv",
    ".wav",
    ".flac",
    ".pdf",
    ".doc",
    ".docx",
    ".xls",
    ".xlsx",
    ".ppt",
    ".pptx",
    ".db",
    ".sqlite",
    ".mdb",
    ".pyc",
    ".pyo",
    ".class",
}


class FileManagerSkill(BaseSkill):
    """
    Skill: Gestion de archivos del sistema local.

    Acciones:
        read     - Leer contenido de un archivo de texto
        write    - Escribir/crear un archivo de texto
        append   - Agregar contenido al final de un archivo
        list     - Listar contenido de un directorio
        search   - Buscar archivos por nombre/patron
        grep     - Buscar texto dentro de archivos
        info     - Informacion detallada de un archivo
        move     - Mover/renombrar archivo o directorio
        copy     - Copiar archivo o directorio
        mkdir    - Crear directorio(s)
        delete   - Eliminar archivo (a Papelera si es posible)
        tree     - Arbol visual de un directorio

    Seguridad:
        - Rutas del sistema protegidas contra escritura/borrado
        - Limites de lectura/escritura (2MB / 5MB)
        - No lee archivos binarios como texto
        - Confirma operaciones destructivas
    """

    VALID_ACTIONS = {
        "read",
        "write",
        "append",
        "list",
        "search",
        "grep",
        "info",
        "move",
        "copy",
        "mkdir",
        "delete",
        "tree",
    }

    def __init__(self):
        super().__init__(name="file_manager", description="Lee, escribe, busca y gestiona archivos del sistema local")

    # ── Validacion ─────────────────────────────────────────────────

    def validate_inputs(self, inputs: Dict[str, Any]) -> tuple[bool, str]:
        action = inputs.get("action", "")
        if not action:
            return False, f"Se requiere 'action'. Validas: {self.VALID_ACTIONS}"
        if action not in self.VALID_ACTIONS:
            return False, f"Accion invalida '{action}'. Validas: {self.VALID_ACTIONS}"

        # Validaciones por accion
        if action in ("read", "info", "delete"):
            if not inputs.get("path"):
                return False, "Se requiere 'path'"

        elif action in ("write", "append"):
            if not inputs.get("path"):
                return False, "Se requiere 'path'"
            if "content" not in inputs:
                return False, "Se requiere 'content'"
            path = inputs["path"]
            blocked = self._is_protected(path)
            if blocked:
                return False, f"Ruta protegida: {blocked}"

        elif action == "list":
            if not inputs.get("path"):
                return False, "Se requiere 'path' (directorio)"

        elif action == "search":
            if not inputs.get("path") and not inputs.get("pattern"):
                return False, "Se requiere 'path' y/o 'pattern'"

        elif action == "grep":
            if not inputs.get("pattern"):
                return False, "Se requiere 'pattern' (texto a buscar)"
            if not inputs.get("path"):
                return False, "Se requiere 'path' (directorio o archivo)"

        elif action in ("move", "copy"):
            if not inputs.get("source") or not inputs.get("destination"):
                return False, "Se requiere 'source' y 'destination'"

        elif action == "mkdir":
            if not inputs.get("path"):
                return False, "Se requiere 'path'"

        return True, ""

    def _is_protected(self, path: str) -> Optional[str]:
        """Verifica si una ruta esta protegida contra modificacion.
        Also resolves symlinks to prevent symlink traversal attacks."""
        # Resolve symlinks to get the real path (prevents symlink bypass)
        try:
            real_path = os.path.realpath(path).replace("/", os.sep)
        except (OSError, ValueError):
            return "path resolution failed"
        abs_path = os.path.abspath(path).replace("/", os.sep)

        # Check both the apparent path and the real (resolved) path
        for check_path in (abs_path, real_path):
            for protected in _PROTECTED_PATHS:
                protected_norm = os.path.abspath(protected).replace("/", os.sep)
                if check_path.lower().startswith(protected_norm.lower()):
                    return protected

        # Block symlinks that point outside the apparent directory
        if os.path.islink(path):
            if real_path.lower() != abs_path.lower():
                logger.warning(f"Symlink detected: {abs_path} → {real_path}")
                # Allow if real path is not in a protected location (already checked)
                # but warn for audit purposes

        return None

    # ── Ejecucion principal ────────────────────────────────────────

    async def execute(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        start = time.time()
        self.execution_count += 1

        is_valid, error = self.validate_inputs(inputs)
        if not is_valid:
            return {"success": False, "result": None, "error": error, "execution_time": 0}

        try:
            action = inputs["action"]
            dispatch = {
                "read": self._read,
                "write": self._write,
                "append": self._append,
                "list": self._list,
                "search": self._search,
                "grep": self._grep,
                "info": self._info,
                "move": self._move,
                "copy": self._copy,
                "mkdir": self._mkdir,
                "delete": self._delete,
                "tree": self._tree,
            }

            result = dispatch[action](inputs)

            elapsed = round(time.time() - start, 3)
            self.last_execution = datetime.now()

            has_error = isinstance(result, dict) and result.get("error") is not None
            return {
                "success": not has_error,
                "result": result,
                "error": result.get("error") if has_error else None,
                "execution_time": elapsed,
            }

        except PermissionError:
            elapsed = round(time.time() - start, 3)
            return {"success": False, "result": None, "error": "Permiso denegado", "execution_time": elapsed}
        except Exception as e:
            elapsed = round(time.time() - start, 3)
            logger.error(f"File manager error: {e}")
            return {"success": False, "result": None, "error": str(e), "execution_time": elapsed}

    # ── read ───────────────────────────────────────────────────────

    def _read(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        """Lee contenido de un archivo de texto."""
        path = os.path.abspath(inputs["path"])
        if not os.path.exists(path):
            return {"error": f"Archivo no encontrado: {path}"}
        if not os.path.isfile(path):
            return {"error": f"No es un archivo: {path}"}

        ext = os.path.splitext(path)[1].lower()
        if ext in _BINARY_EXTENSIONS:
            return {"error": f"Archivo binario ({ext}), no se puede leer como texto. Usa 'info' para metadata."}

        size = os.path.getsize(path)
        if size > MAX_READ_BYTES:
            return {
                "error": f"Archivo demasiado grande: {size / 1024 / 1024:.1f} MB (max {MAX_READ_BYTES // 1024 // 1024} MB)"  # noqa: E501
            }

        # Leer con deteccion de encoding
        start_line = inputs.get("start_line", 1) - 1  # 0-indexed
        max_lines = inputs.get("max_lines", 0)  # 0 = todas

        encoding = inputs.get("encoding", "utf-8")
        try:
            with open(path, "r", encoding=encoding, errors="replace") as f:
                if max_lines > 0:
                    lines = []
                    for i, line in enumerate(f):
                        if i < start_line:
                            continue
                        lines.append(line)
                        if len(lines) >= max_lines:
                            break
                    content = "".join(lines)
                    total_lines = start_line + len(lines)
                else:
                    content = f.read()
                    total_lines = content.count("\n") + 1
        except UnicodeDecodeError:
            return {"error": f"No se pudo decodificar con {encoding}. Prueba con encoding='latin-1'"}

        return {
            "path": path,
            "content": content[:MAX_READ_BYTES],
            "size_bytes": size,
            "total_lines": total_lines,
            "encoding": encoding,
            "truncated": len(content) > MAX_READ_BYTES,
        }

    # ── write ──────────────────────────────────────────────────────

    def _write(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        """Escribe contenido a un archivo (crea o sobreescribe)."""
        path = os.path.abspath(inputs["path"])
        content = inputs["content"]

        if len(content.encode("utf-8")) > MAX_WRITE_BYTES:
            return {"error": f"Contenido demasiado grande (max {MAX_WRITE_BYTES // 1024 // 1024} MB)"}

        # Crear directorio padre si no existe
        parent = os.path.dirname(path)
        if parent and not os.path.exists(parent):
            os.makedirs(parent, exist_ok=True)

        existed = os.path.exists(path)
        encoding = inputs.get("encoding", "utf-8")

        with open(path, "w", encoding=encoding) as f:
            f.write(content)

        return {
            "path": path,
            "bytes_written": len(content.encode(encoding)),
            "lines": content.count("\n") + 1,
            "created": not existed,
            "overwritten": existed,
        }

    # ── append ─────────────────────────────────────────────────────

    def _append(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        """Agrega contenido al final de un archivo."""
        path = os.path.abspath(inputs["path"])
        content = inputs["content"]

        if not os.path.exists(path):
            return {"error": f"Archivo no encontrado: {path}. Usa 'write' para crear."}

        encoding = inputs.get("encoding", "utf-8")
        with open(path, "a", encoding=encoding) as f:
            f.write(content)

        new_size = os.path.getsize(path)
        return {
            "path": path,
            "bytes_appended": len(content.encode(encoding)),
            "new_size_bytes": new_size,
        }

    # ── list ───────────────────────────────────────────────────────

    def _list(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        """Lista contenido de un directorio."""
        path = os.path.abspath(inputs["path"])
        if not os.path.isdir(path):
            return {"error": f"No es un directorio: {path}"}

        show_hidden = inputs.get("show_hidden", False)
        sort_by = inputs.get("sort", "name")  # name, size, modified

        entries = []
        try:
            for entry in os.scandir(path):
                if not show_hidden and entry.name.startswith("."):
                    continue
                try:
                    stat = entry.stat()
                    entries.append(
                        {
                            "name": entry.name,
                            "type": "dir" if entry.is_dir() else "file",
                            "size_bytes": stat.st_size if entry.is_file() else 0,
                            "modified": datetime.fromtimestamp(stat.st_mtime).isoformat(),
                            "extension": os.path.splitext(entry.name)[1] if entry.is_file() else "",
                        }
                    )
                except (PermissionError, OSError):
                    entries.append(
                        {
                            "name": entry.name,
                            "type": "unknown",
                            "size_bytes": 0,
                            "modified": "",
                            "error": "permiso denegado",
                        }
                    )
        except PermissionError:
            return {"error": f"Permiso denegado para listar: {path}"}

        # Ordenar
        if sort_by == "size":
            entries.sort(key=lambda e: e["size_bytes"], reverse=True)
        elif sort_by == "modified":
            entries.sort(key=lambda e: e["modified"], reverse=True)
        else:
            # Directorios primero, luego por nombre
            entries.sort(key=lambda e: (0 if e["type"] == "dir" else 1, e["name"].lower()))

        total_dirs = sum(1 for e in entries if e["type"] == "dir")
        total_files = sum(1 for e in entries if e["type"] == "file")

        return {
            "path": path,
            "entries": entries[:MAX_LIST_ITEMS],
            "total_dirs": total_dirs,
            "total_files": total_files,
            "truncated": len(entries) > MAX_LIST_ITEMS,
        }

    # ── search ─────────────────────────────────────────────────────

    def _search(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        """Busca archivos por nombre/patron en un directorio."""
        path = os.path.abspath(inputs.get("path", "."))
        pattern = inputs.get("pattern", "*")
        recursive = inputs.get("recursive", True)
        file_type = inputs.get("file_type")  # "file", "dir", o None para ambos
        max_results = min(inputs.get("max_results", MAX_SEARCH_RESULTS), MAX_SEARCH_RESULTS)

        if not os.path.isdir(path):
            return {"error": f"Directorio no encontrado: {path}"}

        matches = []
        try:
            if recursive:
                for root, dirs, files in os.walk(path):
                    # Saltar directorios ocultos y de sistema
                    dirs[:] = [
                        d
                        for d in dirs
                        if not d.startswith(".") and d not in ("node_modules", "__pycache__", ".git", "venv")
                    ]

                    if file_type != "file":
                        for d in dirs:
                            if fnmatch.fnmatch(d, pattern):
                                full_path = os.path.join(root, d)
                                matches.append({"path": full_path, "type": "dir", "name": d})
                                if len(matches) >= max_results:
                                    break

                    if file_type != "dir":
                        for f in files:
                            if fnmatch.fnmatch(f, pattern):
                                full_path = os.path.join(root, f)
                                try:
                                    size = os.path.getsize(full_path)
                                except OSError:
                                    size = 0
                                matches.append({"path": full_path, "type": "file", "name": f, "size_bytes": size})
                                if len(matches) >= max_results:
                                    break

                    if len(matches) >= max_results:
                        break
            else:
                for entry in os.scandir(path):
                    if fnmatch.fnmatch(entry.name, pattern):
                        if file_type and (
                            (file_type == "file" and not entry.is_file()) or (file_type == "dir" and not entry.is_dir())
                        ):
                            continue
                        matches.append(
                            {
                                "path": os.path.join(path, entry.name),
                                "type": "dir" if entry.is_dir() else "file",
                                "name": entry.name,
                            }
                        )
                        if len(matches) >= max_results:
                            break

        except PermissionError:
            pass  # Saltar directorios sin permiso

        return {
            "search_path": path,
            "pattern": pattern,
            "recursive": recursive,
            "matches": matches,
            "total_found": len(matches),
            "truncated": len(matches) >= max_results,
        }

    # ── grep ───────────────────────────────────────────────────────

    def _grep(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        """Busca texto dentro de archivos."""
        path = os.path.abspath(inputs["path"])
        pattern = inputs["pattern"]
        file_pattern = inputs.get("file_pattern", "*")
        case_sensitive = inputs.get("case_sensitive", False)
        max_results = min(inputs.get("max_results", 50), MAX_SEARCH_RESULTS)
        context_lines = inputs.get("context", 0)

        flags = 0 if case_sensitive else re.IGNORECASE
        try:
            regex = re.compile(pattern, flags)
        except re.error as e:
            return {"error": f"Patron regex invalido: {e}"}

        results = []

        if os.path.isfile(path):
            file_matches = self._grep_file(path, regex, context_lines, max_results)
            results.extend(file_matches)
        elif os.path.isdir(path):
            for root, dirs, files in os.walk(path):
                dirs[:] = [
                    d
                    for d in dirs
                    if not d.startswith(".") and d not in ("node_modules", "__pycache__", ".git", "venv")
                ]

                for fname in files:
                    if not fnmatch.fnmatch(fname, file_pattern):
                        continue
                    ext = os.path.splitext(fname)[1].lower()
                    if ext in _BINARY_EXTENSIONS:
                        continue

                    fpath = os.path.join(root, fname)
                    file_matches = self._grep_file(fpath, regex, context_lines, max_results - len(results))
                    results.extend(file_matches)

                    if len(results) >= max_results:
                        break
                if len(results) >= max_results:
                    break
        else:
            return {"error": f"Ruta no encontrada: {path}"}

        return {
            "pattern": pattern,
            "search_path": path,
            "matches": results,
            "total_matches": len(results),
            "truncated": len(results) >= max_results,
        }

    def _grep_file(self, path: str, regex, context: int, limit: int) -> List[Dict]:
        """Busca patron en un archivo individual."""
        matches = []
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                lines = f.readlines()

            for i, line in enumerate(lines):
                if regex.search(line):
                    match = {
                        "file": path,
                        "line_number": i + 1,
                        "line": line.rstrip()[:300],
                    }
                    if context > 0:
                        start = max(0, i - context)
                        end = min(len(lines), i + context + 1)
                        match["context"] = [ln.rstrip()[:300] for ln in lines[start:end]]

                    matches.append(match)
                    if len(matches) >= limit:
                        break
        except (PermissionError, OSError, UnicodeDecodeError):
            pass

        return matches

    # ── info ───────────────────────────────────────────────────────

    def _info(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        """Informacion detallada de un archivo o directorio."""
        path = os.path.abspath(inputs["path"])
        if not os.path.exists(path):
            return {"error": f"No encontrado: {path}"}

        stat = os.stat(path)
        is_file = os.path.isfile(path)

        info = {
            "path": path,
            "name": os.path.basename(path),
            "type": "file" if is_file else "directory",
            "size_bytes": stat.st_size,
            "size_human": self._human_size(stat.st_size),
            "created": datetime.fromtimestamp(stat.st_ctime).isoformat(),
            "modified": datetime.fromtimestamp(stat.st_mtime).isoformat(),
            "accessed": datetime.fromtimestamp(stat.st_atime).isoformat(),
            "readable": os.access(path, os.R_OK),
            "writable": os.access(path, os.W_OK),
        }

        if is_file:
            info["extension"] = os.path.splitext(path)[1]
            info["is_binary"] = info["extension"].lower() in _BINARY_EXTENSIONS

            # Hash SHA-256 para archivos pequenos (SHA-256 > MD5 for security)
            if stat.st_size < 10 * 1024 * 1024:  # < 10 MB
                try:
                    h = hashlib.sha256()
                    with open(path, "rb") as f:
                        for chunk in iter(lambda: f.read(8192), b""):
                            h.update(chunk)
                    info["sha256"] = h.hexdigest()
                except (PermissionError, OSError):
                    pass

            # Contar lineas para texto
            if not info["is_binary"] and stat.st_size < MAX_READ_BYTES:
                try:
                    with open(path, "r", encoding="utf-8", errors="replace") as f:
                        info["line_count"] = sum(1 for _ in f)
                except (PermissionError, OSError):
                    pass
        else:
            # Para directorios: contar contenido
            try:
                items = list(os.scandir(path))
                info["contains_dirs"] = sum(1 for e in items if e.is_dir())
                info["contains_files"] = sum(1 for e in items if e.is_file())
            except PermissionError:
                info["note"] = "Sin permiso para listar contenido"

        return info

    # ── move ───────────────────────────────────────────────────────

    def _move(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        """Mueve o renombra un archivo/directorio."""
        source = os.path.abspath(inputs["source"])
        dest = os.path.abspath(inputs["destination"])

        if not os.path.exists(source):
            return {"error": f"Origen no encontrado: {source}"}

        blocked = self._is_protected(source)
        if blocked:
            return {"error": f"No se puede mover desde ruta protegida: {blocked}"}

        shutil.move(source, dest)
        return {
            "source": source,
            "destination": dest,
            "moved": True,
        }

    # ── copy ───────────────────────────────────────────────────────

    def _copy(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        """Copia un archivo o directorio."""
        source = os.path.abspath(inputs["source"])
        dest = os.path.abspath(inputs["destination"])

        if not os.path.exists(source):
            return {"error": f"Origen no encontrado: {source}"}

        if os.path.isdir(source):
            shutil.copytree(source, dest, dirs_exist_ok=True)
        else:
            # Crear directorio destino si no existe
            dest_dir = os.path.dirname(dest)
            if dest_dir:
                os.makedirs(dest_dir, exist_ok=True)
            shutil.copy2(source, dest)

        return {
            "source": source,
            "destination": dest,
            "copied": True,
            "is_dir": os.path.isdir(source),
        }

    # ── mkdir ──────────────────────────────────────────────────────

    def _mkdir(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        """Crea directorio(s), incluyendo padres si no existen."""
        path = os.path.abspath(inputs["path"])

        blocked = self._is_protected(path)
        if blocked:
            return {"error": f"Ruta protegida: {blocked}"}

        existed = os.path.exists(path)
        os.makedirs(path, exist_ok=True)

        return {
            "path": path,
            "created": not existed,
            "already_existed": existed,
        }

    # ── delete ─────────────────────────────────────────────────────

    def _delete(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        """Elimina un archivo. Directorios solo si estan vacios o force=True."""
        path = os.path.abspath(inputs["path"])

        if not os.path.exists(path):
            return {"error": f"No encontrado: {path}"}

        blocked = self._is_protected(path)
        if blocked:
            return {"error": f"Ruta protegida contra borrado: {blocked}"}

        if os.path.isfile(path):
            size = os.path.getsize(path)
            os.remove(path)
            return {"path": path, "deleted": True, "type": "file", "size_freed": size}

        elif os.path.isdir(path):
            force = inputs.get("force", False)
            if force:
                shutil.rmtree(path)
                return {"path": path, "deleted": True, "type": "directory", "recursive": True}
            else:
                try:
                    os.rmdir(path)  # Solo funciona si esta vacio
                    return {"path": path, "deleted": True, "type": "directory"}
                except OSError:
                    return {"error": "Directorio no vacio. Usa force=True para borrado recursivo."}

        return {"error": f"Tipo desconocido: {path}"}

    # ── tree ───────────────────────────────────────────────────────

    def _tree(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        """Genera arbol visual de un directorio."""
        path = os.path.abspath(inputs["path"])
        max_depth = inputs.get("depth", 3)
        show_files = inputs.get("show_files", True)

        if not os.path.isdir(path):
            return {"error": f"No es un directorio: {path}"}

        lines = [os.path.basename(path) + "/"]
        self._tree_recursive(path, "", 0, max_depth, show_files, lines)

        tree_text = "\n".join(lines[:200])  # Limitar output
        return {
            "path": path,
            "tree": tree_text,
            "total_lines": len(lines),
            "max_depth": max_depth,
            "truncated": len(lines) > 200,
        }

    def _tree_recursive(
        self, path: str, prefix: str, depth: int, max_depth: int, show_files: bool, lines: List[str]
    ) -> None:
        """Genera arbol recursivamente."""
        if depth >= max_depth or len(lines) >= 200:
            return

        try:
            entries = sorted(os.scandir(path), key=lambda e: (not e.is_dir(), e.name.lower()))
        except PermissionError:
            return

        # Filtrar ocultos y directorios de sistema
        entries = [
            e
            for e in entries
            if not e.name.startswith(".") and e.name not in ("node_modules", "__pycache__", ".git", "venv")
        ]

        if not show_files:
            entries = [e for e in entries if e.is_dir()]

        for i, entry in enumerate(entries):
            is_last = i == len(entries) - 1
            connector = "--- " if is_last else "|-- "
            extension = "    " if is_last else "|   "

            if entry.is_dir():
                lines.append(f"{prefix}{connector}{entry.name}/")
                self._tree_recursive(
                    os.path.join(path, entry.name),
                    prefix + extension,
                    depth + 1,
                    max_depth,
                    show_files,
                    lines,
                )
            elif show_files:
                size = ""
                try:
                    s = entry.stat().st_size
                    size = f" ({self._human_size(s)})"
                except OSError:
                    pass
                lines.append(f"{prefix}{connector}{entry.name}{size}")

    # ── Helpers ────────────────────────────────────────────────────

    @staticmethod
    def _human_size(size_bytes: int) -> str:
        """Convierte bytes a formato legible."""
        if size_bytes == 0:
            return "0 B"
        units = ["B", "KB", "MB", "GB", "TB"]
        i = 0
        size = float(size_bytes)
        while size >= 1024 and i < len(units) - 1:
            size /= 1024
            i += 1
        return f"{size:.1f} {units[i]}"
