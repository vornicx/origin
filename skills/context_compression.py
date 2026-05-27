"""
ContextCompression — Compresión de contexto conversacional (inspirado en Hermes Agent).

Cuando la conversación crece demasiado, comprime turnos antiguos en resúmenes
densos, preservando hechos, decisiones y contexto técnico.

Estrategias:
  - rolling:   Comprime los N turnos más antiguos en un resumen
  - semantic:  Comprime por temas/sesiones detectadas
  - adaptive:  Comprime cuando se supera un umbral de tokens
"""

import logging
from typing import Dict, Any, Optional, List
from datetime import datetime

logger = logging.getLogger("origin.skills.compression")

# Estimación rápida de tokens (4 chars ~= 1 token para español/inglés)
_CHARS_PER_TOKEN = 4
_MAX_TOKENS = 8000  # Umbral por defecto
_COMPRESSION_RATIO = 0.3  # Comprimir al 30% del tamaño original


def estimate_tokens(text: str) -> int:
    return len(text) // _CHARS_PER_TOKEN


class ContextCompressor:
    """Comprime historial conversacional para evitar exceder ventana de contexto.

    Uso:
        compressor = ContextCompressor(llm_router)
        compressed = await compressor.compress(conversation_history, max_tokens=8000)
    """

    def __init__(self, llm_router):
        self._llm = llm_router
        self._compression_count = 0

    @property
    def stats(self) -> Dict[str, Any]:
        return {"compression_count": self._compression_count}

    async def compress(
        self, conversation: List[Dict[str, Any]], max_tokens: int = _MAX_TOKENS, strategy: str = "rolling"
    ) -> List[Dict[str, Any]]:
        """Comprime la conversación si excede max_tokens.

        Retorna la lista comprimida (turnos recientes intactos + resumen).
        """
        if not conversation:
            return conversation

        total_tokens = sum(estimate_tokens(str(m)) for m in conversation)
        if total_tokens <= max_tokens:
            return conversation

        logger.info(f"ContextCompression: {total_tokens} tokens > {max_tokens} max. " f"Comprimiendo ({strategy})...")

        if strategy == "rolling":
            return await self._compress_rolling(conversation, max_tokens)
        elif strategy == "semantic":
            return await self._compress_semantic(conversation, max_tokens)
        else:
            return await self._compress_rolling(conversation, max_tokens)

    async def _compress_rolling(self, conversation: List[Dict[str, Any]], max_tokens: int) -> List[Dict[str, Any]]:
        """Comprime turnos antiguos, mantiene los recientes intactos."""
        # Calcular cuántos turnos recientes mantener
        recent_tokens = 0
        keep_recent = 0
        for m in reversed(conversation):
            tokens = estimate_tokens(str(m))
            if recent_tokens + tokens > int(max_tokens * 0.6):
                break
            recent_tokens += tokens
            keep_recent += 1

        if keep_recent == 0:
            keep_recent = min(3, len(conversation))

        # Separar: antiguos (a comprimir) vs recientes (intactos)
        old = conversation[:-keep_recent] if keep_recent < len(conversation) else []
        recent = conversation[-keep_recent:]

        if not old:
            return conversation

        # Comprimir los antiguos
        summary = await self._summarize_turns(old)
        if summary:
            self._compression_count += 1
            compressed = [{"role": "system", "content": f"[Contexto comprimido de sesiones anteriores]\n{summary}"}]
            compressed.extend(recent)
            logger.info(f"ContextCompression: {len(old)} turnos → resumen ({len(summary)} chars)")
            return compressed

        return conversation

    async def _compress_semantic(self, conversation: List[Dict[str, Any]], max_tokens: int) -> List[Dict[str, Any]]:
        """Comprime por segmentos temáticos."""
        # Detectar cambios de tema
        segments = self._detect_segments(conversation)

        if len(segments) <= 1:
            return await self._compress_rolling(conversation, max_tokens)

        # Mantener el último segmento intacto, comprimir los anteriores
        recent_segment = segments[-1]
        old_segments = segments[:-1]

        if not old_segments:
            return conversation

        summaries = []
        for i, seg in enumerate(old_segments):
            summary = await self._summarize_turns(seg)
            if summary:
                topic = self._detect_topic(seg)
                summaries.append(f"[Tema: {topic}]\n{summary}")

        if summaries:
            self._compression_count += 1
            compressed = [{"role": "system", "content": "--- Sesiones anteriores ---\n" + "\n\n".join(summaries)}]
            compressed.extend(recent_segment)
            return compressed

        return conversation

    def _detect_segments(self, conversation: List[Dict[str, Any]]) -> List[List[Dict[str, Any]]]:
        """Detecta cambios de tema en la conversación."""
        if not conversation:
            return []

        segments = [[conversation[0]]]
        time_gap_min = 30  # minutos sin actividad = nuevo segmento

        for i in range(1, len(conversation)):
            msg = conversation[i]
            prev = conversation[i - 1]

            # Detectar cambio por gap temporal
            ts_current = self._get_timestamp(msg)
            ts_prev = self._get_timestamp(prev)
            if ts_current and ts_prev:
                gap = (ts_current - ts_prev).total_seconds() / 60
                if gap > time_gap_min:
                    segments.append([])

            # Detectar cambios de tema explícitos
            content = msg.get("content", "")
            if isinstance(content, str) and any(
                content.lower().startswith(w)
                for w in [
                    "cambiando de tema",
                    "ahora",
                    "por cierto",
                    "otra cosa",
                    "cambio de tema",
                    "cambiemos",
                    "hablando de otra cosa",
                ]
            ):
                segments.append([])

            segments[-1].append(msg)

        return [s for s in segments if s]

    def _detect_topic(self, segment: List[Dict[str, Any]]) -> str:
        """Detecta el tema principal de un segmento."""
        content = " ".join(m.get("content", "") for m in segment if isinstance(m.get("content"), str))
        words = content.lower().split()[:50]
        # Palabras clave técnicas
        tech_keywords = [
            "python",
            "código",
            "código",
            "error",
            "bug",
            "api",
            "función",
            "funcion",
            "clase",
            "script",
            "windows",
            "docker",
            "git",
            "servidor",
            "archivo",
            "skill",
        ]
        found = [w for w in tech_keywords if w in words]
        return found[0].capitalize() if found else "Conversación"

    def _get_timestamp(self, msg: Dict[str, Any]):
        """Extrae timestamp de un mensaje."""
        ts = msg.get("timestamp")
        if ts:
            try:
                return datetime.fromisoformat(ts)
            except (ValueError, TypeError):
                pass
        return None

    async def _summarize_turns(self, turns: List[Dict[str, Any]]) -> Optional[str]:
        """Usa el LLM para resumir un conjunto de turnos."""
        if not turns:
            return None

        turns_text = "\n".join(f"{m.get('role', '?')}: {str(m.get('content', ''))[:300]}" for m in turns)

        result = await self._llm.call_llm(
            prompt=f"Resume la siguiente conversación en 2-4 oraciones densas. "
            f"Preserva: hechos concretos, decisiones, contexto técnico.\n\n{turns_text}",
            system_message="Eres un sistema de compresión de contexto. Sé denso y preciso.",
            temperature=0.2,
            task_type="summarization",
        )
        if result.get("success"):
            return result["content"]
        return None
