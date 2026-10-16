"""Deterministic extraction of GC participants + voting rights from accounts.

The accounts close with an attendees block (layouts differ across eras):

    ### Members                        (#### in the 2024+ layout)
    Mr Draghi, President               ("- " bullets in the 2024+ layout)
    Mr Hansson (\\*)                   ("Mr Makhlouf\\\\*" in the 2024+ layout)
    ...
    (\\*) Members not holding a voting right in January 2015 under Article 10.2
    of the ESCB Statute.

Semantics of the asterisk (verified on 2015-01-22 and 2024-12-12 accounts): it
marks members NOT holding a voting right that month under the rotation system
— Executive Board members never carry it. ``voting_right`` = no asterisk.

Roles/countries are cross-checked against ``gc_memberships_wayback`` when
available (the account block itself names only the President and Vice-President
and marks alternates, e.g. "Mr Demarco, temporarily replacing Mr Scicluna").

Output: data/processed/account_participants.parquet + .csv
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import polars as pl

from synthetic_council.collectors.members import CENTRAL_BANK_BY_COUNTRY
from synthetic_council.collectors.persons import canonicalise
from synthetic_council.config import PROCESSED_DIR, RAW_DIR
from synthetic_council.mpa_extract.ce_tenure import chief_economist_on

ACCOUNTS_DIR = RAW_DIR / "accounts"
OUT_PATH = PROCESSED_DIR / "account_participants.parquet"

# heading variants seen 2015→: "## Members", "**Members**" (2016-20) and the
# bare "Members" line (e.g. 2019-04-10, whose layout otherwise yields zero
# participants and silently degrades every downstream consumer)
_MEMBERS_HEADING_RE = re.compile(r"^(?:#{2,4}\s+Members|\*\*Members\*\*|Members)\s*$", re.MULTILINE)
_NEXT_HEADING_RE = re.compile(r"^(?:#{1,4}\s|\*\*)")
_STAR_MARKER_RE = re.compile(r"\(\*+\)|\\*+|\*+")
_NUM_MARKER_RE = re.compile(r"\(\d+\)|[¹²³]")
_HONORIFIC_RE = re.compile(r"^(?:Mr|Ms|Mrs|Dr)\.?\s+")
_REPLACE_RE = re.compile(
    r",?\s+(?:temporarily\s+)?replacing\s+(?:Mr|Ms)\s+([A-ZÀ-Ž][\w.'-]+)", re.IGNORECASE
)
_ALTERNATE_TO_RE = re.compile(
    r",?\s+Alternate\s+to\s+(?:Mr|Ms)\s+([A-ZÀ-Ž][\w.'-]+)", re.IGNORECASE
)
_DEPUTY_RE = re.compile(r",\s+(Deputy|Vice|Acting)\s+Governor\s+of\s+(.+)$", re.IGNORECASE)
_ATTENDANCE_RE = re.compile(
    r",\s+(?:via teleconference|by video conference|via video conference)$", re.IGNORECASE
)

_BANK_TO_COUNTRY = {bank.lower(): c for c, bank in CENTRAL_BANK_BY_COUNTRY.items()}
# the accounts print NCBs under their native names; official English names
# (the map above) never match them
_BANK_TO_COUNTRY.update(
    {
        "latvijas banka": "LV",
        "hrvatska narodna banka": "HR",
        "eesti pank": "EE",
        "suomen pankki": "FI",
        "bancaditalia": "IT",
        "banca d'italia": "IT",
    }
)

# Council members who sat (some with voting rights) but were never captured
# by the Wayback GC-members snapshots, so neither the tenure lookup nor the
# account line itself carries their role/country — verified against the
# accounts and the NCBs' own records
_KNOWN_NON_MEMBERS = {
    "Margarita Delgado": ("Acting Governor, Banco de España", "ES"),  # 2024-07-18, voted
    "Tuomas Välimäki": ("Member of the Board, Bank of Finland", "FI"),  # 2023-07 → 2024-01
}


@dataclass
class Participant:
    meeting_date: str
    person: str
    role: str
    country: str | None
    replacing: str | None
    voting_right: bool
    is_chief_economist: bool
    in_memberships: bool


@dataclass
class ParticipantsResult:
    n_meetings: int
    n_participants: int
    n_with_vote: int
    n_alternates: int
    n_not_in_memberships: int
    meetings_without_block: list[str] = field(default_factory=list)


def _strip_bullets(line: str) -> str:
    return re.sub(r"^[-*]\s+", "", line).strip()


def _clean_entry(line: str) -> tuple[str | None, int, str | None, str | None, str | None]:
    """One attendee line -> (name, star count, replaced person, deputy role, country).

    Handles the marker/annotation formats seen across eras: "(\\*)" / "\\\\*"
    voting-rights asterisks (count), "(1)" / superscript footnotes (stripped),
    ", via teleconference", ", President"/", Vice-President" suffixes with or
    without comma, "replacing Mr X", "Alternate to Mr X", and
    ", Deputy/Vice/Acting Governor of {bank}" alternates.
    """
    stars = 0

    def _count(m: re.Match) -> str:
        nonlocal stars
        stars += len(m.group(0).replace("(", "").replace(")", "").replace("\\", ""))
        return ""

    cleaned = _STAR_MARKER_RE.sub(_count, line)
    cleaned = _NUM_MARKER_RE.sub("", cleaned)
    cleaned = re.sub(r"\(\s*\)", "", cleaned)  # empty parens left by odd escaping
    cleaned = _ATTENDANCE_RE.sub("", cleaned).strip(" ,")
    if not cleaned:
        return None, stars, None, None, None

    replaced = None
    for pat in (_REPLACE_RE, _ALTERNATE_TO_RE):
        r = pat.search(cleaned)
        if r:
            replaced = canonicalise(r.group(1))
            cleaned = pat.sub("", cleaned).strip(" ,")

    deputy_role = country_from_bank = None
    dep = _DEPUTY_RE.search(cleaned)
    if dep:
        kind = dep.group(1).capitalize()
        bank = dep.group(2).strip().strip(".")
        deputy_role = f"Alternate ({kind} Governor, {bank})"
        country_from_bank = _BANK_TO_COUNTRY.get(bank.lower())
        cleaned = _DEPUTY_RE.sub("", cleaned).strip(" ,")

    cleaned = re.sub(r",?\s*(President|Vice-President)$", "", cleaned).strip()
    if not _HONORIFIC_RE.match(cleaned):
        return None, stars, None, None, None
    cleaned = _HONORIFIC_RE.sub("", cleaned).strip()
    return canonicalise(cleaned), stars, replaced, deputy_role, country_from_bank


def parse_members_block(markdown: str) -> tuple[list[dict], str]:
    """The Members list as parsed attendee dicts + the voting footnote.

    Handles both era layouts: 2015-style plain lines with "(\\*)" markers and
    2024+ bullet lists with trailing escaped asterisks, plus the bold
    "**Members**" heading used 2016-2020.
    """
    m = _MEMBERS_HEADING_RE.search(markdown)
    if not m:
        return [], ""
    block: list[str] = []
    for line in markdown[m.end() + 1 :].splitlines():
        if _NEXT_HEADING_RE.match(line):
            break
        block.append(line)
    entries: list[dict] = []
    footnote = ""
    for raw in block:
        line = _strip_bullets(raw)
        if not line:
            continue
        name, stars, replaced, deputy_role, country_from_bank = _clean_entry(line)
        if name is None:
            if "voting right" in line:  # the footnote line, not an attendee
                footnote = raw.strip()
            continue
        entries.append(
            {
                "name": name,
                "voting_right": stars == 0,
                "replacing": replaced,
                "deputy_role": deputy_role,
                "country_from_bank": country_from_bank,
            }
        )
    return entries, footnote


def _memberships_lookup() -> pl.DataFrame | None:
    path = RAW_DIR / "gc_memberships_wayback.parquet"
    if not path.exists():
        return None
    df = pl.read_parquet(path)
    return df.with_columns(
        pl.col("start").str.to_date("%Y-%m-%d").alias("_start"),
        pl.col("end").str.to_date("%Y-%m-%d").alias("_end"),
    )


def _role_country(
    memberships: pl.DataFrame | None, person: str, day: str
) -> tuple[str, str | None, str, bool]:
    """Resolve (canonical person, role, country, found) for an account name.

    Accounts name members by surname ("Mr Cœuré"); memberships carry full
    names ("Benoît Cœuré"). Try the exact canonical name first, then a
    surname match restricted to tenures active at the meeting date.
    """
    if memberships is None:
        return person, "Governing Council member", None, False
    d = pl.lit(day).str.to_date("%Y-%m-%d")
    active = memberships.filter(
        # ±45d grace: Wayback tenures are first-seen/last-seen, so they can
        # start after (and end before) the true appointment/departure date
        (pl.col("_start") - pl.duration(days=45) <= d)
        & (pl.col("_end") + pl.duration(days=45) >= d)
    )
    exact = active.filter(pl.col("person") == person)
    pool = (
        exact
        if exact.height
        else active.filter(
            pl.col("person").str.to_lowercase().str.ends_with(f" {person.split()[-1].lower()}")
        )
    )
    if not pool.height:
        return person, "Governing Council member", None, False
    row = pool.sort("_end", descending=True).row(0, named=True)
    return row["person"], row["role"], row["country"], True


def extract(meetings: list[str] | None = None) -> ParticipantsResult:
    """Parse all account files (or the given meeting dates)."""
    files = (
        sorted(ACCOUNTS_DIR.glob("*_account.md"))
        if meetings is None
        else [ACCOUNTS_DIR / f"{d}_account.md" for d in meetings]
    )
    memberships = _memberships_lookup()
    rows: list[Participant] = []
    missing_block: list[str] = []
    for path in files:
        meeting_date = path.name[:10]
        markdown = path.read_text(encoding="utf-8")
        # skip front matter
        if markdown.startswith("---"):
            markdown = markdown.split("---", 2)[2]
        entries, footnote = parse_members_block(markdown)
        if not entries:
            missing_block.append(meeting_date)
            continue
        ce = chief_economist_on(meeting_date)
        for e in entries:
            full, role, country, in_db = _role_country(memberships, e["name"], meeting_date)
            if not in_db and e["deputy_role"]:
                role, country = e["deputy_role"], e["country_from_bank"]
            if not in_db and country is None and full in _KNOWN_NON_MEMBERS:
                role, country = _KNOWN_NON_MEMBERS[full]
            rows.append(
                Participant(
                    meeting_date=meeting_date,
                    person=full,
                    role=role,
                    country=country,
                    replacing=e["replacing"],
                    voting_right=e["voting_right"],
                    is_chief_economist=bool(ce and full.endswith(ce.split()[-1])),
                    in_memberships=in_db,
                )
            )
    schema = {
        "meeting_date": pl.String,
        "person": pl.String,
        "role": pl.String,
        "country": pl.String,
        "replacing": pl.String,
        "voting_right": pl.Boolean,
        "is_chief_economist": pl.Boolean,
        "in_memberships": pl.Boolean,
    }
    df = pl.DataFrame([r.__dict__ for r in rows], schema=schema)
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    df.write_parquet(OUT_PATH)
    df.write_csv(OUT_PATH.with_suffix(".csv"))
    return ParticipantsResult(
        n_meetings=df["meeting_date"].n_unique() if df.height else 0,
        n_participants=df.height,
        n_with_vote=int(df["voting_right"].sum()) if df.height else 0,
        n_alternates=int(df["replacing"].is_not_null().sum()) if df.height else 0,
        n_not_in_memberships=int((~df["in_memberships"]).sum()) if df.height else 0,
        meetings_without_block=missing_block,
    )


if __name__ == "__main__":
    print(extract())
