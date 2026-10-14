"""Tests for the restructured simulation package.

API calls are faked through an httpx.MockTransport injected into the
SimulationConfig's InferenceConfig (plain parameter injection, no
patching): these tests verify orchestration (CE proposal anchor, two
non-CE rounds, CE reply, decision, alignment, mode), prompts, extraction
parsing, the call ledger and the settings block — not the network.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx
import polars as pl
import pytest

from synthetic_council.simulation.agent import load_roster
from synthetic_council.simulation.alignment import real_decision_text
from synthetic_council.simulation.config import SimulationConfig
from synthetic_council.simulation.discussion import run_discussion, run_final_statements
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


# --- the fake backend: one MockTransport routing on prompt content -------

STATE = {"choice": "aligned"}  # mutable toggles for individual tests


def _chat_response(content: str) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "choices": [{"message": {"content": content}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5},
        },
    )


def _handler(request: httpx.Request) -> httpx.Response:
    url = str(request.url)
    body = json.loads(request.content)
    if "alpha/decisions" in url:  # native decisions API (extractor role)
        questions = body.get("questions", {})
        if "conviction" in questions:  # position extraction
            answers = {
                "position": {
                    "type": "choice",
                    "choice": "raise",
                    "probabilities": {"raise": 0.9, "hold": 0.1, "lower": 0.0},
                },
                "conviction": {
                    "type": "score",
                    "score": 1.6,
                    "probabilities": {"0": 0.1, "1": 0.2, "2": 0.7},
                },
            }
        elif "reference_decision" in body.get("state", {}):  # alignment
            aligned = STATE["choice"] == "aligned"
            answers = {
                "choice": {
                    "type": "choice",
                    "choice": STATE["choice"],
                    "probabilities": (
                        {"aligned": 0.95, "non_aligned": 0.05}
                        if aligned
                        else {"aligned": 0.2, "non_aligned": 0.8}
                    ),
                }
            }
        else:  # simulated decision mode
            answers = {
                "choice": {
                    "type": "choice",
                    "choice": "unanimity",
                    "probabilities": {"unanimity": 0.8, "consensus": 0.15, "majority": 0.05},
                }
            }
        return httpx.Response(
            200,
            json={"answers": answers, "usage": {"input_tokens": 12, "output_tokens": 0}},
        )
    system = body["messages"][0]["content"]
    # generator statements, routed by prompt content
    if "opening reaction" in system:
        return _chat_response("Inflation is far above target. Position: raise the rate")
    if "speak once more" in system:
        return _chat_response("I hear my colleagues; I stay with my view. Position: raise the rate")
    if "reply to the discussion" in system:
        return _chat_response("I maintain the proposal unchanged.")
    if "Close the meeting" in system or "close the meeting" in system:
        return _chat_response(
            "The Council decides to follow the proposal. Decision: raise the rate"
        )
    return _chat_response(
        "Having weighed the discussion, I remain convinced. Position: hold the rate"
    )


class Recorder:
    """Collects generator system prompts for flow assertions."""

    def __init__(self) -> None:
        self.calls: list[str] = []


@pytest.fixture
def gen_calls():
    return Recorder()


@pytest.fixture
def cfg(tmp_path: Path, gen_calls) -> SimulationConfig:
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
    config = SimulationConfig(
        arm="ea_only",
        memos_dir=memos,
        out_root=tmp_path / "simulations",
        participants_path=parts,
        ce_proposal_path=tmp_path / "ce_proposal.parquet",
        gc_decisions_path=tmp_path / "gc_decisions.parquet",
        n_rounds=2,
    )
    calls: list[str] = gen_calls.calls

    def transport_handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if "messages" in body:  # generator chat calls only
            calls.append(body["messages"][0]["content"])
        return _handler(request)

    config.inference.transport = httpx.MockTransport(transport_handler)
    return config


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


def test_discussion_flow_proposal_rounds_ce_reply(cfg: SimulationConfig, gen_calls):
    agents, _ = load_roster(MEETING, cfg)
    from synthetic_council.simulation.generator import build_generator

    gen = build_generator(cfg)
    result = asyncio.run(run_discussion(agents, PROPOSAL, CE, gen, cfg))
    # 1 proposal + (2 non-CE x 2 rounds) + 1 CE reply
    assert [t.round_no for t in result.turns] == [0, 1, 1, 2, 2, -1]
    assert result.turns[0].text == PROPOSAL
    assert result.turns[0].person == CE
    assert result.turns[-1].person == CE
    speakers = {t.person for t in result.turns if t.round_no > 0}
    assert speakers == {"Test Person", PRESIDENT}  # CE silent in the rounds
    # round-2 prompts embed the round-1 transcript and the proposal
    round2 = [s for s in gen_calls.calls if "speak once more" in s]
    assert len(round2) == 2
    assert "[Round 1]" in round2[0]
    assert PROPOSAL in round2[0]
    # CE reply prompt sees the discussion
    ce_prompt = [s for s in gen_calls.calls if "reply to the discussion" in s][0]
    assert "[Round 2]" in ce_prompt


def test_discussion_requires_two_rounds(cfg: SimulationConfig):
    agents, _ = load_roster(MEETING, cfg)
    cfg.n_rounds = 1
    from synthetic_council.simulation.generator import build_generator

    with pytest.raises(ValueError):
        asyncio.run(run_discussion(agents, PROPOSAL, CE, build_generator(cfg), cfg))


def test_discussion_requires_ce(cfg: SimulationConfig):
    agents, _ = load_roster(MEETING, cfg)
    for a in agents:
        a.is_chief_economist = False
    from synthetic_council.simulation.generator import build_generator

    with pytest.raises(ValueError):
        asyncio.run(run_discussion(agents, PROPOSAL, CE, build_generator(cfg), cfg))


def test_final_statements_reference_proposal(cfg: SimulationConfig, gen_calls):
    agents, _ = load_roster(MEETING, cfg)
    from synthetic_council.simulation.generator import build_generator

    gen = build_generator(cfg)
    discussion = asyncio.run(run_discussion(agents, PROPOSAL, CE, gen, cfg))
    asyncio.run(run_final_statements(agents, PROPOSAL, discussion, gen, cfg))
    final_prompts = [s for s in gen_calls.calls if "final position" in s]
    assert len(final_prompts) == 3  # everyone, CE included
    assert PROPOSAL in final_prompts[0]
    assert all("Position:" in a.final_statement for a in agents)


# --- extraction ------------------------------------------------------------


def test_extract_position_parses_probabilities(cfg: SimulationConfig):
    from synthetic_council.simulation.extraction import extract_position

    ext = asyncio.run(extract_position("stmt", "memo", cfg))
    assert ext.position == "raise"
    assert ext.probabilities == pytest.approx({"raise": 0.9, "hold": 0.1, "lower": 0.0})
    # conviction expectation of {"0":0.1,"1":0.2,"2":0.7} = 1.6
    assert ext.conviction == pytest.approx(1.6)
    assert ext.margin == pytest.approx(0.8)
    assert ext.input_tokens == 12 and ext.output_tokens == 0  # decisions protocol usage


# --- decision / alignment / mode -------------------------------------------


def test_real_decision_text_change_and_hold(cfg: SimulationConfig):
    text = real_decision_text(MEETING, cfg)
    assert "raise" in text and "100 basis points" in text  # -0.50 -> 0.50
    assert "MRO 0.5%" in text


def test_run_meeting_end_to_end(cfg: SimulationConfig):
    payload = asyncio.run(run_meeting(MEETING, cfg))

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


def test_run_meeting_writes_archive_and_settings(cfg: SimulationConfig):
    asyncio.run(run_meeting(MEETING, cfg))
    out = cfg.out_root / "ea_only" / MEETING
    payload = json.loads((out / "payload.json").read_text())
    assert payload["meeting_date"] == MEETING

    # settings block: models, sampling params, config hash recorded
    settings = payload["settings"]
    assert settings["inference"]["roles"]["generator"]["model"] == "zai-org/GLM-5.3-Flash"
    assert settings["inference"]["roles"]["extractor"]["model"] == "cloudflare/clef"
    assert settings["inference"]["config_sha256"]
    assert settings["simulation"]["n_rounds"] == 2
    assert settings["prompt_versions"]["decision_mode"] == "dm-v1"

    messages = [json.loads(line) for line in (out / "messages.jsonl").read_text().splitlines()]
    stages = {(m.get("context") or {}).get("stage") or m.get("stage") for m in messages}
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
    # full-fidelity ledger: every event carries model identity and inputs
    for m in messages:
        assert m["model"] and m["endpoint"]
        assert m.get("system") or m.get("state")  # full input present
    # generator events record the actual role model, not a stale default
    gen_events = [m for m in messages if m["kind"] == "chat"]
    assert all(m["model"] == "zai-org/GLM-5.3-Flash" for m in gen_events)
    choice_events = [m for m in messages if m["kind"] == "choice"]
    assert all(m["model"] == "cloudflare/clef" for m in choice_events)
    assert all("probabilities" in m or "content" in m for m in messages)

    transcript = (out / "transcript.md").read_text()
    assert "Proposal — Philip R. Lane" in transcript
    assert "Chief Economist's reply" in transcript
    assert "## Decision — Christine Lagarde" in transcript


def test_deterministic_mode_majority_when_split(cfg: SimulationConfig):
    # all agents non-aligned (0.2) -> share 0.0 < 0.7 -> majority
    STATE["choice"] = "non_aligned"
    try:
        payload = asyncio.run(run_meeting(MEETING, cfg))
        assert payload["mode"]["deterministic_label"] == "majority"
    finally:
        STATE["choice"] = "aligned"
