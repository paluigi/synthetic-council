"""Tests for the simulation package.

API-touching backends are faked: these tests verify orchestration, prompts,
extraction parsing, deltas and consensus — not the gateway.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from synthetic_council.simulation.agent import load_agents
from synthetic_council.simulation.config import SimulationConfig
from synthetic_council.simulation.discussion import run_discussion, run_final_statements
from synthetic_council.simulation.extraction import extract_position
from synthetic_council.simulation.runner import meeting_memos, run_meeting

MEMO = """# Briefing memo — Test Person

**Role:** Governor, National Central Bank of Testland
**Country:** Testland
**Meeting:** GC monetary policy meeting, 2022-07-21
**Current rates:** MRO 0.5% · DFR 0.0% · MLF 0.75%

## Euro area economic situation (data available at the meeting)

| Indicator | Reference | Value |
|---|---|---|
| HICP inflation (y/y %) | 2022-06 | 8.64 |
"""


@pytest.fixture
def memos_dir(tmp_path: Path) -> Path:
    d = tmp_path / "memos"
    d.mkdir()
    (d / "2022-07-21_Test_Person.md").write_text(MEMO, encoding="utf-8")
    (d / "2022-07-21_Another_Member.md").write_text(
        MEMO.replace("Test Person", "Another Member")
        .replace("Testland", "Elsewhere")
        .replace("**Country:** Elsewhere\n", ""),
        encoding="utf-8",
    )
    (d / "2022-09-08_Test_Person.md").write_text(MEMO, encoding="utf-8")
    return d


class FakeGenerator:
    """Returns deterministic statements; records prompts."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    async def generate(self, system: str, user: str) -> str:
        self.calls.append((system, user))
        if "opening position statement" in system:
            return (
                "Inflation is far above target. "
                "Position: raise the rate"
            )
        if "speak once more" in system:
            return "I hear my colleagues; I stay with my view. Position: raise the rate"
        return "Having weighed the discussion, I remain convinced. Position: hold the rate"

    async def aclose(self) -> None:
        return None


def fake_jev_item(position: str = "raise", conviction: float = 1.5):
    """Build a minimal fake of the `ai` experimental_evaluate item."""

    class Ans:
        def __init__(self, **kw):
            self.__dict__.update(kw)

    class Value:
        answers = {
            "position": Ans(
                choice=position, probabilities={"raise": 0.9, "hold": 0.1, "lower": 0.0}
            ),
            "conviction": Ans(score=conviction),
        }

    class Item:
        value = Value()
        provider_metadata = {"typesafe": {"confidence": {"position": 1}}}
        usage = None

        def model_dump(self, mode="json"):
            return {"fake": True}

    return Item()


def patch_jev(monkeypatch, position="raise", conviction=1.5):
    async def fake_experimental_evaluate(*a, **kw):
        return fake_jev_item(position, conviction)

    def fake_get_model(_):
        return object()

    import synthetic_council.simulation.extraction as ex

    monkeypatch.setattr(ex, "experimental_evaluate", fake_experimental_evaluate, raising=False)
    monkeypatch.setattr(ex, "get_model", fake_get_model, raising=False)
    # the function imports inside its body; patch the source modules too
    import ai
    import ai.ops

    monkeypatch.setattr(ai, "get_model", fake_get_model)
    monkeypatch.setattr(ai.ops, "experimental_evaluate", fake_experimental_evaluate)


# --- memos & agents -------------------------------------------------------


def test_meeting_memos_filters_by_date(memos_dir: Path):
    paths = meeting_memos(memos_dir, "2022-07-21")
    assert [p.name for p in paths] == [
        "2022-07-21_Another_Member.md",
        "2022-07-21_Test_Person.md",
    ]
    with pytest.raises(FileNotFoundError):
        meeting_memos(memos_dir, "1999-01-01")


def test_load_agents_parses_header(memos_dir: Path):
    agents = load_agents(meeting_memos(memos_dir, "2022-07-21"))
    by_name = {a.person: a for a in agents}
    assert by_name["Test Person"].country == "Testland"
    assert by_name["Test Person"].role.startswith("Governor")
    assert by_name["Test Person"].role_line == (
        "Governor of the national central bank of Testland"
    )
    # no country line -> executive member, role_line falls back to the role
    assert by_name["Another Member"].country is None
    assert by_name["Another Member"].role_line.startswith("Governor")


def test_agent_initial_prompt_contains_memo_and_positions(memos_dir: Path):
    cfg = SimulationConfig()
    agent = load_agents(meeting_memos(memos_dir, "2022-07-21"))[1]
    system, user = agent.initial_prompt(cfg)
    assert "Test Person" in system
    assert "Position: raise the rate" in system  # decision-last instruction
    assert "8.64" in user  # memo table included


# --- extraction -----------------------------------------------------------


def test_extract_position_parses_probabilities_conviction_and_margin(monkeypatch):
    patch_jev(monkeypatch, position="raise", conviction=1.25)
    ext = extract_position("stmt", "memo", SimulationConfig())
    import asyncio

    ext = asyncio.run(ext)
    assert ext.position == "raise"
    assert ext.probabilities == {"raise": 0.9, "hold": 0.1, "lower": 0.0}
    assert ext.conviction == 1.25
    assert ext.typesafe_confidence == {"position": 1}
    assert ext.margin == pytest.approx(0.8)


def test_extract_positions_parallel_order(monkeypatch):
    patch_jev(monkeypatch)
    import asyncio

    from synthetic_council.simulation.extraction import extract_positions

    exts = asyncio.run(extract_positions([("s1", "m"), ("s2", "m")], SimulationConfig()))
    assert len(exts) == 2
    assert all(e.position == "raise" for e in exts)


# --- discussion -----------------------------------------------------------


def test_run_discussion_two_rounds_all_agents_speak(memos_dir: Path):
    cfg = SimulationConfig(n_rounds=2)
    agents = load_agents(meeting_memos(memos_dir, "2022-07-21"))
    gen = FakeGenerator()

    result = run_discussion(agents, gen, cfg)
    import asyncio

    result = asyncio.run(result)
    assert len(result.turns) == 4  # 2 agents x 2 rounds
    assert [t.round_no for t in result.turns] == [1, 1, 2, 2]

    # round-2 prompts must embed the round-1 transcript
    round2_prompts = [s for s, _ in gen.calls if "speak once more" in s]
    assert len(round2_prompts) == 2
    assert "Test Person" in round2_prompts[0]
    assert "[Round 1]" in round2_prompts[0]


def test_run_discussion_requires_two_rounds(memos_dir: Path):
    agents = load_agents(meeting_memos(memos_dir, "2022-07-21"))
    import asyncio

    with pytest.raises(ValueError):
        asyncio.run(run_discussion(agents, FakeGenerator(), SimulationConfig(n_rounds=1)))


def test_final_statements_see_transcript(memos_dir: Path):
    cfg = SimulationConfig(n_rounds=2)
    agents = load_agents(meeting_memos(memos_dir, "2022-07-21"))
    gen = FakeGenerator()
    import asyncio

    discussion = asyncio.run(run_discussion(agents, gen, cfg))
    asyncio.run(run_final_statements(agents, discussion, gen, cfg))
    assert all("weighed the discussion" in a.final_statement for a in agents)
    final_prompts = [s for s, _ in gen.calls if "final position" in s]
    assert len(final_prompts) == 2
    assert "[Round 2]" in final_prompts[0]


# --- full meeting (faked backends) ----------------------------------------


def test_run_meeting_end_to_end(memos_dir: Path, monkeypatch):
    patch_jev(monkeypatch, position="raise", conviction=1.5)
    cfg = SimulationConfig(memos_dir=memos_dir, n_rounds=2)
    payload = run_meeting("2022-07-21", cfg, generator=FakeGenerator())
    import asyncio

    payload = asyncio.run(payload)

    assert payload["n_agents"] == 2
    assert payload["n_rounds"] == 2
    assert payload["consensus"]["before"] == {"raise": 2, "hold": 0, "lower": 0}
    # finals say "hold" -> the fake Jev still answers "raise"; positions unchanged
    assert payload["consensus"]["n_changed"] == 0
    for a in payload["agents"]:
        assert a["deltas"]["position_changed"] is False
        assert a["deltas"]["probabilities_before"]["raise"] == 0.9
        assert a["initial_statement"] and a["final_statement"]
    assert len(payload["turns"]) == 4


def test_payload_is_json_serialisable(memos_dir: Path, monkeypatch, tmp_path: Path):
    patch_jev(monkeypatch)
    cfg = SimulationConfig(memos_dir=memos_dir, n_rounds=2)
    import asyncio

    payload = asyncio.run(run_meeting("2022-07-21", cfg, generator=FakeGenerator()))
    out = tmp_path / "sim.json"
    out.write_text(json.dumps(payload, indent=2, ensure_ascii=False))
    assert json.loads(out.read_text())["meeting_date"] == "2022-07-21"
