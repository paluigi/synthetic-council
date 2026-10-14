"""Tests for the restructured simulation package.

API-touching backends are faked: these tests verify orchestration (CE
proposal anchor, two non-CE rounds, CE reply, decision, alignment, mode),
prompts, extraction parsing, the message archive — not the gateway.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import polars as pl
import pytest

from synthetic_council.simulation.agent import load_roster
from synthetic_council.simulation.alignment import real_decision_text
from synthetic_council.simulation.config import SimulationConfig
from synthetic_council.simulation.discussion import (
    run_discussion,
    run_final_statements,
)
from synthetic_council.simulation.extraction import extract_position
from synthetic_council.simulation.runner import run_meeting

MEETING = "2022-07-21"
CE = "Philip R. Lane"
PRESIDENT = "Christine Lagarde"
PROPOSAL = (
    "Mr Lane therefore proposed that the three key ECB interest rates be raised by 50 basis points."
)

MEMO = """# Briefing memo — {name}

**Role:** {role}
{country_line}**Meeting:** GC monetary policy meeting, {date}
**Current rates:** MRO 0.5% · DFR 0.0% · MLF 0.75%

## Euro area economic situation (data available at the meeting)

| Indicator | Reference | Value |
|---|---|---|
| HICP inflation (y/y %) | 2022-06 | 8.64 |
"""

PEOPLE = [
    # person, role, country, voting, ce
    (PRESIDENT, "ECB President", None, True, False),
    ("Test Person", "Governor, National Central Bank of Testland", "Testland", True, False),
    (CE, "ECB Executive Board member", None, True, True),
]


def _memo(name: str, role: str, country: str | None) -> str:
    return MEMO.format(
        name=name,
        role=role,
        country_line=f"**Country:** {country}\n" if country else "",
        date=MEETING,
    )


@pytest.fixture
def cfg(tmp_path: Path) -> SimulationConfig:
    memos = tmp_path / "memos_ea"
    memos.mkdir()
    for person, role, country, _, _ in PEOPLE:
        (memos / f"{MEETING}_{person.replace(' ', '_')}.md").write_text(
            _memo(person, role, country), encoding="utf-8"
        )
    parts = tmp_path / "account_participants.parquet"
    pl.DataFrame(
        {
            "meeting_date": [MEETING] * len(PEOPLE),
            "person": [p[0] for p in PEOPLE],
            "role": [p[1] for p in PEOPLE],
            "country": [p[2] for p in PEOPLE],
            "replacing": [None] * len(PEOPLE),
            "voting_right": [p[3] for p in PEOPLE],
            "is_chief_economist": [p[4] for p in PEOPLE],
            "in_memberships": [True] * len(PEOPLE),
        }
    ).write_parquet(parts)
    pl.DataFrame(
        {
            "meeting_date": [MEETING],
            "chief_economist": [CE],
            "proposal_speaker": ["Mr Lane"],
            "proposal_text": [PROPOSAL],
            "proposed_direction": ["hike"],
            "proposed_bp": [50],
            "ce_mismatch": [False],
            "extraction_method": ["deterministic"],
            "source_span": ["..."],
        }
    ).write_parquet(tmp_path / "ce_proposal.parquet")
    pl.DataFrame(
        {
            "announcement_date": ["2022-06-09", MEETING],
            "decision": ["hold", "change"],
            "rate_effective_date": [None, "2022-07-27"],
            "mro": [-0.50, 0.50],
            "dfr": [-0.75, 0.00],
            "mlf": [0.25, 0.75],
            "press_release_url": ["", ""],
            "published_at": [None, None],
        }
    ).write_parquet(tmp_path / "gc_decisions.parquet")
    return SimulationConfig(
        arm="ea_only",
        memos_dir=memos,
        out_root=tmp_path / "simulations",
        participants_path=parts,
        ce_proposal_path=tmp_path / "ce_proposal.parquet",
        gc_decisions_path=tmp_path / "gc_decisions.parquet",
        n_rounds=2,
    )


class FakeGenerator:
    """Deterministic statements routed by prompt content; records prompts."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    async def generate(self, system: str, user: str) -> str:
        self.calls.append((system, user))
        if "opening reaction" in system:
            return "Inflation is far above target. Position: raise the rate"
        if "speak once more" in system:
            return "I hear my colleagues; I stay with my view. Position: raise the rate"
        if "reply to the discussion" in system:
            return "I maintain the proposal unchanged."
        if "Close the meeting" in system or "close the meeting" in system:
            return "The Council decides to follow the proposal. Decision: raise the rate"
        return "Having weighed the discussion, I remain convinced. Position: hold the rate"

    async def aclose(self) -> None:
        return None


class FakeJev:
    """Returns per-question canned answers; records the questions asked."""

    position = "raise"
    conviction = 1.5
    choice = "aligned"  # used for alignment {aligned, non_aligned} and mode

    def __init__(self) -> None:
        self.asked: list[dict] = []

    async def evaluate(self, *a, **kw):
        self.asked.append(kw.get("questions", {}))
        questions = kw.get("questions", {})

        class Ans:
            def __init__(self, **k):
                self.__dict__.update(k)

        class Value:
            pass

        answers = {}
        if "position" in questions:
            answers["position"] = Ans(
                choice=FakeJev.position,
                probabilities={"raise": 0.9, "hold": 0.1, "lower": 0.0},
            )
        if "conviction" in questions:
            answers["conviction"] = Ans(score=FakeJev.conviction)
        if "choice" in questions:
            answers["choice"] = Ans(
                choice=FakeJev.choice,
                probabilities={"aligned": 0.95, "non_aligned": 0.05}
                if FakeJev.choice == "aligned"
                else {"aligned": 0.2, "non_aligned": 0.8},
            )
        item = type(
            "Item",
            (),
            {
                "value": type("V", (), {"answers": answers})(),
                "provider_metadata": {"typesafe": {"confidence": {"x": 1}}},
                "usage": None,
                "model_dump": lambda self, mode="json": {"fake": True},
            },
        )()
        return item


@pytest.fixture
def fake_jev(monkeypatch):
    fj = FakeJev()

    async def fake_evaluate(*a, **kw):
        return await fj.evaluate(*a, **kw)

    def fake_get_model(_):
        return object()

    import ai
    import ai.ops

    monkeypatch.setattr(ai.ops, "experimental_evaluate", fake_evaluate)
    monkeypatch.setattr(ai, "get_model", fake_get_model)
    monkeypatch.setattr(ai.ops, "ChoiceQuestion", lambda **kw: kw)
    monkeypatch.setattr(ai.ops, "ScoreQuestion", lambda **kw: kw)
    return fj


# --- roster ---------------------------------------------------------------


def test_load_roster_from_participants(cfg: SimulationConfig):
    agents, skipped = load_roster(MEETING, cfg)
    by = {a.person: a for a in agents}
    assert skipped == []
    assert by[CE].is_chief_economist is True
    assert by[PRESIDENT].role == "ECB President"
    assert by["Test Person"].country == "Testland"
    assert by["Test Person"].voting_right is True
    assert by[CE].role_line == "Executive Board member and Chief Economist of the ECB"


def test_reaction_prompt_embeds_proposal_and_memo(cfg: SimulationConfig):
    agents, _ = load_roster(MEETING, cfg)
    test = next(a for a in agents if a.person == "Test Person")
    system, user = test.reaction_prompt(cfg, CE, PROPOSAL)
    assert "opening reaction" in system
    assert PROPOSAL in system
    assert CE in system
    assert "8.64" in user  # memo table
    assert "Position:" not in system  # reactions are free-form


# --- discussion flow -------------------------------------------------------


def test_discussion_flow_proposal_rounds_ce_reply(cfg: SimulationConfig):
    agents, _ = load_roster(MEETING, cfg)
    gen = FakeGenerator()
    result = asyncio.run(run_discussion(agents, PROPOSAL, CE, gen, cfg))
    # 1 proposal + (2 non-CE x 2 rounds) + 1 CE reply
    assert [t.round_no for t in result.turns] == [0, 1, 1, 2, 2, -1]
    assert result.turns[0].text == PROPOSAL
    assert result.turns[0].person == CE
    assert result.turns[-1].person == CE
    speakers = {t.person for t in result.turns if t.round_no > 0}
    assert speakers == {"Test Person", PRESIDENT}  # CE silent in the rounds
    # round-2 prompts embed the round-1 transcript and the proposal
    round2 = [s for s, _ in gen.calls if "speak once more" in s]
    assert len(round2) == 2
    assert "[Round 1]" in round2[0]
    assert PROPOSAL in round2[0]
    # CE reply prompt sees the discussion
    ce_prompt = [s for s, _ in gen.calls if "reply to the discussion" in s][0]
    assert "[Round 2]" in ce_prompt


def test_discussion_requires_two_rounds(cfg: SimulationConfig):
    agents, _ = load_roster(MEETING, cfg)
    cfg.n_rounds = 1
    with pytest.raises(ValueError):
        asyncio.run(run_discussion(agents, PROPOSAL, CE, FakeGenerator(), cfg))


def test_discussion_requires_ce(cfg: SimulationConfig):
    agents, _ = load_roster(MEETING, cfg)
    for a in agents:
        a.is_chief_economist = False
    with pytest.raises(ValueError):
        asyncio.run(run_discussion(agents, PROPOSAL, CE, FakeGenerator(), cfg))


def test_final_statements_reference_proposal(cfg: SimulationConfig):
    agents, _ = load_roster(MEETING, cfg)
    gen = FakeGenerator()
    discussion = asyncio.run(run_discussion(agents, PROPOSAL, CE, gen, cfg))
    asyncio.run(run_final_statements(agents, PROPOSAL, discussion, gen, cfg))
    final_prompts = [s for s, _ in gen.calls if "final position" in s]
    assert len(final_prompts) == 3  # everyone, CE included
    assert PROPOSAL in final_prompts[0]
    assert all("Position:" in a.final_statement for a in agents)


# --- extraction ------------------------------------------------------------


def test_extract_position_parses_probabilities(fake_jev):
    ext = asyncio.run(extract_position("stmt", "memo", SimulationConfig()))
    assert ext.position == "raise"
    assert ext.probabilities == {"raise": 0.9, "hold": 0.1, "lower": 0.0}
    assert ext.conviction == 1.5
    assert ext.margin == pytest.approx(0.8)


# --- decision / alignment / mode -------------------------------------------


def test_real_decision_text_change_and_hold(cfg: SimulationConfig):
    text = real_decision_text(MEETING, cfg)
    assert "raise" in text and "100 basis points" in text  # -0.50 -> 0.50
    assert "MRO 0.5%" in text


def test_run_meeting_end_to_end(cfg: SimulationConfig, fake_jev):
    payload = asyncio.run(run_meeting(MEETING, cfg, generator=FakeGenerator()))

    assert payload["arm"] == "ea_only"
    assert payload["n_agents"] == 3
    assert payload["ce_proposal"]["text"] == PROPOSAL
    assert payload["ce_proposal"]["direction"] == "hike"
    assert len(payload["turns"]) == 6
    assert payload["decision"]["stance"] == "raise"
    assert payload["decision"]["president"] == PRESIDENT
    assert payload["decision"]["agrees_with_modal"] is True
    # alignment distributions against both references for every agent
    for a in payload["agents"]:
        assert set(a["alignment"]["sim"]) == {"aligned", "non_aligned"}
        assert set(a["alignment"]["real"]) == {"aligned", "non_aligned"}
    # deterministic mode: all fake alignments at 0.95 -> unanimity
    assert payload["mode"]["deterministic_label"] == "unanimity"
    # consensus tallies: initial extraction only for the two reactors
    assert payload["consensus"]["before"] == {"raise": 2, "hold": 0, "lower": 0}


def test_run_meeting_writes_archive(cfg: SimulationConfig, fake_jev):
    asyncio.run(run_meeting(MEETING, cfg, generator=FakeGenerator()))
    out = cfg.out_root / "ea_only" / MEETING
    payload = json.loads((out / "payload.json").read_text())
    assert payload["meeting_date"] == MEETING
    messages = [json.loads(line) for line in (out / "messages.jsonl").read_text().splitlines()]
    stages = {m["stage"] for m in messages}
    assert {
        "round-1",
        "round-2",
        "ce-reply",
        "final-statement",
        "decision",
        "position-extraction",
        "alignment-sim",
        "alignment-real",
        "decision-mode",
    } <= stages
    transcript = (out / "transcript.md").read_text()
    assert "Proposal — Philip R. Lane" in transcript
    assert "Chief Economist's reply" in transcript
    assert "## Decision — Christine Lagarde" in transcript


def test_deterministic_mode_majority_when_split(cfg: SimulationConfig, fake_jev):
    # all agents non-aligned (0.2) -> share 0.0 < 0.7 -> majority
    FakeJev.choice = "non_aligned"
    try:
        payload = asyncio.run(run_meeting(MEETING, cfg, generator=FakeGenerator()))
        assert payload["mode"]["deterministic_label"] == "majority"
    finally:
        FakeJev.choice = "aligned"
