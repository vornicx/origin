from typing import Dict, Any, Optional, List
from enum import Enum
from collections import deque
import asyncio
import logging
import random
import time
import httpx
from .config import settings, config_manager

genai = None

logger = logging.getLogger("origin.llm")


class LLMProvider(str, Enum):
    DEEPSEEK = "deepseek"
    GROQ = "groq"
    OLLAMA = "ollama"
    NVIDIA_NIM = "nvidia_nim"
    OPENCODE = "opencode"
    GEMINI = "gemini"
    CLAUDE = "claude"


# ── Provider config registry ─────────────────────────────────
ProviderConfig = Dict[str, Any]

_PROVIDERS: Dict[LLMProvider, ProviderConfig] = {
    LLMProvider.DEEPSEEK: {
        "url": f"{settings.deepseek_base_url.rstrip('/')}/chat/completions",
        "model": settings.deepseek_model or "deepseek-chat",
        "key_field": "deepseek_api_key",
    },
    LLMProvider.GROQ: {
        "url": "https://api.groq.com/openai/v1/chat/completions",
        "model": "llama-3.3-70b-versatile",
        "key_field": "groq_api_key",
    },
    LLMProvider.OLLAMA: {
        "url": f"{settings.ollama_base_url or 'http://localhost:11434'}/v1/chat/completions",
        "model": "qwen3.5:4b",
        "key_field": None,  # no API key needed
    },
    LLMProvider.NVIDIA_NIM: {
        "url": "https://integrate.api.nvidia.com/v1/chat/completions",
        "model": "mistralai/mixtral-8x7b-instruct-v0.1",
        "key_field": "nvidia_nim_api_key",
    },
    LLMProvider.OPENCODE: {
        "url": "https://api.opencode.ai/v1/chat/completions",
        "model": "opencode-go-v1",
        "key_field": "opencode_api_key",
    },
    LLMProvider.CLAUDE: {
        "url": "https://api.anthropic.com/v1/messages",
        "model": "claude-sonnet-4-6",
        "key_field": "anthropic_api_key",
    },
}


class LLMRouter:
    # Per-provider rolling stats: success count, fail count, last_latency_s, recent_errors
    _DEFAULT_PROVIDER_ORDER = [
        LLMProvider.DEEPSEEK,
        LLMProvider.GROQ,
        LLMProvider.OLLAMA,
        LLMProvider.NVIDIA_NIM,
        LLMProvider.OPENCODE,
        LLMProvider.GEMINI,
        LLMProvider.CLAUDE,
    ]

    def __init__(self):
        self.settings = settings
        self.profile_mgr = config_manager
        self.priority_order = (
            config_manager.profiles.get("usuario", {}).get("preferencias_tecnicas", {}).get("proveedores_modelos", {})
        )

        self._http_client: Optional[httpx.AsyncClient] = None

        # Provider health: { provider: {"ok": int, "fail": int, "last_ms": float, "cooldown_until": float} }
        self._health: Dict[LLMProvider, Dict[str, Any]] = {
            p: {"ok": 0, "fail": 0, "last_ms": 0.0, "cooldown_until": 0.0, "recent": deque(maxlen=10)}
            for p in LLMProvider
        }
        self._max_retries = 1
        self._retry_base_delay = 0.3

        self._genai = None
        if settings.gemini_api_key:
            try:
                import google.generativeai as _genai

                _genai.configure(api_key=settings.gemini_api_key)
                self._genai = _genai
                logger.info("Gemini provider configured")
            except ImportError:
                logger.warning("google-generativeai not installed — Gemini provider disabled")

    @property
    def http_client(self) -> httpx.AsyncClient:
        if self._http_client is None or self._http_client.is_closed:
            self._http_client = httpx.AsyncClient(
                timeout=httpx.Timeout(30.0, connect=5.0),
                limits=httpx.Limits(
                    max_connections=20,
                    max_keepalive_connections=10,
                    keepalive_expiry=30,
                ),
                http2=False,
            )
        return self._http_client

    async def close(self):
        if self._http_client and not self._http_client.is_closed:
            await self._http_client.aclose()
            self._http_client = None

    def get_available_providers(self) -> List[str]:
        available = []
        if settings.deepseek_api_key:
            available.append(LLMProvider.DEEPSEEK)
        if settings.groq_api_key:
            available.append(LLMProvider.GROQ)
        available.append(LLMProvider.OLLAMA)
        if settings.nvidia_nim_api_key:
            available.append(LLMProvider.NVIDIA_NIM)
        if settings.opencode_api_key:
            available.append(LLMProvider.OPENCODE)
        if settings.gemini_api_key and self._genai:
            available.append(LLMProvider.GEMINI)
        if settings.anthropic_api_key:
            available.append(LLMProvider.CLAUDE)
        return [p.value for p in available]

    def _base_provider_order(self) -> List[LLMProvider]:
        """Default-first provider order, then profile preferences, then fallbacks."""
        ordered: List[LLMProvider] = []

        default_name = (self.settings.default_llm_provider or "deepseek").lower().strip()
        try:
            ordered.append(LLMProvider(default_name))
        except ValueError:
            logger.warning("Unknown DEFAULT_LLM_PROVIDER=%s; falling back to DeepSeek", default_name)
            ordered.append(LLMProvider.DEEPSEEK)

        profile_order = sorted(
            self.priority_order.items(),
            key=lambda item: item[1].get("prioridad", 999) if isinstance(item[1], dict) else 999,
        )
        for provider_name, _meta in profile_order:
            try:
                provider = LLMProvider(provider_name)
            except ValueError:
                continue
            if provider not in ordered:
                ordered.append(provider)

        for provider in self._DEFAULT_PROVIDER_ORDER:
            if provider not in ordered:
                ordered.append(provider)

        return ordered

    # ── Shared OpenAI-compatible caller ──────────────────────

    async def _call_openai_compat(
        self,
        provider: LLMProvider,
        prompt: str,
        system_message: Optional[str] = None,
        temperature: float = 0.7,
        model: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Generic OpenAI-compatible API call. Used by all providers except Gemini.

        Returns {"content": str, "completion_tokens": int}.
        """
        cfg = _PROVIDERS.get(provider)
        if not cfg:
            raise Exception(f"Unknown provider: {provider}")

        api_key = getattr(self.settings, cfg["key_field"]) if cfg["key_field"] else None
        url = cfg["url"]
        model_name = model or cfg["model"]

        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        messages = []
        if system_message:
            messages.append({"role": "system", "content": system_message})
        messages.append({"role": "user", "content": prompt})

        try:
            response = await self.http_client.post(
                url,
                headers=headers,
                json={
                    "model": model_name,
                    "messages": messages,
                    "temperature": temperature,
                    "max_tokens": 4096,
                },
            )
            result = response.json()
            if "error" in result:
                err = result["error"]
                raise Exception(err if isinstance(err, str) else err.get("message", str(err)))
            content = result.get("choices", [{}])[0].get("message", {}).get("content", "")
            if not content:
                raise Exception("Empty response from " + provider.value)
            usage = result.get("usage") or {}
            completion_tokens = int(usage.get("completion_tokens") or 0)
            return {"content": content, "completion_tokens": completion_tokens}
        except httpx.TimeoutException as e:
            raise Exception(f"{provider.value} timeout: {str(e)}")
        except Exception as e:
            raise Exception(f"{provider.value} error: {str(e)}")

    # ── Provider-specific wrappers ───────────────────────────

    async def call_deepseek(
        self, prompt: str, system_message: Optional[str] = None, temperature: float = 0.7, model: str = "deepseek-chat"
    ) -> str:
        r = await self._call_openai_compat(LLMProvider.DEEPSEEK, prompt, system_message, temperature, model)
        return r["content"]

    async def call_groq(
        self,
        prompt: str,
        system_message: Optional[str] = None,
        temperature: float = 0.7,
        model: str = "llama-3.3-70b-versatile",
    ) -> str:
        r = await self._call_openai_compat(LLMProvider.GROQ, prompt, system_message, temperature, model)
        return r["content"]

    async def call_ollama(
        self, prompt: str, system_message: Optional[str] = None, temperature: float = 0.7, model: str = "qwen3.5:4b"
    ) -> str:
        r = await self._call_openai_compat(LLMProvider.OLLAMA, prompt, system_message, temperature, model)
        return r["content"]

    async def call_nvidia_nim(
        self,
        prompt: str,
        system_message: Optional[str] = None,
        temperature: float = 0.7,
        model: str = "mistralai/mixtral-8x7b-instruct-v0.1",
    ) -> str:
        r = await self._call_openai_compat(LLMProvider.NVIDIA_NIM, prompt, system_message, temperature, model)
        return r["content"]

    async def call_opencode(
        self, prompt: str, system_message: Optional[str] = None, temperature: float = 0.7, model: str = None
    ) -> str:
        r = await self._call_openai_compat(LLMProvider.OPENCODE, prompt, system_message, temperature, model)
        return r["content"]

    # ── Claude (Anthropic Messages API) ──────────────────────

    async def call_claude(
        self,
        prompt: str,
        system_message: Optional[str] = None,
        temperature: float = 0.7,
        model: str = "claude-sonnet-4-6",
    ) -> Dict[str, Any]:
        api_key = self.settings.anthropic_api_key
        if not api_key:
            raise Exception("ANTHROPIC_API_KEY not configured")

        headers = {
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }
        body: Dict[str, Any] = {
            "model": model,
            "max_tokens": 4096,
            "temperature": temperature,
            "messages": [{"role": "user", "content": prompt}],
        }
        if system_message:
            body["system"] = system_message

        try:
            response = await self.http_client.post(
                _PROVIDERS[LLMProvider.CLAUDE]["url"],
                headers=headers,
                json=body,
            )
            data = response.json()
            if "error" in data:
                err = data["error"]
                raise Exception(err.get("message", str(err)) if isinstance(err, dict) else str(err))
            content_blocks = data.get("content", [])
            text = "".join(b.get("text", "") for b in content_blocks if b.get("type") == "text")
            if not text:
                raise Exception("Empty response from Claude")
            usage = data.get("usage") or {}
            return {"content": text, "completion_tokens": int(usage.get("output_tokens") or 0)}
        except httpx.TimeoutException as e:
            raise Exception(f"claude timeout: {str(e)}")
        except Exception as e:
            raise Exception(f"claude error: {str(e)}")

    # ── Gemini (non-OpenAI-compatible) ───────────────────────

    async def call_gemini(
        self,
        prompt: str,
        system_message: Optional[str] = None,
        temperature: float = 0.7,
        model: str = "models/gemini-1.5-flash",
    ) -> Dict[str, Any]:
        if not self._genai:
            raise Exception("Gemini not configured")
        try:
            generation_config = {"temperature": temperature, "max_output_tokens": 4096}
            safety_settings = []
            gen_model = self._genai.GenerativeModel(
                model_name=model,
                generation_config=generation_config,
                safety_settings=safety_settings,
                system_instruction=system_message,
            )
            # generate_content is synchronous — run in thread pool to avoid blocking the event loop
            response = await asyncio.to_thread(gen_model.generate_content, prompt)
            tokens = 0
            try:
                usage = getattr(response, "usage_metadata", None)
                if usage is not None:
                    tokens = int(getattr(usage, "candidates_token_count", 0) or 0)
            except Exception:
                tokens = 0
            return {"content": response.text, "completion_tokens": tokens}
        except Exception as e:
            raise Exception(f"Gemini error: {str(e)}")

    # ── Main router ──────────────────────────────────────────

    def _provider_score(self, prov: LLMProvider) -> float:
        """Score más alto = más confiable. Decae con fallos recientes."""
        h = self._health[prov]
        total = h["ok"] + h["fail"]
        if total == 0:
            return 0.3
        if total < 3:
            success_rate = h["ok"] / total
            return 0.3 + success_rate * 0.2
        success_rate = h["ok"] / total
        latency_penalty = max(0.0, (h["last_ms"] - 3000) / 10000)
        return max(0.0, success_rate - latency_penalty)

    def _record_outcome(self, prov: LLMProvider, ok: bool, latency_ms: float, err: str = ""):
        h = self._health[prov]
        h["last_ms"] = latency_ms
        if ok:
            h["ok"] += 1
            h["recent"].append(("ok", latency_ms))
        else:
            h["fail"] += 1
            h["recent"].append(("fail", err[:80]))
            # Cooldown exponencial: 5s tras 1 fallo, 15s tras 2 seguidos, 60s tras 3+
            recent_fails = sum(1 for r in h["recent"] if r[0] == "fail")
            cooldown = min(60, 5 * (2 ** (recent_fails - 1)))
            h["cooldown_until"] = time.time() + cooldown

    def _is_in_cooldown(self, prov: LLMProvider) -> bool:
        return time.time() < self._health[prov]["cooldown_until"]

    def health_stats(self) -> Dict[str, Any]:
        """Snapshot de salud de proveedores para /dashboard."""
        return {
            p.value: {
                "ok": h["ok"],
                "fail": h["fail"],
                "last_ms": round(h["last_ms"], 1),
                "score": round(self._provider_score(p), 3),
                "in_cooldown": self._is_in_cooldown(p),
            }
            for p, h in self._health.items()
            if h["ok"] + h["fail"] > 0
        }

    async def _call_one(
        self, prov: LLMProvider, prompt: str, system_message: Optional[str], temperature: float
    ) -> Dict[str, Any]:
        if prov == LLMProvider.GEMINI:
            return await self.call_gemini(prompt, system_message, temperature)
        if prov == LLMProvider.CLAUDE:
            return await self.call_claude(prompt, system_message, temperature)
        return await self._call_openai_compat(prov, prompt, system_message, temperature)

    async def call_llm(
        self,
        prompt: str,
        system_message: Optional[str] = None,
        temperature: float = 0.7,
        task_type: str = "general",
        provider: Optional[LLMProvider] = None,
    ) -> Dict[str, Any]:
        """Call LLM with retries + health-based fallback ordering.

        Mejoras vs. versión previa:
          - Reintenta cada proveedor hasta `self._max_retries` veces con backoff
            exponencial + jitter en errores transientes (5xx, timeout).
          - Reordena el fallback chain por score de salud (success_rate − latency
            penalty) — el proveedor más confiable según uso reciente va primero.
          - Cooldown automático: tras N fallos consecutivos, salta al proveedor
            durante un período creciente (5s → 15s → 60s).
        """
        if provider:
            providers_to_try = [provider]
        else:
            base = self._base_provider_order()
            # Reordenar por score, manteniendo Ollama relativamente alto (local-first)
            providers_to_try = sorted(base, key=lambda p: -self._provider_score(p))

        # Pre-filtrado: descartar los que no tienen credenciales o están en cooldown
        eligible = []
        for prov in providers_to_try:
            if prov == LLMProvider.GEMINI and not self._genai:
                continue
            cfg = _PROVIDERS.get(prov)
            if cfg and cfg["key_field"] and not getattr(self.settings, cfg["key_field"], None):
                continue
            if self._is_in_cooldown(prov):
                logger.debug(f"{prov.value} in cooldown — skipping")
                continue
            eligible.append(prov)

        if not eligible:
            # Si todos en cooldown, intenta al menos con uno (degraded mode)
            eligible = [
                p
                for p in providers_to_try
                if p == LLMProvider.OLLAMA
                or (_PROVIDERS.get(p, {}).get("key_field") and getattr(self.settings, _PROVIDERS[p]["key_field"], None))
            ][:1]

        last_error = "No providers available"
        for prov in eligible:
            for attempt in range(self._max_retries + 1):
                t0 = time.perf_counter()
                try:
                    logger.debug(f"Calling {prov.value} (attempt {attempt + 1}) for {task_type}...")
                    call_result = await self._call_one(prov, prompt, system_message, temperature)
                    latency_ms = (time.perf_counter() - t0) * 1000
                    self._record_outcome(prov, ok=True, latency_ms=latency_ms)
                    completion_tokens = int(call_result.get("completion_tokens") or 0)
                    tokens_per_s = (
                        round(completion_tokens / (latency_ms / 1000.0), 2)
                        if completion_tokens > 0 and latency_ms > 0
                        else 0.0
                    )
                    return {
                        "success": True,
                        "content": call_result["content"],
                        "provider": prov.value,
                        "latency_ms": round(latency_ms, 1),
                        "completion_tokens": completion_tokens,
                        "tokens_per_s": tokens_per_s,
                        "attempts": attempt + 1,
                    }
                except Exception as e:
                    err = str(e)
                    latency_ms = (time.perf_counter() - t0) * 1000
                    # Errores no recuperables (401/403/400 con mensaje) — no reintentar
                    is_transient = any(
                        k in err.lower()
                        for k in ("timeout", "503", "502", "504", "connection", "temporarily", "rate limit", "overload")
                    )
                    last_error = err
                    if not is_transient or attempt == self._max_retries:
                        self._record_outcome(prov, ok=False, latency_ms=latency_ms, err=err)
                        logger.warning(f"{prov.value} failed: {err}")
                        break
                    delay = self._retry_base_delay * (2**attempt) + random.uniform(0, 0.2)
                    logger.info(f"{prov.value} transient error, retry in {delay:.2f}s: {err[:80]}")
                    await asyncio.sleep(delay)

        return {
            "success": False,
            "content": None,
            "error": last_error,
            "provider": eligible[0].value if eligible else "none",
        }
