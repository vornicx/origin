from typing import Dict, Any
import logging

from .utils import extract_json_from_text

logger = logging.getLogger("origin.mind.intent")


class IntentParser:
    """Paso 1: Parsea la intención del usuario y selecciona skills relevantes."""

    def __init__(self, llm_router, profile_mgr, available_skills: Dict[str, str]):
        self.llm_router = llm_router
        self.profile_mgr = profile_mgr
        self.available_skills = available_skills

    async def parse(self, user_input: str) -> Dict[str, Any]:
        skills_desc = "\n".join([f"- {name}: {desc}" for name, desc in self.available_skills.items()])

        system_msg = f"""Eres {self.profile_mgr.origin_identity.get('nombre')}.
Tu tarea es parsear la intención del usuario y seleccionar skills relevantes.

SKILLS DISPONIBLES:
{skills_desc}

Retorna JSON con:
- intent: descripción concisa de qué quiere
- task_type: simple | complex | reasoning
- required_skills: lista DE NOMBRES EXACTOS de skills a usar (del listado arriba, puede estar vacío)
- confidence: 0.0-1.0
- context: contexto relevante extraído

IMPORTANTE: Solo incluye nombres de skills que existen en la lista superior.
"""

        result = await self.llm_router.call_llm(
            prompt=(
                f"Usuario dice: {user_input}\n\n"
                "Parsea su intención e identifica skills necesarios. Responde en JSON."
            ),
            system_message=system_msg,
            temperature=0.3,
            task_type="intent_parsing",
        )

        if not result.get("success"):
            return {
                "intent": "unknown",
                "task_type": "simple",
                "required_skills": [],
                "confidence": 0.0,
                "raw_input": user_input,
            }

        intent_data = extract_json_from_text(result.get("content", ""))
        if intent_data:
            return intent_data
        return {
            "intent": user_input[:80],
            "task_type": "simple",
            "required_skills": [],
            "confidence": 0.5,
            "raw_input": user_input,
        }

    def is_simple_task(self, intent: Dict[str, Any]) -> bool:
        return (
            intent.get("task_type") == "simple"
            and intent.get("confidence", 0) >= 0.8
            and len(intent.get("required_skills", [])) <= 1
        )
