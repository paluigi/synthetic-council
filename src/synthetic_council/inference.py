"""Provider-agnostic LLM inference: config, clients, decision measurement.

All model/provider choices live in ``inference.toml`` (repo root) —
endpoints (base_url + ``api_key_env`` pointing into .env.local) and roles
(generator / extractor) with sampling parameters. Swapping a provider or
model is a config edit, never a code change. Keys are never stored in the
file or the repo.

Every call — chat and decision — goes through this module and produces a
structured ``CallEvent`` (full inputs, full outputs, model identity,
sampling params, usage, latency) that callers archive: the per-meeting
``messages.jsonl`` ledger for simulation runs and per-context JSONL sinks
for utility calls. Any archived call can be replayed verbatim, so future
experiments can branch off any step of a current one.

Measurement (the former Jev role) supports two API shapes, chosen per role
in the config:

- ``api = "decisions"`` — the endpoint's native Jev-style decisions API
  (OpenRouter's ``/api/alpha/decisions`` for Cloudflare's Clef): typed
  questions (``choice`` over criteria with full probability distributions,
  ``score`` over a rubric with a legend and fractional score) and
  per-answer confidence, all in one request.
- ``api = "chat"`` (default) — any OpenAI-compatible chat model emulates
  the same measurement with strict-JSON prompted distributions
  (``choice-v1`` protocol).

Both paths return the same ``JevChoice``/answers shapes, so consumers are
agnostic to the backend.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
import tomllib
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

from synthetic_council.config import PACKAGE_ROOT

CONFIG_PATH = PACKAGE_ROOT / "inference.toml"
CHOICE_VERSION = "choice-v1"
DEFAULT_RETRIES = 3

# sampling parameter names allowed on roles (flow to the API + archives)
_SAMPLING_PARAMS = ("temperature", "top_p", "max_tokens", "retries")


@dataclass
class Endpoint:
    name: str
    base_url: str
    api_key_env: str
    decisions_url: str = ""  # native Jev-style decisions API, when offered


@dataclass
class Role:
    name: str
    endpoint: str
    model: str
    api: str = "chat"  # "chat" | "decisions"
    params: dict[str, Any] = field(default_factory=dict)

    @property
    def model_id(self) -> str:
        """Full provenance id, e.g. 'openrouter/cloudflare/clef'."""
        return f"{self.endpoint}/{self.model}"

    def settings(self) -> dict[str, Any]:
        return {
            "endpoint": self.endpoint,
            "model": self.model,
            "api": self.api,
            **self.params,
        }


@dataclass
class InferenceConfig:
    endpoints: dict[str, Endpoint]
    roles: dict[str, Role]
    config_sha256: str = ""
    # optional httpx transport (tests inject MockTransport; production None)
    transport: Any = field(default=None, repr=False, compare=False)

    def role(self, name: str) -> Role:
        try:
            return self.roles[name]
        except KeyError:
            raise KeyError(
                f"inference role '{name}' not defined in {CONFIG_PATH}"
                f" (available: {sorted(self.roles)})"
            ) from None

    def settings(self) -> dict[str, Any]:
        """Everything that determines inference behaviour, one diffable
        block — written into every experiment payload."""
        return {
            "roles": {k: r.settings() for k, r in sorted(self.roles.items())},
            "choice_version": CHOICE_VERSION,
            "config_sha256": self.config_sha256,
        }

    def check_keys(self) -> list[str]:
        """Names of referenced endpoints whose api key env is missing."""
        import os

        missing = []
        for role in self.roles.values():
            ep = self.endpoints.get(role.endpoint)
            if ep is None:
                missing.append(f"role '{role.name}' -> unknown endpoint '{role.endpoint}'")
            elif not os.environ.get(ep.api_key_env, ""):
                missing.append(f"{ep.api_key_env} (endpoint '{role.endpoint}')")
        return missing


def load_config(path: Path | str | None = None) -> InferenceConfig:
    path = Path(path) if path is not None else CONFIG_PATH
    raw = path.read_bytes()
    data = tomllib.loads(raw.decode("utf-8"))
    endpoints = {
        name: Endpoint(name, ep["base_url"], ep["api_key_env"], ep.get("decisions_url", ""))
        for name, ep in data.get("endpoints", {}).items()
    }
    roles: dict[str, Role] = {}
    for name, r in data.get("roles", {}).items():
        params = {k: v for k, v in r.items() if k not in ("endpoint", "model", "api")}
        unknown = [k for k in params if k not in _SAMPLING_PARAMS]
        if unknown:
            raise ValueError(f"role '{name}': unknown sampling params {unknown}")
        api = r.get("api", "chat")
        if api not in ("chat", "decisions"):
            raise ValueError(f"role '{name}': api must be 'chat' or 'decisions', got {api!r}")
        roles[name] = Role(name, r["endpoint"], r["model"], api, params)
    cfg = InferenceConfig(
        endpoints=endpoints,
        roles=roles,
        config_sha256=hashlib.sha256(raw).hexdigest(),
    )
    for role in roles.values():
        if role.endpoint not in endpoints:
            raise ValueError(
                f"inference.toml: role '{name}' references unknown endpoint "
                f"'{role.endpoint}' (defined: {sorted(endpoints)})"
            )
        if role.api == "decisions" and not endpoints[role.endpoint].decisions_url:
            raise ValueError(
                f"inference.toml: role '{name}' uses api='decisions' but endpoint "
                f"'{role.endpoint}' defines no decisions_url"
            )
    return cfg


# --- call events (the archive currency) ---------------------------------------


@dataclass
class CallEvent:
    """One complete LLM call: everything needed to replay or branch."""

    kind: str  # "chat" | "choice"
    role: str
    endpoint: str
    model: str
    params: dict[str, Any]
    system: str = ""
    user: str = ""
    state: dict[str, Any] = field(default_factory=dict)
    criteria: dict[str, str] = field(default_factory=dict)
    instructions: str = ""
    prompt: str = ""
    content: str = ""
    outcome: str = ""
    probabilities: dict[str, float] = field(default_factory=dict)
    questions: dict[str, Any] = field(default_factory=dict)
    answers: dict[str, Any] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)
    usage: dict[str, int] = field(default_factory=dict)
    latency_ms: float = 0.0
    context: dict[str, Any] = field(default_factory=dict)  # stage/agent/round/...

    def to_dict(self) -> dict[str, Any]:
        return {
            "call_id": uuid.uuid4().hex[:12],
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            **{k: v for k, v in self.__dict__.items()},
        }


@dataclass
class JevChoice:
    """A measured choice — name kept from the former Jev module so
    consumers and legacy payloads stay readable."""

    outcome: str
    probabilities: dict[str, float] = field(default_factory=dict)
    input_tokens: int | None = None
    output_tokens: int | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def margin(self) -> float | None:
        if not self.probabilities:
            return None
        p = sorted(self.probabilities.values(), reverse=True)
        return p[0] - p[1] if len(p) > 1 else p[0]


@dataclass
class ChatResult:
    content: str
    model: str
    endpoint: str
    params: dict[str, Any]
    usage: dict[str, int]
    latency_ms: float
    raw: dict[str, Any]


class Client:
    """OpenAI-compatible chat + native-decisions client per config."""

    def __init__(self, config: InferenceConfig):
        self.config = config
        self._clients: dict[str, httpx.AsyncClient] = {}

    def _http(self, endpoint: Endpoint) -> httpx.AsyncClient:
        if endpoint.name not in self._clients:
            import os

            key = os.environ.get(endpoint.api_key_env, "")
            self._clients[endpoint.name] = httpx.AsyncClient(
                base_url=endpoint.base_url,
                headers={
                    "Authorization": f"Bearer {key}",
                    "Content-Type": "application/json",
                },
                timeout=180,
                transport=self.config.transport,
            )
        return self._clients[endpoint.name]

    async def _post_with_retries(
        self, endpoint: Endpoint, url: str, body: dict[str, Any], retries: int
    ) -> dict[str, Any]:
        import copy

        payload = copy.deepcopy(body)  # retries must not mutate the caller's body
        t0 = time.monotonic()
        last_exc: Exception | None = None
        for attempt in range(retries):
            try:
                r = await self._http(endpoint).post(url, json=payload)
                r.raise_for_status()
                data = r.json()
                data["_latency_ms"] = (time.monotonic() - t0) * 1000
                return data
            except (httpx.HTTPError, ValueError) as exc:
                last_exc = exc
                await asyncio.sleep(2**attempt * 2)
        raise RuntimeError(f"call to {url} failed after {retries} attempts: {last_exc}") from (
            last_exc
        )

    async def chat(
        self,
        role_name: str,
        system: str,
        user: str,
        temperature: float | None = None,
    ) -> ChatResult:
        role = self.config.role(role_name)
        endpoint = self.config.endpoints[role.endpoint]
        params = dict(role.params)
        if temperature is not None:
            params["temperature"] = temperature
        body: dict[str, Any] = {
            "model": role.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        body.update({k: v for k, v in params.items() if k != "retries"})
        data = await self._post_with_retries(
            endpoint,
            "/chat/completions",
            body,
            int(params.get("retries", DEFAULT_RETRIES)),
        )
        # a stressed endpoint can return 200 with no content field (or empty
        # content) — retry those instead of propagating an empty generation
        empty_retries = int(params.get("empty_content_retries", 3))
        latency = 0.0
        content = ""
        for i in range(empty_retries + 1):
            data = await self._post_with_retries(
                endpoint,
                "/chat/completions",
                body,
                int(params.get("retries", DEFAULT_RETRIES)),
            )
            latency += data.pop("_latency_ms")
            content = ((data["choices"][0]["message"].get("content")) or "").strip()
            if content or i == empty_retries:
                break
            await asyncio.sleep(5 * 2**i)
        u = data.get("usage", {}) or {}
        # native input/output tokens win; OpenAI-style prompt/completion only
        # as fallback for providers that report just those
        inp = u.get("input_tokens")
        if inp is None:
            inp = u.get("prompt_tokens")
        out_tok = u.get("output_tokens")
        if out_tok is None:
            out_tok = u.get("completion_tokens")
        usage = {
            k: v
            for k, v in (("input_tokens", inp), ("output_tokens", out_tok))
            if v is not None
        }
        return ChatResult(
            content=content,
            model=role.model,
            endpoint=role.endpoint,
            params=params,
            usage=usage,
            latency_ms=latency,
            raw=data,
        )

    async def decide(
        self, role_name: str, state: dict[str, Any], questions: dict[str, Any]
    ) -> tuple[dict[str, Any], dict[str, int], dict[str, Any]]:
        """Native decisions API (Jev-style): typed questions in one request.

        ``questions`` maps a name to
        ``{"type": "choice", "instructions", "criteria": {key: description}}``
        or ``{"type": "score", "instructions", "criteria": [rubric...]}``.
        Returns (answers, usage, raw response).
        """
        role = self.config.role(role_name)
        endpoint = self.config.endpoints[role.endpoint]
        if role.api != "decisions" or not endpoint.decisions_url:
            raise ValueError(f"role '{role_name}' is not configured for a decisions API")
        body = {
            "model": role.model,
            "state": state,
            "questions": questions,
        }
        data = await self._post_with_retries(
            endpoint,
            endpoint.decisions_url,
            body,
            int(role.params.get("retries", DEFAULT_RETRIES)),
        )
        latency = data.pop("_latency_ms", 0.0)
        usage = {
            k: data.get("usage", {}).get(k)
            for k in ("input_tokens", "output_tokens")
            if data.get("usage", {}).get(k) is not None
        }
        if latency:
            data["latency_ms"] = latency
        return data.get("answers", {}), usage, data

    async def aclose(self) -> None:
        for c in self._clients.values():
            await c.aclose()
        self._clients.clear()


# --- measurement (the Jev replacement) -----------------------------------------

_CHOICE_SYSTEM = """You are a precise decision-measurement model. You judge a case described \
in JSON and assign a probability to each of the possible outcomes. The probabilities must be \
numbers between 0 and 1, must cover exactly the listed outcomes, and must sum to 1.

Respond with ONLY a JSON object mapping every outcome name to its probability. No prose, no \
code fences, no explanation. Example for outcomes ["a", "b"]: {"a": 0.72, "b": 0.28}."""


def _parse_distribution(text: str, criteria: dict[str, str]) -> dict[str, float] | None:
    """Extract and validate a probability distribution from model output.

    Tolerates code fences and stray prose around the JSON object. Unknown
    keys are skipped; the criteria must all be present. Values are clipped
    to >= 0 and normalised to sum to 1.
    """
    m = text.strip()
    if m.startswith("```"):
        m = m.strip("`")
        if m.lower().startswith("json"):
            m = m[4:]
        m = m.strip()
    start, end = m.find("{"), m.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        obj = json.loads(m[start : end + 1])
    except json.JSONDecodeError:
        return None
    if not isinstance(obj, dict):
        return None
    lookup = {k.lower(): k for k in criteria}
    probs: dict[str, float] = {}
    for key, value in obj.items():
        canonical = lookup.get(str(key).lower())
        if canonical is None or not isinstance(value, (int, float)):
            continue
        probs[canonical] = max(0.0, float(value))
    if set(probs) != set(criteria):
        return None
    total = sum(probs.values())
    if total <= 0:
        return None
    return {k: v / total for k, v in probs.items()}


async def decision(
    client: Client,
    role_name: str,
    state: dict[str, Any],
    questions: dict[str, Any],
    context: dict[str, Any] | None = None,
    on_event: Callable[[CallEvent], None] | None = None,
) -> tuple[dict[str, dict[str, Any]], dict[str, int]]:
    """Ask typed questions in one request; returns (answers, usage).

    Routes to the role's native decisions API when configured (api =
    "decisions"); otherwise emulates each question over chat with the
    choice-v1 prompted-JSON protocol. Answers share one shape either way:
    choice -> {type, choice, probabilities, confidence?}; score ->
    {type, score, probabilities, legend?}.
    """
    role = client.config.role(role_name)
    if role.api == "decisions":
        answers, usage, raw = await client.decide(role_name, state, questions)
        event = CallEvent(
            kind="choice",
            role=role_name,
            endpoint=role.endpoint,
            model=role.model,
            params=role.params | {"api": "decisions"},
            state=state,
            questions=questions,
            answers=answers,
            content=json.dumps(answers, ensure_ascii=False),
            raw={
                "endpoint": role.endpoint,
                "model": role.model,
                "api": "decisions",
                "response": raw,
            },
            usage=usage,
            latency_ms=raw.get("latency_ms", 0.0),
            context=context or {},
        )
        if on_event is not None:
            on_event(event)
        return answers, usage

    # chat emulation: one prompted call per question — each archived as its
    # own CallEvent (the native decisions branch archives one event for all
    # questions; parity matters for replaying/branching experiments)
    answers = {}
    usage: dict[str, int] = {}
    for name, q in questions.items():
        if q["type"] == "choice":
            res = await choice(client, role_name, state, q["criteria"], q["instructions"])
            answers[name] = {
                "type": "choice",
                "choice": res.outcome,
                "probabilities": res.probabilities,
            }
        else:  # score over a rubric list
            rubric = {str(i): d for i, d in enumerate(q["criteria"])}
            res = await choice(client, role_name, state, rubric, q["instructions"])
            answers[name] = {
                "type": "score",
                "score": sum(int(k) * v for k, v in res.probabilities.items()),
                "probabilities": res.probabilities,
                "legend": rubric,
            }
        q_usage = {"input_tokens": res.input_tokens, "output_tokens": res.output_tokens}
        usage = {
            "input_tokens": usage.get("input_tokens", 0) + q_usage["input_tokens"],
            "output_tokens": usage.get("output_tokens", 0) + q_usage["output_tokens"],
        }
        if on_event is not None:
            on_event(
                CallEvent(
                    kind="choice",
                    role=role_name,
                    endpoint=role.endpoint,
                    model=role.model,
                    params=role.params,
                    state=state,
                    questions={name: q},
                    answers=answers[name],
                    content=res.raw.get("content", ""),
                    raw=res.raw,
                    usage=q_usage,
                    context=context or {},
                )
            )
    return answers, usage


async def choice(
    client: Client,
    role_name: str,
    state: dict[str, Any],
    criteria: dict[str, str],
    instructions: str,
    context: dict[str, Any] | None = None,
    on_event: Callable[[CallEvent], None] | None = None,
) -> JevChoice:
    """Measure one probability distribution over ``criteria`` keys.

    Returns the ``JevChoice`` shape every consumer already uses; ``raw``
    carries endpoint/model/params and the rendered prompt (chat protocol)
    or the native response (decisions protocol) so the call replays
    verbatim.
    """
    role = client.config.role(role_name)
    if role.api == "decisions":
        answers, usage = await decision(
            client,
            role_name,
            state,
            {
                "choice": {
                    "type": "choice",
                    "instructions": instructions,
                    "criteria": criteria,
                }
            },
            context=context,
            on_event=on_event,
        )
        a = answers.get("choice", {})
        probs = {k: float(v) for k, v in (a.get("probabilities") or {}).items()}
        if not probs:
            raise RuntimeError(f"decisions API returned no probabilities: {a!r}")
        outcome = a.get("choice") or max(probs, key=probs.get)  # type: ignore[arg-type]
        return JevChoice(
            outcome=outcome,
            probabilities=probs,
            input_tokens=usage.get("input_tokens"),
            output_tokens=usage.get("output_tokens"),
            raw={"protocol": "decisions", "answer": a},
        )

    # chat protocol
    payload = {
        "instructions": instructions,
        "outcomes": {k: k for k in criteria},
        "outcome_descriptions": criteria,
        "case": state,
    }
    user = json.dumps(payload, ensure_ascii=False, indent=1)
    attempts = int(role.params.get("retries", DEFAULT_RETRIES))
    last_bad = ""
    for attempt in range(attempts):
        result = await client.chat(role_name, _CHOICE_SYSTEM, user)
        dist = _parse_distribution(result.content, criteria)
        if dist is not None:
            outcome = max(dist, key=dist.get)  # type: ignore[arg-type]
            event = CallEvent(
                kind="choice",
                role=role_name,
                endpoint=result.endpoint,
                model=result.model,
                params=result.params,
                state=state,
                criteria=criteria,
                instructions=instructions,
                prompt=f"{_CHOICE_SYSTEM}\n\n{user}",
                content=result.content,
                outcome=outcome,
                probabilities=dist,
                raw={
                    "protocol": "chat",
                    "choice_version": CHOICE_VERSION,
                    "endpoint": result.endpoint,
                    "model": result.model,
                    "params": result.params,
                    "response": result.raw,
                },
                usage=result.usage,
                latency_ms=result.latency_ms,
                context=context or {},
            )
            if on_event is not None:
                on_event(event)
            return JevChoice(
                outcome=outcome,
                probabilities=dist,
                input_tokens=result.usage.get("input_tokens"),
                output_tokens=result.usage.get("output_tokens"),
                raw=event.to_dict(),
            )
        last_bad = result.content[:200]
        await asyncio.sleep(2 ** min(attempt, 4))
    raise RuntimeError(
        f"choice call to {role.model_id} returned no valid distribution "
        f"after {attempts} attempts; last output: {last_bad!r}"
    )


def jsonl_sink(path: Path) -> Callable[[CallEvent], None]:
    """Archive sink for utility calls: one JSONL file per context."""
    path.parent.mkdir(parents=True, exist_ok=True)

    def sink(event: CallEvent) -> None:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(event.to_dict(), ensure_ascii=False) + "\n")

    return sink


def chat_sync(
    config: InferenceConfig,
    role_name: str,
    system: str,
    user: str,
    temperature: float | None = None,
    on_event: Callable[[CallEvent], None] | None = None,
) -> str:
    """Sync one-shot chat for utility callers (mpa_extract & friends)."""

    async def _run() -> str:
        client = Client(config)
        try:
            res = await client.chat(role_name, system, user, temperature)
            if on_event is not None:
                on_event(
                    CallEvent(
                        kind="chat",
                        role=role_name,
                        endpoint=res.endpoint,
                        model=res.model,
                        params=res.params,
                        system=system,
                        user=user,
                        content=res.content,
                        usage=res.usage,
                        latency_ms=res.latency_ms,
                        raw={"endpoint": res.endpoint, "model": res.model},
                    )
                )
            return res.content
        finally:
            await client.aclose()

    return asyncio.run(_run())
