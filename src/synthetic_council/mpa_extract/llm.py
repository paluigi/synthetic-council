"""Minimal chat-LLM access for the accounts extraction pipeline.

Reuses the simulation generator backends (Vercel AI Gateway or any
OpenAI-compatible endpoint) so every LLM call in the project goes through one
configurable path. No API calls are made unless a fallback needs them.
"""

from __future__ import annotations

import asyncio


async def chat_async(system: str, user: str, temperature: float = 0.2) -> str:
    from synthetic_council.simulation.config import SimulationConfig
    from synthetic_council.simulation.generator import build_generator

    cfg = SimulationConfig()
    gen = build_generator(cfg)
    try:
        return await gen.generate(system, user)
    finally:
        await gen.aclose()


def chat(system: str, user: str, temperature: float = 0.2) -> str:
    return asyncio.run(chat_async(system, user, temperature))
