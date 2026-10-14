"""Two-arm experiment comparison (point 4).

Reads the per-meeting payloads of both arms
(``data/processed/simulations_{ea_only,country}/{date}/payload.json``) and the
ground truth (decision-mode labels from the accounts, real rate decisions)
and produces ``data/experiments/comparison/``:

- ``per_meeting.parquet`` — one row per (meeting × arm): alignment stats,
  simulated decision mode (Jev + deterministic), decision stance vs real;
- ``report.md`` — (a) alignment delta between arms (paired exact sign test +
  bootstrap CI), (b) decision-mode match rates + 3×3 confusion matrices per
  arm vs the ground-truth labels, (c) direction accuracy per arm, (d) yearly
  episode breakdown.
"""

from __future__ import annotations

import json
import math
import random
from pathlib import Path

import polars as pl

from synthetic_council.config import PROCESSED_DIR

ARMS = ["ea_only", "country"]
SIM_ROOT = PROCESSED_DIR / "simulations"
OUT_DIR = Path("data/experiments/comparison")
MODE_LABELS = ["unanimity", "consensus", "majority"]


def _payloads(arm: str) -> dict[str, dict]:
    out: dict[str, dict] = {}
    root = SIM_ROOT / arm
    if not root.exists():
        return out
    for p in sorted(root.glob("*/payload.json")):
        out[p.parent.name] = json.loads(p.read_text(encoding="utf-8"))
    return out


def _real_direction(row: dict, prev: dict | None) -> str | None:
    if row["decision"] == "change" and prev is not None:
        delta = (row["mro"] or 0) - (prev["mro"] or 0)
        if delta > 0:
            return "raise"
        if delta < 0:
            return "lower"
        return "hold"
    return "hold"


def per_meeting_frame() -> pl.DataFrame:
    ea = _payloads("ea_only")
    co = _payloads("country")
    mode_path = PROCESSED_DIR / "account_decision_mode.parquet"
    mode_by: dict[str, str] = {}
    if mode_path.exists():
        modes = pl.read_parquet(mode_path).with_columns(pl.col("meeting_date").cast(pl.String))
        mode_by = {r["meeting_date"]: r["label"] for r in modes.iter_rows(named=True)}
    else:
        print(
            "WARNING: account_decision_mode.parquet missing (run "
            "`uv run python -m synthetic_council.mpa_extract.decision_mode`) — "
            "mode comparison will be empty"
        )
    dec = (
        pl.read_parquet(PROCESSED_DIR / "gc_decisions.parquet")
        .with_columns(pl.col("announcement_date").cast(pl.String).alias("meeting_date"))
        .sort("meeting_date")
    )
    dec_rows = dec.iter_rows(named=True)
    dec_list = list(dec_rows)
    dec_by = {r["meeting_date"]: r for r in dec_list}

    rows = []
    for arm, payloads in (("ea_only", ea), ("country", co)):
        for date, p in payloads.items():
            agents = p["agents"]
            aligned = [
                (a["alignment"]["sim"].get("aligned") if a.get("alignment") else None)
                for a in agents
            ]
            aligned = [x for x in aligned if x is not None]
            real_row = dec_by.get(date)
            idx = next((i for i, r in enumerate(dec_list) if r["meeting_date"] == date), None)
            prev = dec_list[idx - 1] if idx else None
            real_dir = _real_direction(real_row, prev) if real_row else None
            rows.append(
                {
                    "meeting_date": date,
                    "arm": arm,
                    "n_agents": len(agents),
                    "mean_p_aligned_sim": sum(aligned) / len(aligned) if aligned else None,
                    "min_p_aligned_sim": min(aligned) if aligned else None,
                    "share_aligned": (
                        sum(1 for x in aligned if x >= 0.5) / len(aligned) if aligned else None
                    ),
                    "mean_p_aligned_real": (
                        sum(
                            (a["alignment"]["real"] or {}).get("aligned", 0.0)
                            for a in agents
                            if a.get("alignment")
                        )
                        / max(1, sum(1 for a in agents if a.get("alignment")))
                    ),
                    "mode_jev": p["mode"]["jev_label"],
                    "mode_deterministic": p["mode"]["deterministic_label"],
                    "ground_truth_mode": mode_by.get(date),
                    "decision_stance": p["decision"]["stance"],
                    "real_direction": real_dir,
                    "decision_matches_real": (
                        p["decision"]["stance"] == real_dir
                        if p["decision"]["stance"] and real_dir
                        else None
                    ),
                    "year": date[:4],
                }
            )
    schema = {
        "meeting_date": pl.String,
        "arm": pl.String,
        "n_agents": pl.Int64,
        "mean_p_aligned_sim": pl.Float64,
        "min_p_aligned_sim": pl.Float64,
        "share_aligned": pl.Float64,
        "mean_p_aligned_real": pl.Float64,
        "mode_jev": pl.String,
        "mode_deterministic": pl.String,
        "ground_truth_mode": pl.String,
        "decision_stance": pl.String,
        "real_direction": pl.String,
        "decision_matches_real": pl.Boolean,
        "year": pl.String,
    }
    return pl.DataFrame(rows, schema=schema).sort("meeting_date", "arm")


def _sign_test(diffs: list[float]) -> float:
    """Exact two-sided sign test on the paired differences."""
    vals = [d for d in diffs if d != 0]
    n = len(vals)
    if n == 0:
        return 1.0
    pos = sum(1 for d in vals if d > 0)
    tail = min(pos, n - pos)
    p = sum(math.comb(n, k) for k in range(0, tail + 1)) / 2**n
    return min(1.0, 2 * p)


def _bootstrap_ci(diffs: list[float], n_boot: int = 5000, seed: int = 7) -> tuple[float, float]:
    rng = random.Random(seed)
    if not diffs:
        return (float("nan"), float("nan"))
    means = []
    for _ in range(n_boot):
        sample = [diffs[rng.randrange(len(diffs))] for _ in range(len(diffs))]
        means.append(sum(sample) / len(sample))
    means.sort()
    return means[int(0.025 * n_boot)], means[int(0.975 * n_boot)]


def _confusion(df: pl.DataFrame, arm: str, col: str) -> list[list[int]]:
    m = [
        [0, 0, 0],
        [0, 0, 0],
        [0, 0, 0],
    ]
    sub = df.filter((pl.col("arm") == arm) & pl.col("ground_truth_mode").is_not_null())
    for r in sub.iter_rows(named=True):
        if r[col] in MODE_LABELS and r["ground_truth_mode"] in MODE_LABELS:
            i = MODE_LABELS.index(r["ground_truth_mode"])
            j = MODE_LABELS.index(r[col])
            m[i][j] += 1
    return m


def _settings_by_arm(allow_mixed: bool = False) -> dict[str, list[dict]]:
    """Distinct payload settings blocks per arm (drift detection)."""
    out: dict[str, list[dict]] = {}
    for arm in ARMS:
        blocks: list[dict] = []
        for p in _payloads(arm).values():
            s = p.get("settings")
            if s is None:
                s = {"(legacy payload)": "no settings block (pre inference.toml run)"}
            if s not in blocks:
                blocks.append(s)
        out[arm] = blocks
        if len(blocks) > 1 and not allow_mixed:
            raise SystemExit(
                f"SETTINGS DRIFT in arm '{arm}': {len(blocks)} distinct settings blocks "
                "across payloads — outcome comparisons across mixed settings are "
                "misleading. Re-run the arm under one inference.toml, or pass "
                "--allow-mixed to proceed anyway."
            )
    return out


def _settings_lines(arm: str, settings: dict) -> list[str]:
    inf = settings.get("inference", {})
    roles = inf.get("roles", {})
    gen, ext = roles.get("generator", {}), roles.get("extractor", {})
    lines = [f"- {arm}:"]
    lines.append(
        f"  generator: {gen.get('endpoint')}/{gen.get('model')} "
        f"(temp {gen.get('temperature', '—')})"
    )
    lines.append(
        f"  extractor: {ext.get('endpoint')}/{ext.get('model')} "
        f"(temp {ext.get('temperature', '—')})"
    )
    if inf.get("config_sha256"):
        lines.append(f"  inference.toml sha256: {inf['config_sha256'][:16]}")
    if settings.get("git_sha"):
        lines.append(f"  git: {settings['git_sha']}")
    return lines


def diff_settings(a: dict, b: dict, prefix: str = "") -> list[str]:
    """Human-readable leaf-level deltas between two settings blocks."""
    out: list[str] = []
    keys = sorted(set(a) | set(b))
    for k in keys:
        va, vb = a.get(k), b.get(k)
        if isinstance(va, dict) and isinstance(vb, dict):
            out.extend(diff_settings(va, vb, f"{prefix}{k}."))
        elif va != vb:
            out.append(f"{prefix}{k}: {va!r} -> {vb!r}")
    return out


def build_report(df: pl.DataFrame, settings_by_arm: dict[str, list[dict]] | None = None) -> str:
    lines = ["# Two-arm experiment comparison", ""]
    lines.append(f"Meetings: {df['meeting_date'].n_unique()} | arms: {ARMS}")
    lines.append("")

    # run settings (models, sampling parameters, config hash)
    settings_by_arm = settings_by_arm if settings_by_arm is not None else {}
    if any(settings_by_arm.get(a) for a in ARMS):
        lines.append("## Run settings")
        lines.append("")
        for arm in ARMS:
            for s in settings_by_arm.get(arm, []):
                lines.extend(_settings_lines(arm, s))
        lines.append("")

    # (a) alignment delta, paired by meeting
    ea = {
        r["meeting_date"]: r["mean_p_aligned_sim"]
        for r in df.filter(pl.col("arm") == "ea_only").iter_rows(named=True)
        if r["mean_p_aligned_sim"] is not None
    }
    co = {
        r["meeting_date"]: r["mean_p_aligned_sim"]
        for r in df.filter(pl.col("arm") == "country").iter_rows(named=True)
        if r["mean_p_aligned_sim"] is not None
    }
    common = sorted(set(ea) & set(co))
    diffs = [co[d] - ea[d] for d in common]
    if diffs:
        mean_d = sum(diffs) / len(diffs)
        lo, hi = _bootstrap_ci(diffs)
        lines.append("## (a) Alignment delta (country − ea_only), paired by meeting")
        lines.append("")
        lines.append(f"- n paired meetings: {len(diffs)}")
        lines.append(f"- mean Δ P(aligned with simulated decision): {mean_d:+.4f}")
        lines.append(f"- bootstrap 95% CI: [{lo:+.4f}, {hi:+.4f}]")
        lines.append(f"- exact sign test p-value: {_sign_test(diffs):.4f}")
        lines.append("- positive Δ ⇒ the country annex increases internal alignment")
        lines.append("")

    # (b) decision-mode match vs ground truth
    lines.append("## (b) Decision-mode match vs accounts ground truth")
    lines.append("")
    for arm in ARMS:
        for col, name in (("mode_jev", "Jev"), ("mode_deterministic", "deterministic")):
            m = _confusion(df, arm, col)
            total = sum(sum(r) for r in m)
            diag = sum(m[i][i] for i in range(3))
            lines.append(f"### {arm} / {name}")
            lines.append("")
            lines.append("| ground \\ simulated | " + " | ".join(MODE_LABELS) + " |")
            lines.append("|---|---|---|---|")
            for i, label in enumerate(MODE_LABELS):
                lines.append(f"| {label} | " + " | ".join(str(x) for x in m[i]) + " |")
            lines.append("")
            lines.append(f"match rate: {diag}/{total}")
            lines.append("")

    # (c) direction accuracy
    lines.append("## (c) Decision direction accuracy vs real decisions")
    lines.append("")
    for arm in ARMS:
        sub = df.filter((pl.col("arm") == arm) & pl.col("decision_matches_real").is_not_null())
        if sub.height:
            acc = sub["decision_matches_real"].mean()
            lines.append(f"- {arm}: {sub.height} meetings, accuracy {acc:.3f}")
    lines.append("")

    # (d) yearly breakdown
    lines.append("## (d) Yearly breakdown (mean P(aligned_sim) and direction accuracy)")
    lines.append("")
    lines.append("| year | arm | n | mean P(aligned) | dir. accuracy |")
    lines.append("|---|---|---|---|---|")
    for r in (
        df.group_by("year", "arm")
        .agg(
            pl.len().alias("n"),
            pl.col("mean_p_aligned_sim").mean().alias("align"),
            pl.col("decision_matches_real").mean().alias("acc"),
        )
        .sort("year", "arm")
        .iter_rows(named=True)
    ):
        acc = f"{r['acc']:.2f}" if r["acc"] is not None else "—"
        align = f"{r['align']:.3f}" if r["align"] is not None else "—"
        lines.append(f"| {r['year']} | {r['arm']} | {r['n']} | {align} | {acc} |")
    lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> None:
    import argparse

    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--allow-mixed", action="store_true", help="skip the settings-drift guard")
    p.add_argument(
        "--diff-runs",
        nargs=2,
        metavar=("PAYLOAD_A", "PAYLOAD_B"),
        help="print the settings deltas between two payload.json files "
        "(e.g. an original and a replay variant)",
    )
    args = p.parse_args(argv)

    if args.diff_runs:
        a = json.loads(Path(args.diff_runs[0]).read_text(encoding="utf-8")).get("settings", {})
        b = json.loads(Path(args.diff_runs[1]).read_text(encoding="utf-8")).get("settings", {})
        deltas = diff_settings(a, b) or ["(no differences)"]
        print("\n".join(deltas))
        return

    df = per_meeting_frame()
    if not df.height:
        raise SystemExit(
            "no simulation payloads found under "
            f"{SIM_ROOT}/{{{','.join(ARMS)}}} — run both arms first"
        )
    settings_by_arm = _settings_by_arm(allow_mixed=args.allow_mixed)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    df.write_parquet(OUT_DIR / "per_meeting.parquet")
    report = build_report(df, settings_by_arm)
    (OUT_DIR / "report.md").write_text(report, encoding="utf-8")
    print(report)
    print(f"\nwritten: {OUT_DIR}/per_meeting.parquet, {OUT_DIR}/report.md")


if __name__ == "__main__":
    main()
