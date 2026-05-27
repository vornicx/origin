"""Sistema de hooks/plugins para Origin Skills.

Permite que cualquier módulo registre callbacks que se ejecutan
antes/después de cada skill, sin modificar el registry del executor.
"""

from typing import Dict, Any, List, Callable, Awaitable, Optional
import logging
from enum import Enum
from dataclasses import dataclass, field

logger = logging.getLogger("origin.skills.hooks")


class HookEvent(str, Enum):
    BEFORE_SKILL = "before_skill"
    AFTER_SKILL = "after_skill"
    BEFORE_PLAN = "before_plan"
    AFTER_PLAN = "after_plan"
    ON_ERROR = "on_error"
    ON_STARTUP = "on_startup"
    ON_SHUTDOWN = "on_shutdown"


@dataclass
class HookContext:
    event: HookEvent
    skill_name: str = ""
    inputs: Dict[str, Any] = field(default_factory=dict)
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    execution_time: float = 0.0


HookCallback = Callable[[HookContext], Awaitable[None]]


class PluginManager:
    """Gestor central de hooks/plugins.

    Uso:
        pm = PluginManager()
        pm.register("before_skill", my_callback, priority=10)
        await pm.trigger("before_skill", HookContext(...))
    """

    def __init__(self):
        self._hooks: Dict[HookEvent, List[tuple[int, HookCallback]]] = {event: [] for event in HookEvent}

    def register(self, event: HookEvent, callback: HookCallback, priority: int = 100):
        """Registra un callback para un evento.

        priority: menor número = se ejecuta primero (default 100)
        """
        self._hooks[event].append((priority, callback))
        self._hooks[event].sort(key=lambda x: x[0])
        logger.debug(f"Hook registered: {event.value} (priority={priority})")

    def unregister(self, event: HookEvent, callback: HookCallback):
        self._hooks[event] = [(p, c) for p, c in self._hooks[event] if c != callback]

    async def trigger(self, event: HookEvent, context: HookContext):
        """Dispara todos los callbacks registrados para un evento."""
        for priority, callback in self._hooks[event]:
            try:
                await callback(context)
            except Exception as e:
                logger.warning(f"Hook {event.value} (priority={priority}) failed: {e}")

    def wrap_skill(self, skill_name: str, execute_fn):
        """Wraps a skill's execute method with before/after hooks."""

        async def wrapped(inputs: Dict[str, Any]) -> Dict[str, Any]:
            import time

            ctx = HookContext(event=HookEvent.BEFORE_SKILL, skill_name=skill_name, inputs=inputs)
            await self.trigger(HookEvent.BEFORE_SKILL, ctx)

            start = time.time()
            try:
                result = await execute_fn(inputs)
                ctx.result = result
                ctx.execution_time = time.time() - start
                ctx.event = HookEvent.AFTER_SKILL
                await self.trigger(HookEvent.AFTER_SKILL, ctx)
                return result
            except Exception as e:
                ctx.error = str(e)
                ctx.execution_time = time.time() - start
                ctx.event = HookEvent.ON_ERROR
                await self.trigger(HookEvent.ON_ERROR, ctx)
                raise

        return wrapped

    def get_registered_hooks(self) -> Dict[str, List[str]]:
        return {
            event.value: [c.__name__ for _, c in callbacks] for event, callbacks in self._hooks.items() if callbacks
        }
