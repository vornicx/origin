from abc import ABC, abstractmethod
from typing import Dict, Any
from datetime import datetime


class BaseSkill(ABC):
    """Clase base para todas las skills del Cuerpo de Origin"""

    def __init__(self, name: str, description: str):
        self.name = name
        self.description = description
        self.created_at = datetime.now()
        self.execution_count = 0
        self.last_execution = None

    @abstractmethod
    async def execute(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        """
        Ejecuta la skill

        Args:
            inputs: Diccionario con inputs para la skill

        Returns:
            Diccionario con resultado
            {
                "success": bool,
                "result": Any,
                "error": Optional[str],
                "execution_time": float
            }
        """

    @abstractmethod
    def validate_inputs(self, inputs: Dict[str, Any]) -> tuple[bool, str]:
        """
        Valida que los inputs sean correctos

        Returns:
            (is_valid: bool, error_message: str)
        """

    def get_metadata(self) -> Dict[str, Any]:
        """Retorna metadata de la skill"""
        return {
            "name": self.name,
            "description": self.description,
            "created_at": self.created_at.isoformat(),
            "execution_count": self.execution_count,
            "last_execution": self.last_execution.isoformat() if self.last_execution else None,
        }
