"""Statement generator backends.

Two interchangeable backends produce the agents' statements:

- ``gateway`` — Vercel AI Gateway through the ``ai`` package
  (``experimental_generate``); model ids look like ``openai/gpt-4o-mini``.
- ``openai_compat`` — any OpenAI-compatible ``POST {base_url}/chat/completions``
  endpoint (vLLM, Ollama, OpenRouter, a corporate proxy, ...), configured via
  ``OPENAI_COMPAT_BASE_URL`` / ``OPENAI_COMPAT_API_KEY`` / the explicit model
  argument.

Position/confidence *extraction* never goes through here: it is always Jev via
the gateway (see ``extraction.py``).
"""

from __future__ import annotations

import abc
import asyncio
import json
import urllib.request
from typing import Any

from synthetic_council.simulation.config import SimulationConfig


class BaseGenerator(abc.ABC):
    """Generate a free-text statement from a system prompt and a user prompt."""

    @abc.abstractmethod
    async def generate(self, system: str, user: str) -> str: ...

    @abc.abstractmethod
    async def aclose(self) -> None: ...


class GatewayGenerator(BaseGenerator):
    """Statements via the Vercel AI Gateway (`ai` package)."""

    def __init__(self, model_id: str, temperature: float = 0.7) -> None:
        from ai import get_model  # deferred: only needed by this backend

        self._model = get_model(model_id)
        self._temperature = temperature

    async def generate(self, system: str, user: str) -> str:
        from ai import (
            InferenceRequestParams,
            TemperatureSamplerParams,
            experimental_generate,
            system_message,
            user_message,
        )

        params = InferenceRequestParams(
            sampling={"temperature": TemperatureSamplerParams(temperature=self._temperature)}
        )
        last_exc: Exception | None = None
        for attempt in range(3):
            try:
                msg = await experimental_generate(
                    self._model,
                    messages=[system_message(system), user_message(user)],
                    params=params,
                )
                return "".join(
                    p.text for p in msg.parts if getattr(p, "kind", None) == "text"
                ).strip()
            except Exception as exc:  # transient gateway timeouts/5xx
                last_exc = exc
                if attempt == 2:
                    raise
                await asyncio.sleep(2**attempt * 2)
        raise RuntimeError(f"generate failed: {last_exc}")

    async def aclose(self) -> None:
        return None


class OpenAICompatGenerator(BaseGenerator):
    """Statements via any OpenAI-compatible chat-completions endpoint.

    Stdlib-only HTTP (no extra dependency); requires ``base_url`` (e.g.
    ``http://localhost:8000/v1``) and an optional API key.
    """

    def __init__(
        self,
        model: str,
        base_url: str,
        api_key: str = "",
        temperature: float = 0.7,
    ) -> None:
        if not base_url:
            raise ValueError(
                "openai_compat backend needs OPENAI_COMPAT_BASE_URL "
                "(or config.openai_compat_base_url)"
            )
        self._url = base_url.rstrip("/") + "/chat/completions"
        self._model = model
        self._api_key = api_key
        self._temperature = temperature

    async def generate(self, system: str, user: str) -> str:
        payload: dict[str, Any] = {
            "model": self._model,
            "temperature": self._temperature,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        body = json.dumps(payload).encode()

        def _post() -> dict[str, Any]:
            req = urllib.request.Request(
                self._url, data=body, headers=headers, method="POST"
            )
            with urllib.request.urlopen(req, timeout=120) as resp:
                return json.loads(resp.read())

        last_exc: Exception | None = None
        for attempt in range(3):
            try:
                data = await asyncio.to_thread(_post)
                return data["choices"][0]["message"]["content"].strip()
            except Exception as exc:
                last_exc = exc
                if attempt == 2:
                    raise
                await asyncio.sleep(2**attempt * 2)
        raise RuntimeError(f"generate failed: {last_exc}")

    async def aclose(self) -> None:
        return None


def build_generator(cfg: SimulationConfig) -> BaseGenerator:
    """Instantiate the configured generator backend."""
    if cfg.generator_backend == "gateway":
        return GatewayGenerator(cfg.generator_model, cfg.generator_temperature)
    if cfg.generator_backend == "openai_compat":
        model = cfg.openai_compat_model or cfg.generator_model
        return OpenAICompatGenerator(
            model,
            cfg.openai_compat_base_url,
            cfg.openai_compat_api_key,
            cfg.generator_temperature,
        )
    raise ValueError(f"unknown generator backend: {cfg.generator_backend!r}")
