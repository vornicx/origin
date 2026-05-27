"""
ContextFiles — Sistema de archivos de contexto (inspirado en AGENTS.md de Hermes Agent).

Permite que el usuario defina archivos de contexto persistentes que se inyectan
en cada conversación. Similar a como Hermes Agent usa AGENTS.md, .hermes/context/.

Tipos de archivos de contexto:
  - AGENTS.md     → Instrucciones generales para el agente (como system prompt persistente)
  - workspace.md  → Contexto del proyecto actual
  - user.md       → Preferencias del usuario (auto-generado por personality_learner)

Cada archivo de contexto se inyecta como mensaje system al inicio del reasoning loop.
"""

import logging
from typing import Dict, Any, List, Optional
from pathlib import Path

logger = logging.getLogger("origin.core.context")

CONTEXT_DIR = Path(__file__).parent.parent / "context"
CONTEXT_DIR.mkdir(parents=True, exist_ok=True)

MAX_CONTEXT_FILE_CHARS = 100_000   # 100 KB per file
MAX_TOTAL_CONTEXT_CHARS = 500_000  # 500 KB total across all files

# Orden de carga por prioridad (menor número = primero en el prompt)
_CONTEXT_PRIORITY = {
    "AGENTS.md": 10,
    "workspace.md": 20,
    "user.md": 30,
    "session.md": 40,
}


class ContextManager:
    """Gestiona archivos de contexto que se inyectan en cada interacción.

    Uso:
        ctx = ContextManager()
        context = ctx.get_all_context()  # Lista de dicts {role, content}
        ctx.set_context("workspace.md", "Estamos trabajando en el proyecto X")
    """

    def __init__(self):
        self._files: Dict[str, str] = {}
        self._load_all()

    def _load_all(self):
        """Carga todos los archivos de contexto existentes."""
        for pattern in ["*.md", "*.txt"]:
            for file_path in CONTEXT_DIR.glob(pattern):
                try:
                    content = file_path.read_text(encoding="utf-8", errors="replace").strip()
                    if content:
                        if len(content) > MAX_CONTEXT_FILE_CHARS:
                            logger.warning(
                                f"Context file '{file_path.name}' too large ({len(content)} chars), skipping"
                            )
                            continue
                        self._files[file_path.name] = content
                        logger.debug(f"Context loaded: {file_path.name} ({len(content)} chars)")
                except Exception as e:
                    logger.warning(f"Error loading context {file_path.name}: {e}")

    def get_context(self) -> List[Dict[str, str]]:
        """Retorna todos los contextos como lista de mensajes system."""
        result = []
        sorted_files = sorted(
            self._files.items(),
            key=lambda x: _CONTEXT_PRIORITY.get(x[0], 50),
        )
        for filename, content in sorted_files:
            result.append(
                {
                    "role": "system",
                    "content": f"[Contexto: {filename}]\n{content}",
                }
            )
        return result

    def get(self, filename: str) -> Optional[str]:
        return self._files.get(filename)

    @staticmethod
    def _safe_filename(filename: str) -> str:
        """Strip path separators and traversal sequences, keep only the basename."""
        from pathlib import PurePosixPath
        safe = filename.replace("\\", "/").replace("..", "")
        safe = PurePosixPath(safe).name
        if not safe:
            raise ValueError("Invalid context filename")
        return safe

    def set_context(self, filename: str, content: str) -> bool:
        """Establece o actualiza un archivo de contexto.

        Returns False (and logs a warning) if size limits would be exceeded.
        """
        filename = self._safe_filename(filename)
        if len(content) > MAX_CONTEXT_FILE_CHARS:
            logger.warning(
                f"Context file '{filename}' rejected: {len(content)} chars exceeds limit of {MAX_CONTEXT_FILE_CHARS}"
            )
            return False

        # Check total size after this update
        current_total = sum(len(c) for n, c in self._files.items() if n != filename)
        if current_total + len(content) > MAX_TOTAL_CONTEXT_CHARS:
            logger.warning(
                f"Context file '{filename}' rejected: would exceed total limit of {MAX_TOTAL_CONTEXT_CHARS} chars"
            )
            return False

        self._files[filename] = content
        file_path = CONTEXT_DIR / filename
        try:
            file_path.write_text(content, encoding="utf-8")
            logger.info(f"Context saved: {filename} ({len(content)} chars)")
        except Exception as e:
            logger.error(f"Error saving context {filename}: {e}")
        return True

    def delete_context(self, filename: str) -> bool:
        """Elimina un archivo de contexto."""
        filename = self._safe_filename(filename)
        if filename in self._files:
            del self._files[filename]
            file_path = CONTEXT_DIR / filename
            if file_path.exists():
                file_path.unlink()
            logger.info(f"Context deleted: {filename}")
            return True
        return False

    def list_contexts(self) -> List[Dict[str, Any]]:
        """Lista todos los contextos disponibles."""
        return [
            {
                "filename": name,
                "chars": len(content),
                "priority": _CONTEXT_PRIORITY.get(name, 50),
                "lines": content.count("\n") + 1,
            }
            for name, content in sorted(
                self._files.items(),
                key=lambda x: _CONTEXT_PRIORITY.get(x[0], 50),
            )
        ]

    def get_formatted_prompt(self) -> str:
        """Retorna todo el contexto como string formateado para el prompt."""
        parts = []
        for ctx in self.get_context():
            parts.append(ctx["content"])
        return "\n\n".join(parts)
