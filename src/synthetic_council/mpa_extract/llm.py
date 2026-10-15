"""Minimal chat-LLM access for the accounts/profile pipelines.

Goes through the ``generator`` role of ``inference.toml`` (one shared,
config-driven path for every LLM call in the project) and archives every
call — full inputs, output, model identity, sampling params, usage — to a
per-context JSONL sink under ``data/processed/llm_calls/`` so utility
calls are replayable and branchable exactly like simulation calls.
"""

from __future__ import annotations

from synthetic_council.config import PROCESSED_DIR
from synthetic_council.inference import chat_sync, jsonl_sink, load_config

LLM_CALLS_DIR = PROCESSED_DIR / "llm_calls"


def chat(
    system: str,
    user: str,
    temperature: float | None = None,
    context: str = "misc",
) -> str:
    """One synchronous chat call on the generator role; archived to
    ``data/processed/llm_calls/{context}.jsonl``."""
    config = load_config()
    sink = jsonl_sink(LLM_CALLS_DIR / f"{context}.jsonl")

    def on_event(event) -> None:
        event.context = {"context": context}
        sink(event)

    return chat_sync(config, "generator", system, user, temperature, on_event)
