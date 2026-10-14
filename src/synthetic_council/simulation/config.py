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

    The experiment ``arm`` selects the memo set: ``ea_only`` gives every
    member the euro-area-only memo; ``country`` additionally gives national
    governors their country annex (data/processed/memos_ea_country).
    """

    # --- experiment arm --------------------------------------------------
    arm: str = "ea_only"  # "ea_only" | "country"
    memos_dir: Path | None = None  # defaults per arm (see resolved_memos_dir)
    out_root: Path = field(default_factory=lambda: PROCESSED_DIR / "simulations")

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
    n_rounds: int = 2  # discussion rounds for the non-CE members
    max_statements_chars: int = 600  # target length for each statement
    max_transcript_chars: int = 24_000  # transcript tail shown to each agent

    # --- concurrency -------------------------------------------------------
    max_concurrent_extractions: int = 8

    # --- ground-truth inputs (CE proposal anchor, real decisions) ---------
    ce_proposal_path: Path = field(
        default_factory=lambda: PROCESSED_DIR / "account_ce_proposal.parquet"
    )
    gc_decisions_path: Path = field(default_factory=lambda: PROCESSED_DIR / "gc_decisions.parquet")
    participants_path: Path = field(
        default_factory=lambda: PROCESSED_DIR / "account_participants.parquet"
    )

    # --- simulated decision-mode aggregation thresholds -------------------
    # deterministic mapping from per-agent P(aligned with the decision):
    # unanimity if EVERY member is aligned with p >= unanimity_min_p;
    # consensus if the aligned share >= consensus_share and no member's
    # alignment falls below consensus_min_p; otherwise majority.
    unanimity_min_p: float = 0.85
    consensus_share: float = 0.70
    consensus_min_p: float = 0.20

    def resolved_memos_dir(self) -> Path:
        if self.memos_dir is not None:
            return self.memos_dir
        if self.arm == "country":
            return PROCESSED_DIR / "memos_ea_country"
        return PROCESSED_DIR / "memos_ea"

    # --- message archive ---------------------------------------------------
    # attached by the runner; every generator/Jev request+response is logged
    recorder: object | None = field(default=None, repr=False, compare=False)
