"""
Origin Core - The Mind of Origin

Heavy imports are lazy to keep unit-test startup fast.
Import specific modules directly (e.g. `from core.auth import ...`)
or import the package symbols only when you need the full stack.
"""

__all__ = [
    "Mind",
    "ProfileManager",
    "MemoryManager",
    "PersistenceManager",
    "IntentParser",
    "Planner",
    "Reviewer",
    "Responder",
    "LLMRouter",
]


def __getattr__(name: str):
    """Lazy-load heavy submodules only when accessed as `core.<Name>`."""
    _module_map = {
        "Mind": (".mind", "Mind"),
        "ProfileManager": (".profile_manager", "ProfileManager"),
        "MemoryManager": (".memory_manager", "MemoryManager"),
        "PersistenceManager": (".persistence", "PersistenceManager"),
        "IntentParser": (".intent", "IntentParser"),
        "Planner": (".planner", "Planner"),
        "Reviewer": (".reviewer", "Reviewer"),
        "Responder": (".responder", "Responder"),
        "LLMRouter": (".llm_router", "LLMRouter"),
    }
    if name in _module_map:
        mod_path, attr = _module_map[name]
        import importlib
        mod = importlib.import_module(mod_path, package=__name__)
        return getattr(mod, attr)
    raise AttributeError(f"module 'core' has no attribute {name!r}")
