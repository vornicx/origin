import json
from pathlib import Path
from typing import Dict, Any, Optional
from pydantic_settings import BaseSettings

PROJECT_ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    """Configuración centralizada de Origin"""

    # LLM APIs
    default_llm_provider: str = "deepseek"
    deepseek_api_key: str = ""
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_model: str = "deepseek-chat"
    groq_api_key: str = ""
    gemini_api_key: str = ""
    opencode_api_key: str = ""
    nvidia_nim_api_key: str = ""
    anthropic_api_key: str = ""

    # Web Search
    brave_search_api_key: str = ""
    searxng_url: str = ""

    # Fish Audio TTS
    fish_audio_api_key: str = ""

    # ElevenLabs TTS
    elevenlabs_api_key: str = ""

    # Telegram Bot
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""

    # Ollama
    ollama_base_url: str = "http://localhost:11434"

    # Database
    database_url: str = "postgresql://postgres:password@localhost/origin_db"

    # Environment
    debug: bool = False
    log_level: str = "INFO"

    model_config = {
        "env_file": str(PROJECT_ROOT / ".env"),
        "case_sensitive": False,
        "extra": "ignore",
    }


class ConfigManager:
    """Gestor de configuraciones (perfiles, políticas)"""

    def __init__(self):
        self.config_dir = Path(__file__).parent.parent / "config"
        self._profiles: Optional[Dict[str, Any]] = None
        self._policy: Optional[Dict[str, Any]] = None

    @property
    def profiles(self) -> Dict[str, Any]:
        """Carga y cachea perfiles de usuario"""
        if self._profiles is None:
            profile_path = self.config_dir / "profiles.json"
            with open(profile_path, "r", encoding="utf-8") as f:
                self._profiles = json.load(f)
        return self._profiles

    @property
    def policy(self) -> Dict[str, Any]:
        """Carga y cachea política de ejecución"""
        if self._policy is None:
            policy_path = self.config_dir / "policy.json"
            with open(policy_path, "r", encoding="utf-8") as f:
                self._policy = json.load(f)
        return self._policy

    def get_user_profile(self, user_id: str = "vadim_vornic") -> Dict[str, Any]:
        """Obtiene el perfil de usuario"""
        return self.profiles.get("usuario", {})

    def get_origin_identity(self) -> Dict[str, Any]:
        """Obtiene la identidad de Origin"""
        return self.profiles.get("origin", {})

    def requires_confirmation(self, action: str) -> bool:
        """Verifica si una acción requiere confirmación"""
        return action in self.policy.get("execution_policy", {}).get("requires_confirmation", [])

    def is_auto_allowed(self, action: str) -> bool:
        """Verifica si una acción es permitida automáticamente"""
        return action in self.policy.get("execution_policy", {}).get("auto_allowed", [])

    def get_interrupt_mode(self) -> str:
        """Obtiene el modo de interrupción actual"""
        return self.policy.get("interrupt_policy", {}).get("default_mode", "focus")


# Instancias globales
settings = Settings()
config_manager = ConfigManager()
