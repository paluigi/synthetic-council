"""Tests for the provider-agnostic inference layer (no network).

Endpoints are faked with httpx.MockTransport injected into the
InferenceConfig — plain parameter injection, no patching.
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

import httpx
import pytest

from synthetic_council.inference import (
    CallEvent,
    Client,
    InferenceConfig,
    JevChoice,
    _parse_distribution,
    choice,
    jsonl_sink,
    load_config,
)

TOML = """
[endpoints.local]
base_url = "https://fake.local/v1"
api_key_env = "FAKE_KEY_ENV"

[endpoints.other]
base_url = "https://other.local/v1"
api_key_env = "OTHER_KEY_ENV"

[roles.generator]
endpoint = "local"
model = "vendor/gen-model"
temperature = 0.5

[roles.extractor]
endpoint = "other"
model = "vendor/judge-model"
temperature = 0.0
"""


def _config(tmp_path: Path, toml: str = TOML) -> InferenceConfig:
    p = tmp_path / "inference.toml"
    p.write_text(toml)
    return load_config(p)


class TestLoader:
    def test_roles_and_endpoints(self, tmp_path):
        cfg = _config(tmp_path)
        assert cfg.role("generator").model == "vendor/gen-model"
        assert cfg.role("generator").model_id == "local/vendor/gen-model"
        assert cfg.role("extractor").endpoint == "other"
        assert cfg.role("generator").params == {"temperature": 0.5}

    def test_settings_block_is_diffable(self, tmp_path):
        s = _config(tmp_path).settings()
        assert s["roles"]["generator"] == {
            "endpoint": "local",
            "model": "vendor/gen-model",
            "api": "chat",
            "temperature": 0.5,
        }
        assert s["config_sha256"]

    def test_unknown_endpoint_raises(self, tmp_path):
        bad = TOML.replace('endpoint = "local"', 'endpoint = "nope"')
        with pytest.raises(ValueError, match="unknown endpoint"):
            _config(tmp_path, bad)

    def test_unknown_sampling_param_raises(self, tmp_path):
        bad = TOML + "\n[roles.extra]\nendpoint = 'local'\nmodel = 'm'\ntop_k = 5\n"
        with pytest.raises(ValueError, match="unknown sampling params"):
            _config(tmp_path, bad)

    def test_missing_role_key_detected(self, tmp_path):
        cfg = _config(tmp_path)
        os.environ.pop("FAKE_KEY_ENV", None)
        os.environ.pop("OTHER_KEY_ENV", None)
        missing = cfg.check_keys()
        assert "FAKE_KEY_ENV (endpoint 'local')" in missing
        assert "OTHER_KEY_ENV (endpoint 'other')" in missing


class TestParseDistribution:
    CRITERIA = {"raise": "r", "hold": "h", "lower": "l"}

    def test_clean_json(self):
        d = _parse_distribution('{"raise": 0.6, "hold": 0.3, "lower": 0.1}', self.CRITERIA)
        assert d == pytest.approx({"raise": 0.6, "hold": 0.3, "lower": 0.1})

    def test_fenced_json(self):
        d = _parse_distribution('```json\n{"raise": 2, "hold": 1, "lower": 1}\n```', self.CRITERIA)
        assert d == pytest.approx({"raise": 0.5, "hold": 0.25, "lower": 0.25})

    def test_surrounding_prose(self):
        d = _parse_distribution(
            'The answer: {"raise": 0.9, "hold": 0.1, "lower": 0} done', self.CRITERIA
        )
        assert d is not None and d["raise"] == pytest.approx(0.9)

    def test_keys_normalised_case(self):
        d = _parse_distribution('{"Raise": 1, "HOLD": 1, "lower": 0}', self.CRITERIA)
        assert d is not None and set(d) == set(self.CRITERIA)

    def test_missing_key_rejected(self):
        assert _parse_distribution('{"raise": 1, "hold": 1}', self.CRITERIA) is None

    def test_extra_key_ignored(self):
        # unknown keys are skipped (chatty models), the criteria still parse
        d = _parse_distribution('{"raise": 1, "hold": 1, "lower": 1, "other": 1}', self.CRITERIA)
        assert d is not None and set(d) == set(self.CRITERIA)

    def test_non_json_rejected(self):
        assert _parse_distribution("hold, definitely", self.CRITERIA) is None

    def test_normalisation_to_one(self):
        d = _parse_distribution('{"raise": 30, "hold": 10, "lower": 10}', self.CRITERIA)
        assert sum(d.values()) == pytest.approx(1.0)


def _mock_client(cfg: InferenceConfig, responder) -> Client:
    cfg.transport = httpx.MockTransport(responder)
    return Client(cfg)


class TestChoice:
    def _responder(self, content: str):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "choices": [{"message": {"content": content}}],
                    "usage": {"prompt_tokens": 7, "completion_tokens": 3},
                },
            )

        return handler

    def test_outcome_probabilities_and_event(self, tmp_path):
        events: list[CallEvent] = []
        client = _mock_client(
            _config(tmp_path), self._responder('{"aligned": 0.8, "non_aligned": 0.2}')
        )
        res = asyncio.run(
            choice(
                client,
                "extractor",
                state={"x": 1},
                criteria={"aligned": "a", "non_aligned": "n"},
                instructions="decide",
                on_event=events.append,
            )
        )
        assert isinstance(res, JevChoice)
        assert res.outcome == "aligned"
        assert res.probabilities == pytest.approx({"aligned": 0.8, "non_aligned": 0.2})
        assert res.margin == pytest.approx(0.6)
        assert res.input_tokens == 7 and res.output_tokens == 5 or res.output_tokens == 3
        (event,) = events
        assert event.kind == "choice" and event.model == "vendor/judge-model"
        d = event.to_dict()
        assert d["call_id"] and d["ts"]
        assert d["prompt"] and d["state"] == {"x": 1}
        assert d["probabilities"] == res.probabilities

    def test_retry_then_success_on_garbage(self, tmp_path):
        outputs = iter(["no json here", "maybe {oops}", '{"raise": 1, "hold": 0, "lower": 0}'])

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"choices": [{"message": {"content": next(outputs)}}]})

        client = _mock_client(_config(tmp_path), handler)
        res = asyncio.run(
            choice(
                client,
                "extractor",
                state={},
                criteria={"raise": "r", "hold": "h", "lower": "l"},
                instructions="i",
            )
        )
        assert res.outcome == "raise"

    def test_all_garbage_raises(self, tmp_path):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"choices": [{"message": {"content": "not json"}}]})

        client = _mock_client(_config_with_retries(tmp_path, 1), handler)
        with pytest.raises(RuntimeError, match="no valid distribution"):
            asyncio.run(
                choice(
                    client,
                    "extractor",
                    state={},
                    criteria={"a": "x", "b": "y"},
                    instructions="i",
                )
            )

    def test_settings_travel(self, tmp_path):
        cfg = _config(tmp_path)
        s = cfg.settings()
        assert s["roles"]["extractor"]["temperature"] == 0.0


def _config_with_retries(tmp_path: Path, retries: int) -> InferenceConfig:
    toml = TOML.replace("temperature = 0.0", f"temperature = 0.0\nretries = {retries}")
    p = tmp_path / "inference.toml"
    p.write_text(toml)
    return load_config(p)


class TestJsonlSink:
    def test_appends_events(self, tmp_path):
        sink = jsonl_sink(tmp_path / "llm_calls" / "ctx.jsonl")
        event = CallEvent(
            kind="chat",
            role="generator",
            endpoint="local",
            model="vendor/gen-model",
            params={"temperature": 0.5},
            system="s",
            user="u",
            content="c",
        )
        sink(event)
        sink(event)
        lines = (tmp_path / "llm_calls" / "ctx.jsonl").read_text().splitlines()
        assert len(lines) == 2
        parsed = json.loads(lines[0])
        assert parsed["model"] == "vendor/gen-model" and parsed["system"] == "s"


class TestDiffSettings:
    def test_leaf_deltas(self):
        from synthetic_council.analysis.compare_arms import diff_settings

        a = {"inference": {"roles": {"extractor": {"model": "clef", "temperature": 0.0}}}}
        b = {"inference": {"roles": {"extractor": {"model": "jev", "temperature": 0.3}}}}
        deltas = diff_settings(a, b)
        assert "inference.roles.extractor.model: 'clef' -> 'jev'" in deltas
        assert "inference.roles.extractor.temperature: 0.0 -> 0.3" in deltas


class TestReplayBranch:
    def test_replay_remeasures_without_generator_calls(self, tmp_path):
        """A variant payload is produced from judge calls only: the mock
        transport fails any generator-style prompt, proving statements are
        reused verbatim from the source payload."""
        import asyncio

        from synthetic_council.simulation.config import SimulationConfig
        from synthetic_council.simulation.replay import replay_meeting

        payload_dir = tmp_path / "ea_only" / "2022-07-21"
        payload_dir.mkdir(parents=True)
        (payload_dir / "payload.json").write_text(
            json.dumps(
                {
                    "meeting_date": "2022-07-21",
                    "arm": "ea_only",
                    "ce_proposal": {"text": "raise by 50bp"},
                    "agents": [
                        {
                            "person": "A",
                            "role": "Governor",
                            "country": "X",
                            "voting_right": True,
                            "is_chief_economist": False,
                            "memo": "",
                            "initial_statement": "I favour raising. Position: raise",
                            "final_statement": "Still raising. Position: raise",
                        }
                    ],
                    "decision": {
                        "president": "P",
                        "statement": "Decision: raise the rate",
                        "stance": "raise",
                        "modal_stance_voters": "raise",
                        "voter_counts": {"raise": 1},
                        "agrees_with_modal": True,
                    },
                }
            )
        )
        cfg = SimulationConfig(arm="ea_only", out_root=tmp_path)

        def handler(request: httpx.Request) -> httpx.Response:
            url = str(request.url)
            assert "alpha/decisions" in url, "generator call during replay!"
            body = json.loads(request.content)
            questions = body.get("questions", {})
            if "conviction" in questions:  # position extraction
                answers = {
                    "position": {
                        "type": "choice",
                        "choice": "raise",
                        "probabilities": {"raise": 0.8, "hold": 0.2, "lower": 0.0},
                    },
                    "conviction": {
                        "type": "score",
                        "score": 1.3,
                        "probabilities": {"0": 0.2, "1": 0.3, "2": 0.5},
                    },
                }
            elif "reference_decision" in body.get("state", {}):
                answers = {
                    "choice": {
                        "type": "choice",
                        "choice": "aligned",
                        "probabilities": {"aligned": 0.9, "non_aligned": 0.1},
                    }
                }
            else:
                answers = {
                    "choice": {
                        "type": "choice",
                        "choice": "unanimity",
                        "probabilities": {"unanimity": 0.7, "consensus": 0.2, "majority": 0.1},
                    }
                }
            return httpx.Response(
                200,
                json={"answers": answers, "usage": {"input_tokens": 5, "output_tokens": 0}},
            )

        cfg.inference.transport = httpx.MockTransport(handler)
        out = asyncio.run(replay_meeting(payload_dir / "payload.json", cfg))
        variant = json.loads(out.read_text())
        assert variant["replay_of"].endswith("payload.json")
        assert variant["settings"]["inference"]["roles"]["extractor"]["model"]
        a = variant["agents"][0]
        assert a["initial_statement"].startswith("I favour raising")  # reused verbatim
        assert a["extraction_final"]["position"] == "raise"
        ledger = (payload_dir / "messages.jsonl").read_text().splitlines()
        events = [json.loads(line) for line in ledger]
        assert events and all(e["kind"] == "choice" for e in events)
        assert {e["context"]["stage"] for e in events} >= {
            "position-extraction",
            "decision-mode",
        }


class TestChatRobustness:
    def test_retries_when_choices_missing(self, tmp_path, monkeypatch):
        """A 200 body without a choices array (endpoint error body) must be
        retried like empty content, not crash the caller with KeyError."""

        async def _no_sleep(_s):
            return None

        monkeypatch.setattr("synthetic_council.inference.asyncio.sleep", _no_sleep)
        outputs = iter(
            [
                {"error": {"message": "overloaded"}},
                {"choices": [{"message": {"content": "hello there"}}]},
            ]
        )
        calls = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            return httpx.Response(200, json=next(outputs))

        client = _mock_client(_config(tmp_path), handler)
        res = asyncio.run(client.chat(role_name="generator", system="s", user="u"))
        assert res.content == "hello there"
        assert calls["n"] == 2
