from typing import Dict, Any
import json
import logging

logger = logging.getLogger("origin.mind.responder")


class Responder:
    """Paso 6: Genera respuesta final personalizada para el usuario."""

    def __init__(self, llm_router, profile_mgr, memory_mgr):
        self.llm_router = llm_router
        self.profile_mgr = profile_mgr
        self.memory_mgr = memory_mgr
        self.last_llm_result: Dict[str, Any] = {}

    _TONE_MAP = {
        "casual": "Tono coloquial y directo, como hablarías con un amigo técnico. Frases cortas.",
        "formal": "Tono formal y estructurado. Lenguaje preciso, sin contracciones informales.",
        "technical": "Tono técnico y conciso. Prioriza exactitud sobre suavidad.",
    }

    async def generate(
        self, user_input: str, intent: Dict[str, Any], execution_result: Dict[str, Any], memory_context: Dict[str, Any]
    ) -> str:
        user_context = self.profile_mgr.get_user_context()
        origin_prompt = self.profile_mgr.get_origin_prompt_context()
        lite = bool(memory_context.get("_lite_context"))

        # Lite context (chitchat fast-path): minimal prompt for sub-second latency
        if lite:
            tone = memory_context.get("current_tone", "technical")
            tone_instruction = self._TONE_MAP.get(tone, "")
            answer_prompt = f"""{origin_prompt}

Usuario ({user_context.get('nombre')}):
{user_input}

Tono: {tone} — {tone_instruction}

Responde breve y directo. Sin fluff.
"""
            result = await self.llm_router.call_llm(
                prompt=answer_prompt, system_message=None, temperature=0.7, task_type="chitchat"
            )
            self.last_llm_result = result
            if result.get("success"):
                answer = result.get("content", "")
                self.memory_mgr.add_conversation("assistant", answer)
                return answer
            return f"Error generando respuesta: {result.get('error')}"

        latest_summary = memory_context.get("latest_summary")
        summary_section = f"\nCONTEXTO COMPRIMIDO DE SESIÓN ANTERIOR:\n{latest_summary}\n" if latest_summary else ""

        # Context files (AGENTS.md style)
        context_files = memory_context.get("context_files", "")
        context_section = f"\n{context_files}\n" if context_files else ""

        # Memory Tree: multi-scale context (identity, month, day, hours)
        memory_tree_ctx = memory_context.get("memory_tree", "")
        tree_section = f"\nMEMORIA A LARGO PLAZO (Memory Tree):\n{memory_tree_ctx}\n" if memory_tree_ctx else ""

        # Subconscious: relevant background thoughts/insights
        sub_ctx = memory_context.get("subconscious", "")
        sub_section = f"\n{sub_ctx}\n" if sub_ctx else ""

        cross_refs = memory_context.get("cross_reference_index", [])
        cross_ref_section = ""
        if cross_refs:
            lines = [f"  #{r['idx']} (tema_{r['topic_id']}) {r['role']}: {r['message']}" for r in cross_refs]
            cross_ref_section = "\nÍNDICE DE MENSAJES RECIENTES:\n" + "\n".join(lines) + "\n"

        tone = memory_context.get("current_tone", "technical")
        tone_confidence = memory_context.get("tone_confidence", 0.5)
        tone_instruction = self._TONE_MAP.get(tone, "")

        answer_prompt = f"""{origin_prompt}
{summary_section}
{tree_section}
{sub_section}
{context_section}
---

Usuario ({user_context.get('nombre')}):
{user_input}

Contexto relevante:
- Conversación reciente: {len(memory_context.get('recent_conversation', []))} mensajes
- Memorias relevantes: {len(memory_context.get('relevant_memories', []))}
- Tema de conversación actual: tema_{memory_context.get('current_topic_id', 0)}
{cross_ref_section}
Tono detectado: {tone} (confianza {tone_confidence:.0%}) — {tone_instruction}

Resultado del reasoning:
{json.dumps(execution_result, indent=2, default=str)}

Responde de forma directa al punto. Sin fluff.
"""

        result = await self.llm_router.call_llm(
            prompt=answer_prompt, system_message=None, temperature=0.7, task_type="answer_generation"
        )
        self.last_llm_result = result

        if result.get("success"):
            answer = result.get("content", "")
            self.memory_mgr.add_conversation("assistant", answer)
            return answer
        else:
            return f"Error generando respuesta: {result.get('error')}"
