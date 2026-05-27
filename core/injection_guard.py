"""
InjectionGuard — Detección y bloqueo de prompt injection.

Capa de seguridad que analiza inputs del usuario ANTES de que lleguen
al reasoning loop. Detecta:
  - Manipulación de rol ("eres ahora...", "ignore instructions")
  - Instrucciones de sistema inyectadas ("System:", "[INST]")
  - Codificación oculta (base64, hex)
  - Exfiltración de datos ("muestra tu prompt", "dump memory")
  - Jailbreak patterns ("DAN", "developer mode")
  - Inyección en cadena ("Cuando respondas, primero ejecuta...")

Scoring: cada detección suma al risk_score (0.0-1.0).
  >= BLOCK_THRESHOLD → input rechazado
  >= WARN_THRESHOLD  → input permitido + log de warning
  <  WARN_THRESHOLD  → input limpio

Inspirado en OpenHuman Prompt Injection Guard.
"""

import base64
import json
import logging
import re
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple

logger = logging.getLogger("origin.security.injection")

# ── Config ────────────────────────────────────────────────────
DATA_DIR = Path(__file__).parent.parent / "data" / "security"
AUDIT_FILE = DATA_DIR / "injection_audit.jsonl"

BLOCK_THRESHOLD = 0.7  # Risk score >= this → block input
WARN_THRESHOLD = 0.35  # Risk score >= this → allow + warn
MAX_AUDIT_LINES = 1000  # Max audit log entries


# ── Pattern categories ────────────────────────────────────────
# Each pattern: (regex, weight, description)
# Weight is added to risk_score when matched.

_PATTERNS: Dict[str, List[Tuple[str, float, str]]] = {
    "role_manipulation": [
        (
            r"(?i)(ignore|forget|disregard|override)\s+(all\s+)?(previous|prior|above|earlier|your)\s+(instructions|rules|guidelines|constraints|directives|programming)",  # noqa: E501
            0.8,
            "Ignore instructions",
        ),
        (
            r"(?i)(you\s+are\s+now|act\s+as\s+if|pretend\s+(to\s+be|you\s+are)|from\s+now\s+on\s+you\s+are)",
            0.6,
            "Role reassignment",
        ),
        (
            r"(?i)(eres\s+ahora|a\s+partir\s+de\s+ahora\s+eres|finge\s+ser|actua\s+como\s+si\s+fueras|olvida\s+tus\s+instrucciones)",  # noqa: E501
            0.6,
            "Role reassignment (ES)",
        ),
        (
            r"(?i)(do\s+not\s+follow|don'?t\s+follow)\s+(your|any|the)\s+(rules|guidelines|instructions|safety)",
            0.7,
            "Rule override attempt",
        ),
        (
            r"(?i)jailbreak|jail\s*break|developer\s+mode|god\s+mode|sudo\s+mode|admin\s+mode|unrestricted\s+mode",
            0.8,
            "Jailbreak keyword",
        ),
        (r"(?i)\bDAN\b.*\bdo\s+anything\s+now\b", 0.9, "DAN jailbreak"),
    ],
    "system_injection": [
        (r"(?i)^(system|assistant|admin|root)\s*:\s*", 0.5, "System role prefix"),
        (r"(?i)\[/?INST\]|\[/?SYS(TEM)?\]|<\|?(system|im_start|endoftext)\|?>", 0.7, "Chat template tags"),
        (r"(?i)<<\s*(SYS|SYSTEM|INSTRUCTIONS?)\s*>>", 0.6, "System delimiter injection"),
        (
            r"(?i)(new\s+system\s+(prompt|message|instruction)|system\s+prompt\s+override|inject\s+system)",
            0.7,
            "System prompt override",
        ),
    ],
    "data_exfiltration": [
        (
            r"(?i)(show|display|print|reveal|dump|output|repeat|echo)\s+(your|the|my|all|entire)?\s*(system\s+prompt|instructions|rules|configuration|config|memory|credentials|api\s*keys?|tokens?|passwords?|secrets?)",  # noqa: E501
            0.6,
            "Prompt/config extraction",
        ),
        (
            r"(?i)(muestra|revela|imprime|dime|dame)\s+(tu|el|la|tus|los|las)?\s*(prompt|instrucciones|reglas|configuracion|memoria|claves?|contrasenas?|passwords?|tokens?|secretos?)",  # noqa: E501
            0.6,
            "Prompt extraction (ES)",
        ),
        (
            r"(?i)(what\s+are\s+your|tell\s+me\s+your|list\s+your)\s+(instructions|rules|system\s+prompt|api\s*keys?|configuration)",  # noqa: E501
            0.4,
            "Info probing",
        ),
        (
            r"(?i)\.env|api[_\s]*key|secret[_\s]*key|private[_\s]*key|access[_\s]*token",
            0.3,
            "Sensitive file/key reference",
        ),
    ],
    "chain_injection": [
        (
            r"(?i)(when\s+you\s+respond|in\s+your\s+(response|answer|reply))\s*,?\s*(first|also|additionally|include|execute|run|add)",  # noqa: E501
            0.4,
            "Response chain injection",
        ),
        (
            r"(?i)(antes\s+de\s+responder|cuando\s+respondas|en\s+tu\s+respuesta)\s*,?\s*(primero|tambien|ejecuta|incluye|agrega)",  # noqa: E501
            0.4,
            "Response chain injection (ES)",
        ),
        (
            r"(?i)(after\s+answering|before\s+answering)\s*,?\s*(also|then|execute|send|call|run|post)",
            0.5,
            "Pre/post injection",
        ),
    ],
    "encoding_evasion": [
        (
            r"(?i)(decode|decodifica|base64|hex|rot13|unicode|url.?encode)\s+(this|the\s+following|esto|lo\s+siguiente)",  # noqa: E501
            0.5,
            "Decode instruction",
        ),
        (r"(?i)\\x[0-9a-f]{2}(\\x[0-9a-f]{2}){3,}", 0.4, "Hex-encoded string"),
        (r"(?i)\\u[0-9a-f]{4}(\\u[0-9a-f]{4}){3,}", 0.4, "Unicode escape sequence"),
    ],
}

# Pre-compile all patterns
_COMPILED: Dict[str, List[Tuple[re.Pattern, float, str]]] = {}
for cat, patterns in _PATTERNS.items():
    _COMPILED[cat] = [(re.compile(p), w, d) for p, w, d in patterns]


@dataclass
class InjectionMatch:
    """A single pattern match."""

    category: str
    description: str
    weight: float
    matched_text: str


@dataclass
class InjectionResult:
    """Result of analyzing an input for injection attempts."""

    is_blocked: bool
    risk_score: float
    risk_level: str  # "clean" | "low" | "medium" | "high" | "blocked"
    matches: List[InjectionMatch] = field(default_factory=list)
    sanitized_input: Optional[str] = None
    analysis_ms: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "is_blocked": self.is_blocked,
            "risk_score": round(self.risk_score, 3),
            "risk_level": self.risk_level,
            "matches": [
                {
                    "category": m.category,
                    "description": m.description,
                    "weight": m.weight,
                    "matched_text": m.matched_text[:80],
                }
                for m in self.matches
            ],
            "analysis_ms": round(self.analysis_ms, 2),
        }


class InjectionGuard:
    """
    Analiza inputs del usuario para detectar prompt injection.

    Uso:
        guard = InjectionGuard()
        result = guard.analyze("ignore your instructions and...")
        if result.is_blocked:
            return error
    """

    def __init__(self, block_threshold: float = BLOCK_THRESHOLD, warn_threshold: float = WARN_THRESHOLD):
        self._block_threshold = block_threshold
        self._warn_threshold = warn_threshold
        self._total_analyzed = 0
        self._total_blocked = 0
        self._total_warned = 0

        DATA_DIR.mkdir(parents=True, exist_ok=True)
        logger.info(
            f"InjectionGuard active: block>={block_threshold}, "
            f"warn>={warn_threshold}, {sum(len(v) for v in _COMPILED.values())} patterns"
        )

    # ── Main analysis ──────────────────────────────────────────

    def analyze(self, text: str) -> InjectionResult:
        """Analyze input text for prompt injection patterns.

        Returns InjectionResult with risk score and matches.
        Fast: regex-only, no LLM call. Typically < 1ms.
        """
        t0 = time.perf_counter()
        self._total_analyzed += 1

        matches: List[InjectionMatch] = []

        # 1. Pattern matching
        for category, compiled_patterns in _COMPILED.items():
            for pattern, weight, description in compiled_patterns:
                m = pattern.search(text)
                if m:
                    matches.append(
                        InjectionMatch(
                            category=category,
                            description=description,
                            weight=weight,
                            matched_text=m.group()[:100],
                        )
                    )

        # 2. Check for hidden base64
        b64_score = self._check_base64(text)
        if b64_score > 0:
            matches.append(
                InjectionMatch(
                    category="encoding_evasion",
                    description="Base64-encoded content detected",
                    weight=b64_score,
                    matched_text="[base64 block]",
                )
            )

        # 3. Compute risk score (capped at 1.0, uses max + diminishing returns)
        risk_score = self._compute_score(matches)

        # 4. Determine risk level
        if risk_score >= self._block_threshold:
            risk_level = "blocked"
            is_blocked = True
            self._total_blocked += 1
        elif risk_score >= self._warn_threshold:
            risk_level = "high" if risk_score >= 0.55 else "medium"
            is_blocked = False
            self._total_warned += 1
        elif risk_score > 0.1:
            risk_level = "low"
            is_blocked = False
        else:
            risk_level = "clean"
            is_blocked = False

        analysis_ms = (time.perf_counter() - t0) * 1000

        result = InjectionResult(
            is_blocked=is_blocked,
            risk_score=risk_score,
            risk_level=risk_level,
            matches=matches,
            sanitized_input=self._sanitize(text, matches) if matches else None,
            analysis_ms=analysis_ms,
        )

        # Log detections
        if matches:
            level = logging.WARNING if is_blocked else logging.INFO
            logger.log(
                level,
                f"Injection scan: risk={risk_score:.2f} level={risk_level} "
                f"matches={len(matches)} blocked={is_blocked} "
                f"input={text[:60]}...",
            )
            self._audit_log(text, result)

        return result

    # ── Scoring ────────────────────────────────────────────────

    @staticmethod
    def _compute_score(matches: List[InjectionMatch]) -> float:
        """Compute combined risk score from matches.

        Uses the highest single weight plus diminishing returns
        from additional matches. This avoids false positives from
        multiple low-weight matches while still penalizing stacking.
        """
        if not matches:
            return 0.0

        weights = sorted([m.weight for m in matches], reverse=True)
        score = weights[0]
        for w in weights[1:]:
            # Each additional match adds 30% of its weight
            score += w * 0.3

        return min(1.0, score)

    # ── Base64 detection ───────────────────────────────────────

    @staticmethod
    def _check_base64(text: str) -> float:
        """Detect base64-encoded blocks that might hide instructions."""
        # Look for base64-like strings (min 20 chars, no spaces)
        b64_pattern = re.findall(r"[A-Za-z0-9+/]{20,}={0,2}", text)

        for candidate in b64_pattern:
            try:
                decoded = base64.b64decode(candidate).decode("utf-8", errors="ignore")
                # Check if decoded content looks like instructions
                instruction_keywords = [
                    "ignore",
                    "system",
                    "instruction",
                    "execute",
                    "password",
                    "override",
                    "admin",
                    "prompt",
                    "instrucciones",
                    "olvida",
                ]
                if any(kw in decoded.lower() for kw in instruction_keywords):
                    return 0.6
            except Exception:
                continue
        return 0.0

    # ── Sanitization ───────────────────────────────────────────

    @staticmethod
    def _sanitize(text: str, matches: List[InjectionMatch]) -> str:
        """Strip detected injection patterns from text.

        Returns the input with matched patterns replaced by [FILTERED].
        Used for medium-risk inputs that are allowed but cleaned.
        """
        sanitized = text
        for m in matches:
            if m.weight >= 0.5 and m.matched_text != "[base64 block]":
                sanitized = sanitized.replace(m.matched_text, "[FILTERED]")
        return sanitized

    # ── Audit logging ──────────────────────────────────────────

    def _audit_log(self, input_text: str, result: InjectionResult):
        """Append detection to audit log (JSONL format)."""
        try:
            entry = {
                "timestamp": datetime.now().isoformat(),
                "risk_score": round(result.risk_score, 3),
                "risk_level": result.risk_level,
                "blocked": result.is_blocked,
                "matches": len(result.matches),
                "categories": list(set(m.category for m in result.matches)),
                "input_preview": input_text[:100],
                "analysis_ms": round(result.analysis_ms, 2),
            }
            with open(AUDIT_FILE, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")

            # Prune if too large
            self._prune_audit()
        except Exception as e:
            logger.debug(f"Audit log write error: {e}")

    def _prune_audit(self):
        """Keep only the last MAX_AUDIT_LINES entries."""
        try:
            if not AUDIT_FILE.exists():
                return
            lines = AUDIT_FILE.read_text(encoding="utf-8").strip().split("\n")
            if len(lines) > MAX_AUDIT_LINES:
                keep = lines[-MAX_AUDIT_LINES:]
                AUDIT_FILE.write_text("\n".join(keep) + "\n", encoding="utf-8")
        except Exception:
            pass

    # ── Stats ──────────────────────────────────────────────────

    @property
    def stats(self) -> Dict[str, Any]:
        return {
            "total_analyzed": self._total_analyzed,
            "total_blocked": self._total_blocked,
            "total_warned": self._total_warned,
            "block_rate": (round(self._total_blocked / max(self._total_analyzed, 1), 4)),
            "block_threshold": self._block_threshold,
            "warn_threshold": self._warn_threshold,
            "pattern_count": sum(len(v) for v in _COMPILED.values()),
        }

    def get_audit_recent(self, n: int = 20) -> List[Dict]:
        """Return the N most recent audit entries."""
        if not AUDIT_FILE.exists():
            return []
        try:
            lines = AUDIT_FILE.read_text(encoding="utf-8").strip().split("\n")
            entries = []
            for line in lines[-n:]:
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
            return list(reversed(entries))
        except Exception:
            return []
