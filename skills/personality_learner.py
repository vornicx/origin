"""
PersonalityLearner — Sistema de aprendizaje de personalidad del usuario.

Analiza interacciones para detectar:
  - Preferencias de comunicación (formal vs casual, técnico vs simple)
  - Horarios de uso
  - Temas recurrentes
  - Nivel técnico
  - Tono preferido

Se integra con MemoryManager y ProfileManager.
"""

import logging
import re
from typing import Dict, List, Optional
from collections import Counter
from datetime import datetime
from dataclasses import dataclass

logger = logging.getLogger("origin.core.personality")

# Palabras clave por perfil
_CASUAL_WORDS = {"hola", "hey", "oye", "jaja", "ok", "vale", "gracias", "bye", "che", "dale"}
_FORMAL_WORDS = {"estimado", "agradecería", "solicito", "quisiera", "por favor", "le agradezco"}
_TECH_WORDS = {
    "error",
    "bug",
    "código",
    "función",
    "api",
    "debug",
    "clase",
    "método",
    "función",
    "variable",
    "script",
    "servidor",
    "base de datos",
}


@dataclass
class UserProfile:
    username: str = ""
    communication_style: str = "technical"
    technical_level: float = 0.7
    response_verbosity: str = "concise"
    preferred_language: str = "es"
    active_hours: List[int] = None
    common_topics: List[str] = None
    interaction_count: int = 0
    last_seen: Optional[str] = None
    confidence: float = 0.0

    def to_dict(self) -> Dict:
        return {
            "username": self.username,
            "communication_style": self.communication_style,
            "technical_level": self.technical_level,
            "response_verbosity": self.response_verbosity,
            "preferred_language": self.preferred_language,
            "active_hours": self.active_hours or [],
            "common_topics": self.common_topics or [],
            "interaction_count": self.interaction_count,
            "last_seen": self.last_seen,
            "confidence": self.confidence,
        }


class PersonalityLearner:
    """Aprende del usuario con cada interacción.

    Se llama después de cada ciclo del reasoning loop.
    Actualiza el perfil del usuario basado en:
      - Contenido del mensaje (técnico/casual/formal)
      - Hora del día
      - Temas mencionados
    """

    def __init__(self, memory_manager):
        self._memory = memory_manager
        self._profile = UserProfile()
        self._history: List[str] = []
        self._max_history = 200

    @property
    def profile(self) -> UserProfile:
        return self._profile

    def learn_from_interaction(self, user_input: str) -> UserProfile:
        """Analiza un mensaje del usuario y actualiza el perfil."""
        self._profile.interaction_count += 1
        self._profile.last_seen = datetime.utcnow().isoformat()
        self._history.append(user_input)
        if len(self._history) > self._max_history:
            self._history = self._history[-self._max_history :]

        # Detectar estilo de comunicación
        import re

        clean_words = set(re.sub(r"[^\w\s]", "", user_input.lower()).split())
        casual_score = len(clean_words & _CASUAL_WORDS)
        formal_score = len(clean_words & _FORMAL_WORDS)
        tech_score = len(clean_words & _TECH_WORDS)

        if tech_score > casual_score and tech_score > formal_score:
            self._profile.communication_style = "technical"
            self._profile.technical_level = min(1.0, self._profile.technical_level + 0.05)
        elif casual_score > formal_score:
            self._profile.communication_style = "casual"
            self._profile.technical_level = max(0.0, self._profile.technical_level - 0.02)
        elif formal_score > casual_score:
            self._profile.communication_style = "formal"

        # Detectar nivel técnico por longitud y complejidad
        if len(user_input) > 100 and tech_score > 2:
            self._profile.technical_level = min(1.0, self._profile.technical_level + 0.02)

        # Registrar hora activa
        hour = datetime.now().hour
        if self._profile.active_hours is None:
            self._profile.active_hours = []
        self._profile.active_hours.append(hour)
        if len(self._profile.active_hours) > 100:
            self._profile.active_hours = self._profile.active_hours[-100:]

        # Detectar temas recurrentes
        topics = self._extract_topics(user_input)
        if topics:
            if self._profile.common_topics is None:
                self._profile.common_topics = []
            self._profile.common_topics.extend(topics)
            top = Counter(self._profile.common_topics).most_common(5)
            self._profile.common_topics = [t for t, _ in top]

        # Confianza aumenta con interacciones
        self._profile.confidence = min(1.0, self._profile.interaction_count / 50)

        # Persistir preferencia de tono en memoria
        if self._profile.interaction_count % 5 == 0:
            self._memory.set_preference("communication_style", self._profile.communication_style, source="inferred")
            self._memory.set_preference("technical_level", self._profile.technical_level, source="inferred")

        return self._profile

    def _extract_topics(self, text: str) -> List[str]:
        """Extrae temas potenciales del texto."""
        topics = []
        patterns = [
            (r"python|django|flask|fastapi", "python"),
            (r"javascript|typescript|react|node", "javascript"),
            (r"docker|kubernetes|container", "docker"),
            (r"windows|linux|mac", "sistema_operativo"),
            (r"error|bug|issue|fallo", "debug"),
            (r"git|github|repo|branch", "git"),
            (r"sql|postgres|mysql|db|database", "base_datos"),
            (r"api|rest|endpoint|http", "api"),
            (r"skill|plugin|extensión|modulo", "skills"),
        ]
        for pattern, topic in patterns:
            if re.search(pattern, text.lower()):
                topics.append(topic)
        return topics

    def get_style_instruction(self) -> str:
        """Retorna instrucción de estilo para el prompt del LLM."""
        style = self._profile.communication_style
        verbosity = self._profile.response_verbosity
        tech = self._profile.technical_level

        instructions = []
        if style == "casual":
            instructions.append("Tono coloquial y directo, como con un amigo técnico.")
        elif style == "formal":
            instructions.append("Tono formal y estructurado, lenguaje preciso.")
        else:
            instructions.append("Tono técnico y conciso, prioriza exactitud.")

        if verbosity == "concise" or tech > 0.7:
            instructions.append("Respuestas cortas y al punto, sin explicaciones innecesarias.")

        return " ".join(instructions)
