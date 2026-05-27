from typing import Dict, Any
import json
import logging

from .utils import extract_json_from_text

logger = logging.getLogger("origin.mind.reviewer")


class Reviewer:
    """Paso 4: Auto-revisión del resultado de ejecución."""

    def __init__(self, llm_router, profile_mgr):
        self.llm_router = llm_router
        self.profile_mgr = profile_mgr

    async def review(self, original_intent: str, plan_result: Dict[str, Any]) -> Dict[str, Any]:
        system_msg = f"""Eres {self.profile_mgr.origin_identity.get('nombre')}.

Tu tarea es revisar si el resultado cumple la intención del usuario.
Identifica gaps, errores o mejoras necesarias.

Responde en JSON con:
- is_valid: true/false
- issues: lista de problemas
- suggestions: lista de mejoras
"""

        review_prompt = f"""
Intención original: {original_intent}

Resultado de ejecución:
{json.dumps(plan_result, indent=2, default=str)}

¿Esto cumple y resuelve la intención? Revisa críticamente.
Responde en JSON.
"""

        result = await self.llm_router.call_llm(
            prompt=review_prompt, system_message=system_msg, temperature=0.3, task_type="review"
        )

        if not result.get("success"):
            logger.warning(f"Review LLM call failed: {result.get('error')}")
            return {
                "is_valid": False,
                "issues": [f"Review failed: {result.get('error', 'unknown')}"],
                "suggestions": [],
                "review_status": "llm_error",
            }

        review_data = extract_json_from_text(result.get("content", ""))
        if review_data and isinstance(review_data, dict) and "is_valid" in review_data:
            return review_data
        # Fallback: if we can't parse, treat as unable to review (not as valid)
        logger.warning(f"Could not parse review JSON from: {result.get('content', '')[:100]}")
        return {
            "is_valid": False,
            "issues": ["Review JSON parse failed"],
            "suggestions": [],
            "review_status": "parse_error",
        }
