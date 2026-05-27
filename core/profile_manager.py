import json
import logging
from pathlib import Path
from typing import Dict, Any
from datetime import datetime
from .config import config_manager

logger = logging.getLogger("origin.profile")


class ProfileManager:
    """Gestiona el perfil del usuario y la identidad de Origin"""

    def __init__(self):
        self.user_profile = config_manager.get_user_profile()
        self.origin_identity = config_manager.get_origin_identity()
        self.last_updated = datetime.now()

    def get_user_context(self) -> Dict[str, Any]:
        """Retorna contexto del usuario para el reasoning loop"""
        return {
            "id": self.user_profile.get("id"),
            "nombre": self.user_profile.get("nombre"),
            "estilos": self.user_profile.get("estilos", {}),
            "preferencias_tecnicas": self.user_profile.get("preferencias_tecnicas", {}),
            "prioridades_actuales": self.user_profile.get("prioridades_actuales", []),
            "humor_contexto": self.user_profile.get("humor_contexto", "neutro"),
        }

    def get_origin_prompt_context(self) -> str:
        """Retorna el contexto para prompt de Origin (system message)"""
        identity = self.origin_identity
        behavior = identity.get("comportamiento", {})
        style = identity.get("estilo", {})

        return f"""Eres {identity.get('nombre', 'Origin')}, {identity.get('rol', '')}.

ESTILO:
- Tono: {style.get('tono', '')}
- Registro: {style.get('registro', '')}
- Humor: {style.get('humor', '')}
- Emojis: {'Sí' if style.get('usa_emojis') else 'No'}

COMPORTAMIENTO:
- Prioridades: {', '.join(behavior.get('prioridad', []))}
- Evita: {', '.join(behavior.get('evita', []))}

Recuerda: Precisión > Fluff. Humor nunca bloquea claridad.
"""

    def get_llm_priority_order(self) -> list[str]:
        """Retorna lista ordenada de nombres de proveedores LLM por prioridad"""
        proveedores = self.user_profile.get("preferencias_tecnicas", {}).get("proveedores_modelos", {})
        sorted_items = sorted(
            proveedores.items(),
            key=lambda x: x[1].get("prioridad", 999) if isinstance(x[1], dict) else 999,
        )
        return [name for name, _ in sorted_items]

    def update_user_context(self, key: str, value: Any) -> None:
        """Actualiza un campo del contexto del usuario y persiste a disco"""
        self.user_profile[key] = value
        self.last_updated = datetime.now()
        self.save_profile()

    def save_profile(self) -> None:
        """Persiste el perfil actualizado a config/profiles.json"""
        try:
            profile_path = Path(__file__).parent.parent / "config" / "profiles.json"
            data = {}
            if profile_path.exists():
                with open(profile_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
            data["usuario"] = self.user_profile
            with open(profile_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            config_manager._profiles = data
        except Exception as e:
            logger.warning(f"Could not save profile: {e}")

    def get_full_profile(self) -> Dict[str, Any]:
        """Retorna el perfil completo (usuario + Origin)"""
        return {
            "usuario": self.user_profile,
            "origin": self.origin_identity,
            "last_updated": self.last_updated.isoformat(),
        }

    def format_for_memory(self) -> Dict[str, Any]:
        """Formatea el perfil para guardarlo en memoria"""
        return {
            "type": "system_profile",
            "timestamp": datetime.now().isoformat(),
            "data": {
                "user_id": self.user_profile.get("id"),
                "user_name": self.user_profile.get("nombre"),
                "style_communication": self.user_profile.get("estilos", {}).get("comunicacion"),
                "work_style": self.user_profile.get("estilos", {}).get("trabajo"),
                "tech_preferences": self.user_profile.get("preferencias_tecnicas", {}),
                "current_priorities": self.user_profile.get("prioridades_actuales", []),
            },
        }
