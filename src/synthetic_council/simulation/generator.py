"""Statement generator backend (OpenAI-compatible, config-driven).

One backend only: any endpoint/model configured as the ``generator`` role
in ``inference.toml`` (Featherless GLM-5.3-Flash by default) through
``synthetic_council.inference.Client`` — the former Vercel-gateway and
Jev paths are gone. Position/confidence extraction never goes through
here either: it is a ``choice`` call on the ``extractor`` role.

The generator emits a full ``CallEvent`` per call (system, user, content,
model identity, sampling params, usage, latency) through ``on_event`` so
the per-meeting ledger records everything needed to replay or branch.
"""

from __future__ import annotations

import abc
from collections.abc import Callable
from typing import Any

from synthetic_council.inference import CallEvent, Client, InferenceConfig


class BaseGenerator(abc.ABC):
    """Generate a free-text statement from a system prompt and a user prompt."""

    @abc.abstractmethod
    async def generate(
        self,
        system: str,
        user: str,
        context: dict[str, Any] | None = None,
        temperature: float | None = None,
    ) -> str: ...

    @abc.abstractmethod
    async def aclose(self) -> None: ...


class ChatGenerator(BaseGenerator):
    """Role-bound OpenAI-compatible generator.

    Public ``model``/``endpoint``/``params`` attributes carry provenance
    for experiment settings blocks (and fix the historic bug where the
    archived model name could drift from the model actually used).
    """

    def __init__(
        self,
        config: InferenceConfig,
        role_name: str = "generator",
        on_event: Callable[[CallEvent], None] | None = None,
        temperature: float | None = None,
    ):
        self.role = config.role(role_name)
        self.client = Client(config)
        self.on_event = on_event
        self.temperature = temperature
        self.model = self.role.model
        self.endpoint = self.role.endpoint
        self.params = dict(self.role.params)

    @property
    def model_id(self) -> str:
        return self.role.model_id

    async def generate(
        self,
        system: str,
        user: str,
        context: dict[str, Any] | None = None,
        temperature: float | None = None,
    ) -> str:
        res = await self.client.chat(self.role.name, system, user, temperature or self.temperature)
        if self.on_event is not None:
            self.on_event(
                CallEvent(
                    kind="chat",
                    role=self.role.name,
                    endpoint=res.endpoint,
                    model=res.model,
                    params=res.params,
                    system=system,
                    user=user,
                    content=res.content,
                    usage=res.usage,
                    latency_ms=res.latency_ms,
                    raw={"endpoint": res.endpoint, "model": res.model},
                    context=context or {},
                )
            )
        return res.content

    async def aclose(self) -> None:
        await self.client.aclose()


def build_generator(cfg, on_event: Callable[[CallEvent], None] | None = None) -> BaseGenerator:
    """Generator for the config's generator role; ``cfg`` is a
    SimulationConfig carrying ``cfg.inference``."""
    return ChatGenerator(cfg.inference, "generator", on_event=on_event)
