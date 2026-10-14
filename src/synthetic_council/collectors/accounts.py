"""Collector: ECB Monetary policy accounts (February 2015 → present).

The accounts of the Governing Council's monetary policy meetings are published
~4 weeks after each meeting as standalone HTML pages. They are enumerated
deterministically from the foedb publications database (collected by
``collectors.foedb``): every record whose document URLs point under
/press/accounts/ (96 records as of 2026-09; type facet 20).

For each account the English HTML page is fetched and the ``<main>`` content is
converted to markdown (markdownify). The meeting date is parsed from the
document itself — the opening sentence "… held in Frankfurt am Main on
Wednesday and Thursday, 11-12 December 2024" (fallback: the "Meeting of the
ECB's Governing Council, …" heading or the foedb title "Meeting of 22-23 July
2026") — NOT from the URL slug, which carries the publication date. The
decision day is the second day of the two-day meeting; dates are validated
against ``gc_decisions.announcement_date`` (±35 days) when that dataset exists.

Each markdown file starts with a small front-matter block (source_url,
publication_date, meeting_date) for inspection; parsers must skip it.

Output:
- data/raw/accounts/{meeting_date}_account.md   (one file per meeting)
- data/raw/accounts_index.parquet + .csv

See docs/data-sources/ecb-accounts.md for the full write-up.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from datetime import date

import httpx
import polars as pl
from bs4 import BeautifulSoup
from markdownify import markdownify as to_markdown

from synthetic_council.config import PROCESSED_DIR, RAW_DIR, USER_AGENT

ACCOUNTS_DIR = RAW_DIR / "accounts"
INDEX_PATH = RAW_DIR / "accounts_index.parquet"

MONTHS = {
    m.lower(): i
    for i, m in enumerate(
        [
            "January",
            "February",
            "March",
            "April",
            "May",
            "June",
            "July",
            "August",
            "September",
            "October",
            "November",
            "December",
        ],
        start=1,
    )
}

# "held in Nicosia on Wednesday and Thursday, 4-5 March 2015" — meeting venue
# varies (Frankfurt am Main, Nicosia, ...); the date must be inside the match.
_RANGE_RE = re.compile(
    r"(\d{1,2})(?:\s*[–-]\s*(\d{1,2}))?\s+(" + "|".join(MONTHS) + r")\s+(\d{4})",
    re.IGNORECASE,
)
_HELD_RE = re.compile(
    r"held in [A-Z][\wà-ÿ' .-]{0,40}? on [A-Za-z, ]{0,40}?"
    r"\d{1,2}(?:\s*[–-]\s*\d{1,2})?\s+\w+\s+\d{4}",
)


@dataclass
class AccountsResult:
    n_accounts: int
    n_fetched: int
    n_rate_meetings: int
    n_date_mismatches: int
    first_meeting: str = ""
    last_meeting: str = ""


def find_account_records(foedb: pl.DataFrame) -> pl.DataFrame:
    """Account records from the foedb corpus, one row per account (English doc)."""
    acc = foedb.filter(pl.col("document_urls").list.join(" ").str.contains("/press/accounts/"))
    rows = []
    for r in acc.iter_rows(named=True):
        urls = [u for u in (r["document_urls"] or []) if u.endswith(".en.html")]
        if not urls:
            continue
        rows.append(
            {
                "foedb_id": r["id"],
                "published_at": r["published_at"],
                "title": r["title"],
                "url": urls[0],
            }
        )
    df = pl.DataFrame(rows)
    if df["published_at"].dtype.is_numeric():
        df = df.with_columns(
            pl.from_epoch(pl.col("published_at"), time_unit="s")
            .dt.replace_time_zone("Europe/Berlin")
            .alias("published_at")
        )
    return df.sort("published_at")


def _month_no(name: str) -> int:
    return MONTHS[name.lower()]


def parse_meeting_date(text: str, title: str = "") -> date | None:
    """Decision-day date from the account text (last day of the meeting).

    Anchors, in order (the leading publication-date line must never win):
    1. the opening sentence naming where/when the meeting was held;
    2. the foedb title / "Meeting of …" heading ("Meeting of 22-23 July 2026");
    3. the first date range in the body after skipping the first lines.
    """
    chunks: list[str] = []
    m = _HELD_RE.search(text)
    if m:
        chunks.append(m.group(0))
    for source in (title, text):
        for hm in re.finditer(r"^#{1,3}\s+Meeting of.*$", source, re.MULTILINE):
            chunks.append(hm.group(0))
    chunks.append("\n".join(text.splitlines()[1:6]))
    chunks.append(text)
    for chunk in chunks:
        r = _RANGE_RE.search(chunk)
        if not r:
            continue
        day1, day2, month, year = r.group(1), r.group(2), r.group(3), r.group(4)
        day = int(day2) if day2 else int(day1)
        try:
            return date(int(year), _month_no(month), day)
        except ValueError:
            continue
    return None


def html_to_markdown(html: str) -> str:
    """Main-content markdown: headings, body and the attendees footer kept;
    site chrome (navigation, contact footer, cookie banner) dropped."""
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "nav", "header", "footer", "aside", "noscript"]):
        tag.decompose()
    main = soup.find("main") or soup.body or soup
    md = to_markdown(str(main), heading_style="ATX")
    # trim trailing site chrome that lives inside <main> on some layouts
    for marker in ("\nCONTACT\n", "\nRelated topics\n"):
        i = md.find(marker)
        if i != -1:
            md = md[:i]
    return md.strip() + "\n"


def _full_url(url: str) -> str:
    return url if url.startswith("http") else f"https://www.ecb.europa.eu{url}"


def _decision_dates() -> set[date]:
    path = PROCESSED_DIR / "gc_decisions.parquet"
    if not path.exists():
        return set()
    df = pl.read_parquet(path).select(pl.col("announcement_date").cast(pl.String))
    return {date.fromisoformat(s) for s in df["announcement_date"].to_list()}


def collect(sleep_s: float = 0.3) -> AccountsResult:
    """Fetch every account, convert to markdown, write files + index."""
    foedb_path = RAW_DIR / "foedb_publications.parquet"
    if not foedb_path.exists():
        raise FileNotFoundError(
            f"{foedb_path} missing — run collectors.foedb first (accounts are "
            "enumerated from the publications database)"
        )
    records = find_account_records(pl.read_parquet(foedb_path))
    ACCOUNTS_DIR.mkdir(parents=True, exist_ok=True)
    decision_dates = _decision_dates()

    rows: list[dict] = []
    seen_dates: dict[str, int] = {}
    n_fetched = 0
    mismatches: list[str] = []
    with httpx.Client(
        headers={"User-Agent": USER_AGENT}, timeout=120, follow_redirects=True
    ) as client:
        for r in records.iter_rows(named=True):
            url = _full_url(r["url"])
            resp = client.get(url)
            resp.raise_for_status()
            n_fetched += 1
            md = html_to_markdown(resp.text)
            meeting = parse_meeting_date(md, title=r["title"])
            if meeting is None:
                raise ValueError(f"meeting date not found in account {url}")
            pub_date = r["published_at"].date()
            # a few accounts cover non-rate meetings (e.g. the 7 July 2021
            # strategy-review meeting): flagged, kept, excluded from the
            # decision-anchored pipeline downstream
            is_rate = meeting in decision_dates
            mismatch = ""
            if decision_dates and not is_rate:
                nearest = min(decision_dates, key=lambda d: abs((d - meeting).days))
                gap = abs((nearest - meeting).days)
                if gap > 35:
                    mismatch = f"no announcement date within 35 days (nearest {nearest})"
                else:
                    mismatch = f"not an announcement date (nearest {nearest}, {gap}d)"
                mismatches.append(f"{meeting} ({url}): {mismatch}")
            seen_dates[str(meeting)] = seen_dates.get(str(meeting), 0) + 1
            dup = seen_dates[str(meeting)] > 1
            if dup:
                print(f"WARNING duplicate meeting date {meeting} for {url}")
            stem = f"{meeting}_account" + ("_alt" if dup else "")
            out = ACCOUNTS_DIR / f"{stem}.md"
            front = (
                "---\n"
                f"source_url: {url}\n"
                f"publication_date: {pub_date}\n"
                f"meeting_date: {meeting}\n"
                "---\n\n"
            )
            out.write_text(front + md, encoding="utf-8")
            rows.append(
                {
                    "meeting_date": str(meeting),
                    "publication_date": str(pub_date),
                    "title": r["title"],
                    "url": url,
                    "file": str(out.relative_to(RAW_DIR)),
                    "is_rate_meeting": is_rate,
                    "n_chars": out.stat().st_size,
                    "date_check": mismatch or "ok",
                }
            )
            time.sleep(sleep_s)

    index = pl.DataFrame(rows).sort("meeting_date")
    index.write_parquet(INDEX_PATH)
    index.write_csv(RAW_DIR / "accounts_index.csv")
    for msg in mismatches:
        print(f"WARNING date mismatch: {msg}")
    meetings = sorted(date.fromisoformat(s) for s in index["meeting_date"].to_list())
    return AccountsResult(
        n_accounts=index.height,
        n_fetched=n_fetched,
        n_rate_meetings=int(index["is_rate_meeting"].sum()),
        n_date_mismatches=len(mismatches),
        first_meeting=str(meetings[0]) if meetings else "",
        last_meeting=str(meetings[-1]) if meetings else "",
    )


def refresh_index_flags() -> int:
    """Recompute is_rate_meeting/date_check against the current decisions
    calendar, without refetching (run after rebuilding gc_decisions)."""
    idx = pl.read_parquet(INDEX_PATH)
    decision_dates = _decision_dates()
    if not decision_dates:
        return 0

    def _check(d: str) -> tuple[bool, str]:
        day = date.fromisoformat(d)
        if day in decision_dates:
            return True, "ok"
        nearest = min(decision_dates, key=lambda x: abs((x - day).days))
        gap = abs((nearest - day).days)
        label = "no announcement date within 35 days" if gap > 35 else "not an announcement date"
        return False, f"{label} (nearest {nearest}, {gap}d)"

    flags = [_check(d) for d in idx["meeting_date"].to_list()]
    idx = idx.with_columns(
        pl.Series("is_rate_meeting", [f[0] for f in flags]),
        pl.Series("date_check", [f[1] for f in flags]),
    )
    idx.write_parquet(INDEX_PATH)
    idx.write_csv(RAW_DIR / "accounts_index.csv")
    return int(idx["is_rate_meeting"].sum())


if __name__ == "__main__":
    print(collect())
