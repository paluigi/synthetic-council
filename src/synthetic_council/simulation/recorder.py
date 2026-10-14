"""Message archive: every generator and Jev request/response is logged.

One ``messages.jsonl`` per meeting (one JSON object per message: stage,
agent, round, prompts, response, model, token usage, timestamp) plus a
human-readable ``transcript.md`` — so runs stay inspectable and can be
re-analysed or reused as inputs to other experiments without re-running.

Attached to ``SimulationConfig.recorder`` by the runner; all simulation
modules call ``record``/``record_jev`` when one is present.
"""

from __future__ import annotations

import datetime as _dt
import json
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Recorder:
    out_dir: Path
    meta: dict = field(default_factory=dict)
    _events: list[dict] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.out_dir.mkdir(parents=True, exist_ok=True)

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
