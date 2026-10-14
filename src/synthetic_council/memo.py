"""Memo builder: per-member briefing for each GC monetary policy meeting.

For every Governing Council member (person x role profile active at the meeting
date) and every monetary policy decision meeting, produce a 1-2 page markdown memo:

1. Header — member, role, country (for governors), meeting date, current rates.
2. Member profile — web-evidence persona card (Tavily per-trait + Wikipedia;
   ``scripts/build_profiles.py``), embedded when available.
3. Euro area situation — as-of vintage macro data (HICP, GDP, unemployment, CISS,
   sentiment) + latest staff projections available at that date.
4. Country annex (governors only, ``country_annex=True``) — country sentiment,
   and (where vintages exist) country unemployment/industrial production.
5. Speech stance — the member's latest speech in the strict inter-meeting
   window (``speech_sections``), embedded only when one exists.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import jinja2
import polars as pl

from synthetic_council.collectors import mpd_items
from synthetic_council.collectors.mpd_rounds import rounds_frame
from synthetic_council.config import PROCESSED_DIR, RAW_DIR

COUNTRY_NAMES = {
    "AT": "Austria",
    "BE": "Belgium",
    "CY": "Cyprus",
    "DE": "Germany",
    "EE": "Estonia",
    "ES": "Spain",
    "FI": "Finland",
    "FR": "France",
    "EL": "Greece",
    "GR": "Greece",
    "HR": "Croatia",
    "IE": "Ireland",
    "IT": "Italy",
    "LT": "Lithuania",
    "LU": "Luxembourg",
    "LV": "Latvia",
    "MT": "Malta",
    "NL": "Netherlands",
    "PT": "Portugal",
    "SI": "Slovenia",
    "SK": "Slovakia",
}

PROFILES_DIR = PROCESSED_DIR / "member_profiles"
SPEECH_SECTIONS_DIR = PROCESSED_DIR / "speech_sections"

MEMO_TEMPLATE = """# Briefing memo — {{ member.person }}

**Role:** {{ member.role }}
{% if member.country %}**Country:** {{ country_name }}
{% endif %}**Meeting:** GC monetary policy meeting, {{ meeting.announcement_date }}
**Current rates:** MRO {{ meeting.mro }}% · DFR {{ meeting.dfr }}% · MLF {{ meeting.mlf }}%

{% if profile %}
## Who you are

{{ profile }}

{% endif %}## Euro area economic situation (data available at the meeting)

| Indicator | Reference | Value |
|---|---|---|
{% for row in ea_rows %}| {{ row.indicator }} | {{ row.ref_period }} | {{ row.value }} |
{% endfor %}

## Latest staff projections available at the meeting

Projection round: **{{ proj_round }}** (ECB/Eurosystem staff, euro area;
all available items; annual figures).

{% for group in projections_by_category %}
### {{ group.category }}

{{ group.header }}
{{ group.sep }}
{% for p in group.rows %}{{ p.line }}
{% endfor %}
{% endfor %}


{% if country_rows %}
## {{ country_name }} — country situation

| Indicator | Reference | Value |
|---|---|---|
{% for row in country_rows %}| {{ row.indicator }} | {{ row.ref_period }} | {{ row.value }} |
{% endfor %}
{% endif %}

{% if speech_section %}
## Your most recent speech before the meeting

{{ speech_section }}
{% endif %}
"""


@dataclass
class MemoResult:
    n_memos: int
    out_dir: Path


def _indicator_label(ind: str) -> str:
    return {
        "hicp_yoy": "HICP inflation (y/y %)",
        "hicp_core": "HICP core — ex energy, food, alcohol & tobacco (y/y %)",
        "gdp_yoy": "Real GDP growth (y/y %)",
        "unemp": "Unemployment rate (%)",
        "ciss": "CISS (systemic stress)",
        "sent_esi": "Economic Sentiment Indicator",
        "sent_cons": "Consumer confidence",
    }.get(ind, ind)


def _fmt(v) -> str:
    """Format a numeric value for the memo tables."""
    if v is None:
        return "n/a"
    try:
        return f"{float(v):.2f}"
    except (TypeError, ValueError):
        return str(v)


def _person_slug(person: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", person.lower()).strip("-")
    return s


def profile_text(person: str) -> str:
    """Compact profile card body (header line and provenance footer stripped)."""
    path = PROFILES_DIR / f"{person}.md"
    if not path.exists():
        return ""
    text = path.read_text(encoding="utf-8")
    text = re.sub(r"^# .*?\n", "", text, count=1)
    parts = text.split("\n---\n")
    body = parts[0]
    # keep the roles/facts lines; drop the "**Country:**/blank scaffolding lines
    lines = [ln for ln in body.splitlines() if ln.strip() and not ln.startswith("**")]
    return "\n".join(lines).strip()[:2_500]


def _speech_section(meeting_date: str, person: str) -> str:
    path = SPEECH_SECTIONS_DIR / f"{meeting_date}_{_person_slug(person)}.md"
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8").strip()


def _proj_round_available(projections: pl.DataFrame, meeting_date) -> pl.DataFrame:
    """Latest MPD projection rounds published on/before the meeting.

    MPD rounds are coded in PD_SEAS_EX as {W,A,S,G}{yy} = March/June/September/
    December of 20yy (decoding verified against published projections, see
    collectors/mpd_rounds.py). A round is available from the first of its month.
    """
    df = rounds_frame(projections)
    df = df.with_columns(pl.col("round_date").str.to_date("%Y-%m-%d"))
    return df.filter(pl.col("round_date") <= meeting_date)


def build_memos(
    meetings: pl.DataFrame,
    memberships: pl.DataFrame,
    macro: pl.DataFrame,
    projections: pl.DataFrame,
    out_dir: Path,
    meetings_limit: int | None = None,
    country_annex: bool = True,
) -> MemoResult:
    """Build one memo per (meeting, member).

    ``country_annex=False`` produces the euro-area-only variant (experiment
    arm ``ea_only``): the governor's country section is omitted; everything
    else — including the member profile and the strict-window speech stance —
    is identical across variants.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    env = jinja2.Environment(trim_blocks=True, lstrip_blocks=True)
    tpl = env.from_string(MEMO_TEMPLATE)

    meets = meetings.sort("announcement_date")
    if meetings_limit:
        meets = meets.tail(meetings_limit)

    n = 0
    for m in meets.iter_rows(named=True):
        d = m["announcement_date"]
        # active tenures, with a +45d grace past 'end' (Wayback sighting gaps).
        # A person can qualify twice near handovers (old role in grace, new role
        # started) — keep ONE memo per person: the tenure already started at d,
        # else the latest one.
        attendees = (
            memberships.filter(
                (pl.col("start").str.to_date() <= d)
                & (pl.col("end").str.to_date() + pl.duration(days=45) >= d)
            )
            .with_columns(
                (
                    (pl.col("end").str.to_date() >= d).alias("_active")
                    & (pl.col("start").str.to_date() <= d)
                ).alias("_started")
            )
            .sort(["person", "_started", "start"], descending=[False, True, True])
            .unique(subset=["person"], keep="first")
        )
        ea_rows = macro.filter((pl.col("announcement_date") == d) & (pl.col("geo") == "EA")).sort(
            "indicator"
        )
        ea_out = [
            {
                "indicator": _indicator_label(r["indicator"]),
                "ref_period": r["ref_period"],
                "value": _fmt(r["value"]),
            }
            for r in ea_rows.iter_rows(named=True)
        ]
        # projections: latest round before meeting, EA, headline items
        avail = _proj_round_available(projections, d)
        proj_out = []
        last_round = None
        if avail.height:
            last_round = avail["round_date"].max()
            round_year = int(str(last_round)[:4])
            ea_proj = avail.filter(
                (pl.col("round_date") == last_round)
                & (pl.col("REF_AREA") == "U2")
                & (pl.col("FREQ") == "A")
                & pl.col("TIME_PERIOD").str.contains(r"^\d{4}$")
            ).filter(
                # forward-looking targets only (MPD includes interpolated backdata)
                pl.col("TIME_PERIOD").cast(pl.Int32) >= round_year
            )
            for item in mpd_items.category_order():
                rows = ea_proj.filter(pl.col("PD_ITEM") == item).sort("TIME_PERIOD")
                if not rows.height:
                    continue
                short, unit, cat = mpd_items.ITEMS[item]
                proj_out.append(
                    {
                        "category": cat,
                        "item": short,
                        "unit": unit,
                        "by_year": {
                            r["TIME_PERIOD"]: _fmt(r["OBS_VALUE"])
                            for r in rows.iter_rows(named=True)
                        },
                    }
                )
        # group by category in display order; union of target years;
        # pre-render one markdown table row per item
        proj_years: list[str] = sorted({y for p in proj_out for y in p["by_year"]})
        for p in proj_out:
            cells = " | ".join(p["by_year"].get(y, "—") for y in proj_years)
            p["line"] = f"| {p['item']} | {p['unit']} | {cells} |"
        ycols = " | ".join(proj_years)
        projections_by_category = [
            {
                "category": cat,
                "header": f"| Item | Unit | {ycols} |",
                "sep": "|---|---|" + "---|" * len(proj_years),
                "rows": [p for p in proj_out if p["category"] == cat],
            }
            for cat in mpd_items.CATEGORY_ORDER
            if any(p["category"] == cat for p in proj_out)
        ]
        for a in attendees.iter_rows(named=True):
            country_rows = []
            if country_annex and a["country"]:
                cgeo = "EL" if a["country"] == "GR" else a["country"]
                crows = macro.filter(
                    (pl.col("announcement_date") == d) & (pl.col("geo") == cgeo)
                ).sort("indicator")
                country_rows = [
                    {
                        "indicator": _indicator_label(r["indicator"]),
                        "ref_period": r["ref_period"],
                        "value": _fmt(r["value"]),
                    }
                    for r in crows.iter_rows(named=True)
                ]
            md = tpl.render(
                member=a,
                country_name=COUNTRY_NAMES.get(a["country"] or "", ""),
                meeting={
                    "announcement_date": str(d),
                    "mro": m["mro"] if m["mro"] is not None else "n/a",
                    "dfr": m["dfr"] if m["dfr"] is not None else "n/a",
                    "mlf": m["mlf"] if m["mlf"] is not None else "n/a",
                },
                profile=profile_text(a["person"]),
                ea_rows=ea_out,
                projections_by_category=projections_by_category,
                proj_years=proj_years,
                proj_round=str(last_round) if proj_out else "",
                country_rows=country_rows,
                speech_section=_speech_section(str(d), a["person"]),
            )
            fname = f"{d}_{a['person'].replace(' ', '_')}.md"
            (out_dir / fname).write_text(md, encoding="utf-8")
            n += 1
    return MemoResult(n_memos=n, out_dir=out_dir)


if __name__ == "__main__":
    meetings = pl.read_parquet(PROCESSED_DIR / "gc_decisions.parquet")
    memberships = pl.read_parquet("data/raw/gc_memberships_wayback.parquet")
    macro = pl.read_parquet(PROCESSED_DIR / "macro_asof_panel.parquet")
    projections = pl.read_parquet(RAW_DIR / "mpd_projections.parquet")
    for annex, name in ((False, "memos_ea"), (True, "memos_ea_country")):
        res = build_memos(
            meetings,
            memberships,
            macro,
            projections,
            Path(f"data/processed/{name}"),
            country_annex=annex,
        )
        print(res)
