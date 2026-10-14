"""Message ledger: every LLM call's full inputs and outputs are logged.

One ``messages.jsonl`` per meeting — each line a complete ``CallEvent``
(call id, timestamp, kind, role, endpoint, model, sampling params, full
prompts/inputs, full outputs, token usage, latency, stage/agent/round
context) — plus a human-readable ``transcript.md``. Runs stay inspectable,
and any call can be replayed verbatim or reused as the input of a branched
experiment (see ``simulation/replay.py``).

Attached to ``SimulationConfig.recorder`` by the runner; generators emit
events through their ``on_event`` callback and choice/chat call sites pass
``on_event`` — all funnel into ``record_event``.
"""

from __future__ import annotations

import datetime as _dt
import json
from dataclasses import dataclass, field
from pathlib import Path

from synthetic_council.inference import CallEvent


@dataclass
class Recorder:
    out_dir: Path
    meta: dict = field(default_factory=dict)
    _events: list[dict] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.out_dir.mkdir(parents=True, exist_ok=True)

    def record_event(self, event: CallEvent, **context: object) -> None:
        """Archive one complete LLM call (the ledger currency)."""
        d = event.to_dict()
        d["context"] = {**(context or event.context), **self.meta}
        if "ts" not in d or not d["ts"]:
            d["ts"] = _dt.datetime.now(_dt.UTC).isoformat(timespec="seconds")
        self._events.append(d)

    # --- legacy shape kept for readers of older archives -------------------

    def record(
        self,
        stage: str,
        system: str,
        user: str,
        response: str,
        model: str,
        agent: str | None = None,
        round_no: int | None = None,
        usage: dict | None = None,
    ) -> None:
        self._events.append(
            {
                "ts": _dt.datetime.now(_dt.UTC).isoformat(timespec="seconds"),
                "kind": "generator",
                "stage": stage,
                "agent": agent,
                "round": round_no,
                "model": model,
                "system": system,
                "user": user,
                "response": response,
                "usage": usage or {},
                **self.meta,
            }
        )

    def record_jev(
        self,
        stage: str,
        state: dict,
        criteria: dict,
        outcome: str,
        probabilities: dict,
        model: str,
        agent: str | None = None,
    ) -> None:
        self._events.append(
            {
                "ts": _dt.datetime.now(_dt.UTC).isoformat(timespec="seconds"),
                "kind": "jev",
                "stage": stage,
                "agent": agent,
                "model": model,
                "state": state,
                "criteria": criteria,
                "outcome": outcome,
                "probabilities": probabilities,
                **self.meta,
            }
        )

    def flush(self) -> Path:
        path = self.out_dir / "messages.jsonl"
        with path.open("a", encoding="utf-8") as fh:
            for e in self._events:
                fh.write(json.dumps(e, ensure_ascii=False) + "\n")
        self._events = []
        return path

    def write_transcript(self, blocks: list[str]) -> Path:
        path = self.out_dir / "transcript.md"
        path.write_text("\n\n".join(blocks) + "\n", encoding="utf-8")
        return path
