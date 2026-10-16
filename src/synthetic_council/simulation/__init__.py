"""LLM-agent simulation of ECB Governing Council rate decisions.

Pipeline per meeting (anchored to the real accounts):

1. The Chief Economist's proposal — extracted verbatim from the monetary
   policy account (``mpa_extract.ce_proposal``) — opens the meeting.
2. Every other member reacts to the proposal (round 1, memo-driven), then
   responds to colleagues (round 2); the Chief Economist always gets a reply
   turn after the discussion. Statements come from a configurable
   OpenAI-compatible generator backend.
3. The extractor role from ``inference.toml`` (a native decisions model such
   as Clef on OpenRouter, or any chat model via the choice-v1 protocol)
   measures each reacting member's position (raise/hold/lower) + full
   probability distribution + conviction from the round-1 statements, and
   again from everyone's final statements.
4. The President formulates the decision; a modal-voter cross-check is
   recorded alongside.
5. Per member, the extractor classifies {aligned, non_aligned} against the
   simulated decision AND the real ECB decision (full distributions saved).
6. The simulated decision mode (unanimity/consensus/majority) is measured
   twice: the extractor with the same criteria as the ground-truth
   extraction, and a deterministic aggregation of the alignment
   probabilities.

Every generator/extractor message is archived per meeting (messages.jsonl,
transcript.md) for inspection and reuse. Experiment arms: ``ea_only`` vs
``country`` (governors additionally see their country annex).
"""

from synthetic_council.simulation.agent import Agent, load_agents, load_roster
from synthetic_council.simulation.alignment import AlignmentResult, run_alignment
from synthetic_council.simulation.config import SimulationConfig
from synthetic_council.simulation.decision import Decision, run_decision
from synthetic_council.simulation.discussion import (
    DiscussionResult,
    DiscussionTurn,
    first_round_statements,
    run_discussion,
    run_final_statements,
)
from synthetic_council.simulation.extraction import (
    PositionExtraction,
    extract_position,
    extract_positions,
)
from synthetic_council.simulation.generator import BaseGenerator, build_generator
from synthetic_council.simulation.mode import ModeResult, run_mode
from synthetic_council.simulation.recorder import Recorder
from synthetic_council.simulation.runner import load_ce_proposal, run_meeting, run_simulation

__all__ = [
    "SimulationConfig",
    "BaseGenerator",
    "build_generator",
    "PositionExtraction",
    "extract_position",
    "extract_positions",
    "Agent",
    "load_agents",
    "load_roster",
    "DiscussionTurn",
    "DiscussionResult",
    "run_discussion",
    "first_round_statements",
    "run_final_statements",
    "Decision",
    "run_decision",
    "AlignmentResult",
    "run_alignment",
    "ModeResult",
    "run_mode",
    "Recorder",
    "load_ce_proposal",
    "run_meeting",
    "run_simulation",
]
