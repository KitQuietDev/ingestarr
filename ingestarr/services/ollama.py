from __future__ import annotations

import hashlib
import logging

from openai import OpenAI

log = logging.getLogger(__name__)


class OllamaClient:
    """OpenAI-compatible LLM client with response caching.

    Works with Ollama (local), OpenRouter, or any OpenAI-compatible endpoint.
    For Ollama: api_key defaults to "ollama" (no auth needed).
    For OpenRouter: set OLLAMA_URL=https://openrouter.ai/api and provide a real OLLAMA_API_KEY.
    """

    def __init__(self, base_url: str, model: str, api_key: str = "ollama"):
        self.model = model
        # Append /v1 only if not already present (OpenRouter URLs end in /v1)
        v1_url = base_url.rstrip("/")
        if not v1_url.endswith("/v1"):
            v1_url = f"{v1_url}/v1"
        self._client = OpenAI(
            base_url=v1_url,
            api_key=api_key,
        )
        self._cache: dict[str, str] = {}
        self._hits = 0
        self._misses = 0

    def complete(
        self,
        prompt: str,
        max_tokens: int = 200,
        system: str | None = None,
        temperature: float = 0.0,
        use_cache: bool = True,
    ) -> str:
        cache_key = self._cache_key(prompt, system, max_tokens)

        if use_cache and cache_key in self._cache:
            self._hits += 1
            log.debug("LLM cache hit (%d/%d)", self._hits, self._hits + self._misses)
            return self._cache[cache_key]

        self._misses += 1

        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        try:
            resp = self._client.chat.completions.create(
                model=self.model,
                messages=messages,
                max_tokens=max_tokens,
                temperature=temperature,
            )
            text = resp.choices[0].message.content or ""
        except Exception as exc:
            log.error("LLM call failed: %s", exc)
            raise

        if use_cache:
            self._cache[cache_key] = text
        return text

    @property
    def cache_stats(self) -> dict[str, int]:
        return {"hits": self._hits, "misses": self._misses, "size": len(self._cache)}

    def clear_cache(self) -> None:
        self._cache.clear()
        self._hits = 0
        self._misses = 0

    @staticmethod
    def _cache_key(prompt: str, system: str | None, max_tokens: int) -> str:
        raw = f"{system or ''}|{prompt}|{max_tokens}"
        return hashlib.sha256(raw.encode()).hexdigest()
