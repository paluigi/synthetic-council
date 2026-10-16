"""Simulation configuration."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from synthetic_council.config import PROCESSED_DIR
from synthetic_council.inference import InferenceConfig, load_config


@dataclass
class SimulationConfig:
    """Tunable knobs for the council simulation.

    All inference (statement generation on the ``generator`` role,
    position/alignment/mode measurement on the ``extractor`` role) is
    configured in ``inference.toml`` — providers, models and sampling
    parameters never live here or in code. The loaded config travels with
    the SimulationConfig and is written into every experiment payload as a
    ``settings`` block so runs stay comparable and reproducible.

    The experiment ``arm`` selects the memo set: ``ea_only`` gives every
    member the euro-area-only memo; ``country`` additionally gives national
    governors their country annex (data/processed/memos_ea_country).
    """

    # --- experiment arm --------------------------------------------------
    arm: str = "ea_only"  # "ea_only" | "country"
    memos_dir: Path | None = None  # defaults per arm (see resolved_memos_dir)
    out_root: Path = field(default_factory=lambda: PROCESSED_DIR / "simulations")

    # --- inference (see inference.toml) -----------------------------------
    inference: InferenceConfig = field(default_factory=load_config)

    # --- discussion shape -------------------------------------------------
    n_rounds: int = 2  # discussion rounds for the non-CE members
    max_statements_chars: int = 600  # target length for each statement
    max_transcript_chars: int = 24_000  # transcript tail shown to each agent

    # --- concurrency -------------------------------------------------------
    max_concurrent_extractions: int = 8
    # round-1 reactions and final statements are transcript-independent, so
    # they generate concurrently (rounds 2+ stay sequential by design — each
    # speaker sees the transcript so far); bounded for provider rate limits
    max_concurrent_statements: int = 6

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

    def settings(self) -> dict:
        """Everything that determines this run's outcome: inference roles
        (endpoint/model/sampling), choice version, simulation shape and
        aggregation thresholds — one diffable block for the payload."""
        import subprocess

        from synthetic_council.member_profiles.speech_sections import (
            PRESS_PROMPT_VERSION,
            PROMPT_VERSION,
        )
        from synthetic_council.mpa_extract.decision_mode import PROMPT_VERSION as DM_VERSION

        try:
            git_sha = subprocess.run(
                ["git", "rev-parse", "--short", "HEAD"],
                capture_output=True,
                text=True,
                check=True,
                timeout=10,
            ).stdout.strip()
        except Exception:
            git_sha = "unknown"
        return {
            "inference": self.inference.settings(),
            "simulation": {
                "n_rounds": self.n_rounds,
                "max_statements_chars": self.max_statements_chars,
                "max_transcript_chars": self.max_transcript_chars,
                "max_concurrent_extractions": self.max_concurrent_extractions,
                "max_concurrent_statements": self.max_concurrent_statements,
                "unanimity_min_p": self.unanimity_min_p,
                "consensus_share": self.consensus_share,
                "consensus_min_p": self.consensus_min_p,
            },
            "prompt_versions": {
                "speech_sections": PROMPT_VERSION,
                "speech_sections_press": PRESS_PROMPT_VERSION,
                "decision_mode": DM_VERSION,
            },
            "git_sha": git_sha,
        }

    # --- message archive ---------------------------------------------------
    # attached by the runner; every generator/choice request+response is logged
    recorder: object | None = field(default=None, repr=False, compare=False)
