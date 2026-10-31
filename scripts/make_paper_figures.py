#!/usr/bin/env python
"""Generate the results figures (and verified numbers) for the paper.

Usage:
    uv run python scripts/make_paper_figures.py            # figures only
    uv run python scripts/make_paper_figures.py --verify   # + number sheet for the text

Reads the two experiment parquets produced by analysis/compare_arms.py:
    data/experiments/comparison_glm/per_meeting.parquet   (primary, GLM-5.3-Flash)
    data/experiments/comparison/per_meeting.parquet       (replication, Muse-Spark)

Writes vector PDFs to paper/figures/ and prints every headline statistic the
paper quotes, recomputed from the parquets, so the text never cites a number
that cannot be reproduced from the artifacts.
"""

from __future__ import annotations

import argparse
import math
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import polars as pl

REPO = Path(__file__).resolve().parents[1]
EXPERIMENTS = {
    "glm": REPO / "data/experiments/comparison_glm/per_meeting.parquet",
    "muse": REPO / "data/experiments/comparison/per_meeting.parquet",
}
FIG_DIR = REPO / "paper/figures"

# Okabe-Ito colour-blind-safe palette.
EA_COLOR = "#0072B2"
COUNTRY_COLOR = "#D55E00"


def load_experiment(path: Path) -> pl.DataFrame:
    df = pl.read_parquet(path)
    return df.with_columns(pl.col("meeting_date").str.to_date("%Y-%m-%d"))


def paired_delta(df: pl.DataFrame) -> pl.DataFrame:
    """Per-meeting delta mean_p_aligned_sim (country arm minus ea_only arm)."""
    wide = df.pivot("arm", index="meeting_date", values="mean_p_aligned_sim")
    return wide.with_columns(
        (pl.col("country") - pl.col("ea_only")).alias("delta")
    ).drop_nulls()


def exact_sign_test(deltas: np.ndarray) -> float:
    """Two-sided exact sign test on the paired deltas (zeros dropped)."""
    d = deltas[deltas != 0.0]
    n = d.size
    k = min(int((d > 0).sum()), int((d < 0).sum()))
    p = 2.0 * sum(math.comb(n, i) for i in range(k + 1)) / 2**n
    return min(p, 1.0)


def bootstrap_ci(
    deltas: np.ndarray, n_boot: int = 10_000, seed: int = 42
) -> tuple[float, float]:
    rng = np.random.default_rng(seed)
    means = rng.choice(deltas, size=(n_boot, deltas.size), replace=True).mean(axis=1)
    return float(np.percentile(means, 2.5)), float(np.percentile(means, 97.5))


def plot_arm_delta(dfs: dict[str, pl.DataFrame]) -> None:
    """Figure 2: distribution of the paired per-meeting alignment delta."""
    fig, axes = plt.subplots(1, 2, figsize=(6.6, 2.9), sharey=True)
    panels = [("glm", "GLM-5.3-Flash (primary)"), ("muse", "Muse-Spark (replication)")]
    for ax, (key, title) in zip(axes, panels, strict=True):
        d = paired_delta(dfs[key])["delta"].to_numpy()
        ax.hist(
            d,
            bins=24,
            range=(-0.30, 0.30),
            color="#6E7B8B",
            edgecolor="white",
            linewidth=0.4,
        )
        mean = d.mean()
        ax.axvline(0.0, color="black", linewidth=0.8)
        ax.axvline(mean, color=COUNTRY_COLOR, linewidth=1.4)
        ax.set_title(title, fontsize=9)
        ax.set_xlabel(r"$\Delta$ mean P(aligned), country $-$ ea$\_$only", fontsize=9)
        ax.tick_params(labelsize=8)
        # annotation in axes-fraction space, on the side away from the mean line
        ax.text(
            0.97 if mean < 0 else 0.03,
            0.95,
            f"mean = {mean:+.4f}\nn = {d.size} meetings",
            transform=ax.transAxes,
            ha="right" if mean < 0 else "left",
            va="top",
            fontsize=8,
            color=COUNTRY_COLOR,
        )
    axes[0].set_ylabel("Meetings", fontsize=9)
    for ax in axes:
        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_visible(False)
    fig.tight_layout()
    fig.savefig(FIG_DIR / "fig2_arm_delta.pdf")
    plt.close(fig)


def plot_cohesion(dfs: dict[str, pl.DataFrame]) -> None:
    """Figure 3: yearly mean internal cohesion by arm and generator."""
    fig, ax = plt.subplots(figsize=(6.6, 3.1))
    styles = {"glm": "-", "muse": "--"}
    alphas = {"glm": 1.0, "muse": 0.55}
    for key, df in dfs.items():
        yearly = (
            df.group_by("year", "arm")
            .agg(pl.col("mean_p_aligned_sim").mean())
            .sort("year", "arm")
            .with_columns(pl.col("year").cast(pl.Int32))
        )
        for arm, color in [("ea_only", EA_COLOR), ("country", COUNTRY_COLOR)]:
            s = yearly.filter(pl.col("arm") == arm)
            label = {"ea_only": r"ea$\_$only", "country": "country"}[arm]
            gen_label = {"glm": "GLM", "muse": "Muse"}[key]
            ax.plot(
                s["year"],
                s["mean_p_aligned_sim"],
                styles[key],
                color=color,
                alpha=alphas[key],
                linewidth=1.8,
                label=f"{label} ({gen_label})",
            )
    ax.set_xticks(range(2015, 2027))
    ax.set_ylim(0.78, 1.0)
    ax.set_ylabel("Mean P(aligned with simulated decision)", fontsize=9)
    ax.set_xlabel("Year", fontsize=9)
    ax.tick_params(labelsize=8)
    ax.legend(fontsize=7.5, ncol=2, frameon=False, loc="lower left")
    ax.grid(axis="y", color="#DDDDDD", linewidth=0.5)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    fig.tight_layout()
    fig.savefig(FIG_DIR / "fig3_cohesion.pdf")
    plt.close(fig)


def confusion(df: pl.DataFrame, arm: str, mode_col: str) -> pl.DataFrame:
    return (
        df.filter(pl.col("arm") == arm)
        .group_by("ground_truth_mode", mode_col)
        .len()
        .sort("ground_truth_mode", mode_col)
    )


def verify(dfs: dict[str, pl.DataFrame]) -> None:
    """Print every number the paper quotes, recomputed from the parquets."""
    for key, df in dfs.items():
        d = paired_delta(df)["delta"].to_numpy()
        lo, hi = bootstrap_ci(d)
        print(f"== {key.upper()} ==")
        print(
            f"  paired delta: n={d.size}  mean={d.mean():+.4f}  "
            f"boot95=[{lo:+.4f}, {hi:+.4f}]  sign p={exact_sign_test(d):.4f}"
        )
        for arm in ("ea_only", "country"):
            sub = df.filter(pl.col("arm") == arm)
            acc = sub["decision_matches_real"].mean()
            coh = sub["mean_p_aligned_sim"].mean()
            for mode_col in ("mode_jev", "mode_deterministic"):
                match = (sub[mode_col] == sub["ground_truth_mode"]).sum()
                print(f"  {arm:9s} {mode_col:18s} mode match {match}/94")
            print(f"  {arm:9s} cohesion={coh:.4f}  direction acc={acc:.3f}")
        for arm in ("ea_only", "country"):
            print(f"  confusion {arm}/jev:\n{confusion(df, arm, 'mode_jev')}")
        gt = (
            df.filter(pl.col("arm") == "ea_only")["ground_truth_mode"]
            .value_counts()
            .sort("ground_truth_mode")
        )
        print(f"  ground-truth margins:\n{gt}")

    joined = dfs["glm"].join(dfs["muse"], on=["meeting_date", "arm"], suffix="_muse")
    for arm in ("ea_only", "country"):
        sub = joined.filter(pl.col("arm") == arm)
        stance = (sub["decision_stance"] == sub["decision_stance_muse"]).mean()
        mode = (sub["mode_jev"] == sub["mode_jev_muse"]).mean()
        print(
            f"cross-generator {arm}: stance agreement={stance:.3f}  "
            f"jev-mode agreement={mode:.3f}"
        )
    print(f"always-unanimity baseline: {35 / 94:.3f}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--verify",
        action="store_true",
        help="print the number sheet for the paper text",
    )
    args = parser.parse_args()

    FIG_DIR.mkdir(parents=True, exist_ok=True)
    dfs = {key: load_experiment(path) for key, path in EXPERIMENTS.items()}
    plot_arm_delta(dfs)
    plot_cohesion(dfs)
    print(f"figures written to {FIG_DIR}")
    if args.verify:
        verify(dfs)


if __name__ == "__main__":
    main()
