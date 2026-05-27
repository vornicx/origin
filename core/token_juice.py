"""
TokenJuice — Compresión de contexto basada en reglas (sin LLM).

Reduce el contexto que se envía al LLM aplicando reglas declarativas:
  - STRIP:    Elimina contenido de bajo valor (saludos, filler, ruido).
  - COMPRESS: Reduce contenido verboso (JSON largo, stack traces, listas).
  - PRESERVE: Protege contenido de alto valor (decisiones, código, errores).
  - BUDGET:   Asigna tokens por sección de contexto.

Se ejecuta ANTES del LLM call → 0 latencia extra, 0 costo de tokens.
Complementa a ContextCompressor (que usa LLM para resumir).

Uso:
    juice = TokenJuice()
    compressed = juice.squeeze(text, max_tokens=6000)
    # o por sección:
    ctx = juice.squeeze_context(memory_context, budget=8000)
"""

import logging
import re
import time
from dataclasses import dataclass, field
from typing import Dict, Any, List, Optional, Tuple

logger = logging.getLogger("origin.core.token_juice")

# ── Token estimation ──────────────────────────────────────────
CHARS_PER_TOKEN = 4  # ~4 chars per token for Spanish/English


def estimate_tokens(text: str) -> int:
    """Fast token count estimate."""
    return len(text) // CHARS_PER_TOKEN


def truncate_to_tokens(text: str, max_tokens: int) -> str:
    """Hard truncate text to fit within token budget."""
    max_chars = max_tokens * CHARS_PER_TOKEN
    if len(text) <= max_chars:
        return text
    # Try to cut at last newline within budget
    cut = text[:max_chars].rfind("\n")
    if cut < max_chars // 2:
        cut = max_chars
    return text[:cut] + "\n[...truncado]"


# ── Strip patterns ────────────────────────────────────────────
# (regex, replacement, description)
_STRIP_RULES: List[Tuple[re.Pattern, str, str]] = [
    # Greetings and filler
    (
        re.compile(
            r"(?im)^(user|assistant):\s*(hola|hey|buenas|buenos\s+d[ií]as|buenas\s+(tardes|noches)|saludos|hi|hello|what'?s\s+up)\s*[.!,]?\s*$"  # noqa: E501
        ),
        "",
        "Greeting lines",
    ),
    (
        re.compile(
            r"(?im)^(user|assistant):\s*(gracias|thanks|thank\s+you|ok\s+gracias|perfecto\s+gracias|genial\s+gracias)[.!]?\s*$"  # noqa: E501
        ),
        "",
        "Thank-you only lines",
    ),
    (
        re.compile(
            r"(?im)^(user|assistant):\s*(ok|si|s[ií]|yes|no|ya|listo|entendido|dale|vale|got\s+it|understood)[.!]?\s*$"
        ),
        "",
        "Single-word ack lines",
    ),
    # Repetitive Origin ceremony
    (
        re.compile(r"(?i)(¿(necesitas|quieres|deseas)\s+(algo\s+más|otra\s+cosa|que\s+haga\s+algo\s+más)\??)\s*"),
        "",
        "Offer-more filler",
    ),
    (
        re.compile(r"(?i)(¡?con\s+gusto[.!]?\s*|¡?por\s+supuesto[.!]?\s*|¡?claro\s+que\s+s[ií][.!]?\s*)"),
        "",
        "Enthusiastic filler",
    ),
    (
        re.compile(r"(?i)(aquí\s+tienes?\s*(lo\s+que\s+pediste|la\s+información|el\s+resultado)\s*:?\s*)"),
        "",
        "Here-you-go filler",
    ),
    # Timestamp noise in conversation
    (re.compile(r"\[\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}[^\]]*\]\s*"), "", "ISO timestamps"),
    # Empty or whitespace-only lines (collapse multiple)
    (re.compile(r"\n{3,}"), "\n\n", "Collapse blank lines"),
]


# ── Compress patterns ─────────────────────────────────────────


def _compress_json_blocks(text: str) -> str:
    """Compress large JSON blocks to key-only summary."""

    def _shrink_json(match):
        raw = match.group(0)
        if len(raw) < 300:
            return raw  # Small enough, keep it

        # Count braces depth to estimate complexity
        lines = raw.split("\n")
        if len(lines) <= 8 and len(raw) < 500:
            return raw

        # Extract top-level keys
        keys = re.findall(r'"(\w+)"\s*:', raw)
        unique_keys = list(dict.fromkeys(keys))[:12]  # Dedupe, keep order
        summary = "{" + ", ".join(f'"{k}": ...' for k in unique_keys) + "}"
        return f"[JSON {len(lines)} lineas, keys: {summary}]"

    return re.sub(
        r"\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}",
        _shrink_json,
        text,
    )


def _compress_stack_traces(text: str) -> str:
    """Compress stack traces to first + last frame."""

    def _shrink_trace(match):
        full = match.group(0)
        lines = full.strip().split("\n")
        if len(lines) <= 6:
            return full

        # Keep first 2 lines (Traceback header + first frame) + last 2 lines (last frame + error)
        return "\n".join(lines[:2] + [f"  ... ({len(lines)-4} frames omitidos) ..."] + lines[-2:])

    return re.sub(
        r"Traceback \(most recent call last\):.*?(?=\n\S|\Z)",
        _shrink_trace,
        text,
        flags=re.DOTALL,
    )


def _compress_long_lists(text: str) -> str:
    """Compress bullet/numbered lists longer than 8 items."""

    def _shrink_list(match):
        full = match.group(0)
        lines = [line for line in full.strip().split("\n") if line.strip()]
        if len(lines) <= 8:
            return full
        kept = lines[:5]
        kept.append(f"  ...y {len(lines) - 5} items más")
        return "\n".join(kept)

    # Bullet lists (- or *)
    text = re.sub(
        r"(?:^[ \t]*[-*]\s+.+\n?){9,}",
        _shrink_list,
        text,
        flags=re.MULTILINE,
    )
    # Numbered lists
    text = re.sub(
        r"(?:^[ \t]*\d+[.)]\s+.+\n?){9,}",
        _shrink_list,
        text,
        flags=re.MULTILINE,
    )
    return text


def _compress_repetitive_lines(text: str) -> str:
    """Detect and collapse repetitive log-like lines."""
    lines = text.split("\n")
    if len(lines) < 15:
        return text

    result = []
    prev_pattern = None
    repeat_count = 0
    first_repeat = ""

    for line in lines:
        # Normalize: strip numbers and timestamps to detect pattern
        pattern = re.sub(r"\d+", "#", line.strip())[:60]

        if pattern == prev_pattern and pattern:
            repeat_count += 1
        else:
            if repeat_count > 2:
                result.append(f"  [...{repeat_count} lineas similares a: {first_repeat[:80]}]")
            elif repeat_count > 0:
                pass  # 1-2 repeats: already added
            prev_pattern = pattern
            first_repeat = line.strip()
            repeat_count = 0
            result.append(line)

    if repeat_count > 2:
        result.append(f"  [...{repeat_count} lineas similares a: {first_repeat[:80]}]")

    return "\n".join(result)


def _compress_code_blocks(text: str) -> str:
    """Compress old/large code blocks to signature-only."""

    def _shrink_code(match):
        full = match.group(0)
        lines = full.split("\n")
        if len(lines) <= 15:
            return full

        match.group(1) or ""
        # Keep first 5 lines (usually imports/signature) + "..."
        kept = lines[:6]
        kept.append(f"  # ... ({len(lines) - 6} lineas más)")
        kept.append("```")
        return "\n".join(kept)

    return re.sub(
        r"```(\w*)\n.*?```",
        _shrink_code,
        text,
        flags=re.DOTALL,
    )


# ── Preserve patterns (content to NEVER strip) ───────────────
_PRESERVE_MARKERS = [
    re.compile(r"(?i)(decisión|decision|decidimos|agreed|se acordó|conclusi[oó]n)"),
    re.compile(r"(?i)(error|excepci[oó]n|exception|fallo|falla|bug|issue|problema)"),
    re.compile(r"(?i)(preferencia|prefiero|preference|siempre\s+uso|me\s+gusta\s+que)"),
    re.compile(r"(?i)(ruta|path|url|endpoint|api|clave|key|token|password|secret)"),
    re.compile(r"(?i)(instalar?|configurar?|setup|deploy|migrar?)"),
    re.compile(r"\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b"),  # IP addresses
    re.compile(r"https?://\S+"),  # URLs
]


def has_preserve_marker(text: str) -> bool:
    """Check if text contains high-value content that should be preserved."""
    return any(p.search(text) for p in _PRESERVE_MARKERS)


# ── Context budget allocation ─────────────────────────────────

DEFAULT_BUDGET = {
    "conversation": 0.35,  # 35% — recent conversation turns
    "memory_tree": 0.20,  # 20% — long-term identity/patterns
    "subconscious": 0.10,  # 10% — background thoughts
    "context_files": 0.15,  # 15% — AGENTS.md style context
    "cross_references": 0.10,  # 10% — indexed recent messages
    "summary": 0.10,  # 10% — compressed session summary
}


@dataclass
class JuiceResult:
    """Result of a squeeze operation."""

    original_tokens: int
    compressed_tokens: int
    savings_pct: float
    rules_applied: List[str] = field(default_factory=list)
    processing_ms: float = 0.0

    @property
    def saved_tokens(self) -> int:
        return self.original_tokens - self.compressed_tokens


class TokenJuice:
    """
    Rule-based context compression engine.

    Applies declarative rules to reduce context size before sending to LLM.
    Zero LLM calls, zero extra latency (<5ms typical).
    """

    def __init__(self, budget: Optional[Dict[str, float]] = None):
        self._budget = budget or DEFAULT_BUDGET
        self._total_squeezed = 0
        self._total_tokens_saved = 0
        self._squeeze_count = 0

    # ── Main squeeze ──────────────────────────────────────────

    def squeeze(self, text: str, max_tokens: int = 6000) -> Tuple[str, JuiceResult]:
        """Apply all compression rules to a text block.

        Returns (compressed_text, result).
        Fast: regex-only, typically <5ms.
        """
        t0 = time.perf_counter()
        original_tokens = estimate_tokens(text)
        rules_applied = []

        # Phase 1: Strip low-value content
        for pattern, replacement, desc in _STRIP_RULES:
            before = len(text)
            text = pattern.sub(replacement, text)
            if len(text) < before:
                rules_applied.append(f"strip:{desc}")

        # Phase 2: Compress verbose content
        before = len(text)
        text = _compress_json_blocks(text)
        if len(text) < before:
            rules_applied.append("compress:json_blocks")

        before = len(text)
        text = _compress_stack_traces(text)
        if len(text) < before:
            rules_applied.append("compress:stack_traces")

        before = len(text)
        text = _compress_long_lists(text)
        if len(text) < before:
            rules_applied.append("compress:long_lists")

        before = len(text)
        text = _compress_repetitive_lines(text)
        if len(text) < before:
            rules_applied.append("compress:repetitive_lines")

        before = len(text)
        text = _compress_code_blocks(text)
        if len(text) < before:
            rules_applied.append("compress:code_blocks")

        # Phase 3: Hard truncate if still over budget
        compressed_tokens = estimate_tokens(text)
        if compressed_tokens > max_tokens:
            text = truncate_to_tokens(text, max_tokens)
            compressed_tokens = estimate_tokens(text)
            rules_applied.append("truncate:hard_limit")

        # Clean up trailing whitespace
        text = text.strip()
        compressed_tokens = estimate_tokens(text)

        elapsed = (time.perf_counter() - t0) * 1000
        savings = 1.0 - (compressed_tokens / max(original_tokens, 1))

        result = JuiceResult(
            original_tokens=original_tokens,
            compressed_tokens=compressed_tokens,
            savings_pct=round(savings * 100, 1),
            rules_applied=rules_applied,
            processing_ms=round(elapsed, 2),
        )

        # Track stats
        self._squeeze_count += 1
        self._total_squeezed += original_tokens
        self._total_tokens_saved += result.saved_tokens

        if rules_applied:
            logger.info(
                f"TokenJuice: {original_tokens}→{compressed_tokens} tokens "
                f"(-{result.savings_pct}%) rules={len(rules_applied)} "
                f"in {elapsed:.1f}ms"
            )

        return text, result

    # ── Context-aware squeeze ─────────────────────────────────

    def squeeze_context(self, memory_context: Dict[str, Any], total_budget: int = 8000) -> Dict[str, Any]:
        """Squeeze a full memory_context dict, respecting section budgets.

        Each section gets a token budget based on DEFAULT_BUDGET ratios.
        Returns the modified memory_context with compressed sections.
        """
        t0 = time.perf_counter()
        total_rules = []

        # Calculate budget per section
        section_map = {
            "recent_conversation": "conversation",
            "memory_tree": "memory_tree",
            "subconscious": "subconscious",
            "context_files": "context_files",
            "cross_reference_index": "cross_references",
            "latest_summary": "summary",
        }

        for ctx_key, budget_key in section_map.items():
            content = memory_context.get(ctx_key)
            if not content:
                continue

            # Get section token budget
            ratio = self._budget.get(budget_key, 0.1)
            section_budget = int(total_budget * ratio)

            # Convert to text for compression
            if isinstance(content, list):
                text = "\n".join(
                    f"{m.get('role', '?')}: {m.get('content', '')}" if isinstance(m, dict) else str(m) for m in content
                )
            elif isinstance(content, str):
                text = content
            else:
                continue

            current_tokens = estimate_tokens(text)
            if current_tokens <= section_budget:
                continue  # Already within budget

            # Squeeze this section
            compressed, result = self.squeeze(text, max_tokens=section_budget)
            total_rules.extend(result.rules_applied)

            # Write back
            if isinstance(content, list):
                # Reconstruct as single compressed entry
                memory_context[ctx_key] = [
                    {"role": "system", "content": f"[Comprimido: {result.savings_pct}%] {compressed}"}
                ]
            else:
                memory_context[ctx_key] = compressed

        elapsed = (time.perf_counter() - t0) * 1000
        if total_rules:
            logger.info(
                f"TokenJuice context squeeze: {len(total_rules)} rules "
                f"across {len(section_map)} sections in {elapsed:.1f}ms"
            )

        return memory_context

    # ── Conversation-specific squeeze ─────────────────────────

    def squeeze_conversation(self, turns: List[Dict[str, Any]], max_tokens: int = 3000) -> List[Dict[str, Any]]:
        """Compress conversation turns, preserving recent and high-value ones.

        Strategy:
        1. Always keep the last 4 turns intact (current exchange).
        2. Squeeze older turns: strip filler, compress verbose outputs.
        3. If still over budget, drop oldest low-value turns first.
        """
        if not turns:
            return turns

        total_tokens = sum(estimate_tokens(str(t.get("content", ""))) for t in turns)
        if total_tokens <= max_tokens:
            return turns

        # Split: recent (protected) vs old (compressible)
        protected = min(4, len(turns))
        recent = turns[-protected:]
        old = turns[:-protected] if protected < len(turns) else []

        if not old:
            return turns

        # Phase 1: Squeeze each old turn
        compressed_old = []
        for turn in old:
            content = str(turn.get("content", ""))
            tokens = estimate_tokens(content)

            if tokens < 20:
                # Very short turn — check if it's just filler
                if not has_preserve_marker(content):
                    # Check if it's an ack/greeting
                    stripped = content.strip().lower()
                    if stripped in (
                        "ok",
                        "si",
                        "sí",
                        "yes",
                        "no",
                        "ya",
                        "listo",
                        "entendido",
                        "dale",
                        "vale",
                        "got it",
                        "hola",
                        "gracias",
                        "thanks",
                        "perfecto",
                    ):
                        continue  # Drop filler turn entirely

            if tokens > 200 and not has_preserve_marker(content):
                # Compress verbose old turn
                squeezed, _ = self.squeeze(content, max_tokens=80)
                compressed_old.append({**turn, "content": squeezed})
            else:
                compressed_old.append(turn)

        # Phase 2: If still over budget, drop oldest non-preserve turns
        result = compressed_old + recent
        while estimate_tokens(str(result)) > max_tokens and len(result) > protected + 1:
            # Find first non-preserve turn to drop
            dropped = False
            for i in range(len(result) - protected):
                content = str(result[i].get("content", ""))
                if not has_preserve_marker(content):
                    result.pop(i)
                    dropped = True
                    break
            if not dropped:
                # All remaining are high-value — drop oldest anyway
                result.pop(0)

        return result

    # ── Stats ─────────────────────────────────────────────────

    @property
    def stats(self) -> Dict[str, Any]:
        return {
            "squeeze_count": self._squeeze_count,
            "total_tokens_processed": self._total_squeezed,
            "total_tokens_saved": self._total_tokens_saved,
            "avg_savings_pct": round((self._total_tokens_saved / max(self._total_squeezed, 1)) * 100, 1),
            "budget_config": self._budget,
        }
