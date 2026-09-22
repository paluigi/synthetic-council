"""Simulation configuration."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from synthetic_council.config import PROCESSED_DIR


@dataclass
class SimulationConfig:
    """Tunable knobs for the council simulation.

    Generator (statements) vs extractor (positions) are separate backends:
    statements come from any OpenAI-compatible API, position/confidence
    extraction is always Jev via the Vercel AI Gateway.
    """

    # --- briefing memos -------------------------------------------------
    memos_dir: Path = field(default_factory=lambda: PROCESSED_DIR / "memos")

    # --- generator backend (statements, discussion turns) ---------------
    # "gateway"       -> Vercel AI Gateway via the `ai` package
    # "openai_compat" -> any OpenAI-compatible chat-completions endpoint
    generator_backend: str = "gateway"
    generator_model: str = "openai/gpt-4o-mini"
    # used only by the openai_compat backend
    openai_compat_base_url: str = field(
        default_factory=lambda: os.environ.get("OPENAI_COMPAT_BASE_URL", "")
    )
    openai_compat_api_key: str = field(
        default_factory=lambda: os.environ.get("OPENAI_COMPAT_API_KEY", "")
    )
    openai_compat_model: str = field(
        default_factory=lambda: os.environ.get("OPENAI_COMPAT_MODEL", "")
    )
    generator_temperature: float = 0.7

    # --- extractor backend (always Jev / gateway) ------------------------
    extractor_model: str = "typesafe-ai/jev"

    # --- discussion shape -------------------------------------------------
    n_rounds: int = 2  # every member speaks once per round
    max_statements_chars: int = 600  # target length for each statement
    max_transcript_chars: int = 24_000  # transcript tail shown to each agent

    # --- concurrency -------------------------------------------------------
    max_concurrent_extractions: int = 8
