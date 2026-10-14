"""Collector: press coverage of GC-member remarks via TinyFish (2024 →).

Fills the strict-window speech gaps BIS and CBS cannot: for every GC
monetary policy meeting from 2024-01, for every participating member with
**no speech in the (prev_gc, gc] window**, search the news for coverage of
their remarks (speeches, interviews, testimony) and fetch the articles.
TinyFish's Search endpoint enforces the date range server-side
(``after_date``/``before_date``); the article's own ``published_date``
(from the Fetch response) is the authoritative clip — undated articles
are dropped. Reuters/Bloomberg direct pages often refuse fetchers (HTTP
401); syndicated copies surface via additional search results and are
fetched opportunistically, failures skipped.

Press rows only ever FILL gaps: ``speech_union.build`` prefers any real
speech over press in the same window, and rows colliding with an existing
source on (speaker, date) are dropped here.

Endpoints (free tier): GET https://api.search.tinyfish.ai (X-API-Key,
``domain_type=news`` adds publisher/date fields) and POST
https://api.fetch.tinyfish.ai (``{"urls": [...]}`` -> markdown text).
Key: ``TINYFISH_API_KEY`` in the environment (export ``.env.local``).
Raw search responses are archived per query under
``data/raw/speeches/press_searches/`` for auditability.

Output:
- data/raw/speeches/press_speeches.parquet  (date, speaker, author_text,
  title, url, contents, source='press', publisher, language)
- data/raw/speeches/press_fetched.parquet   (url/search-pair -> outcome;
  the resume index — ``search:{person}:{gc}`` marks a fully processed
  (member, window) pair)
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time

import httpx
import polars as pl

from synthetic_council.collectors.cbs_speeches import _first_compatible, _skeleton
from synthetic_council.config import PROCESSED_DIR, RAW_DIR, USER_AGENT

SEARCH_URL = "https://api.search.tinyfish.ai"
FETCH_URL = "https://api.fetch.tinyfish.ai"
OUT_PATH = RAW_DIR / "speeches" / "press_speeches.parquet"
FETCHED_PATH = RAW_DIR / "speeches" / "press_fetched.parquet"
SEARCH_DIR = RAW_DIR / "speeches" / "press_searches"

DATE_FROM = "2024-01-01"
MAX_FETCH_PER_PAIR = 6  # candidate articles fetched per (member, window)
KEEP_PER_PAIR = 2  # articles kept per (member, window)
MIN_CHARS = 600
SLEEP_S = 0.3

_URL_DATE_RES = (
    re.compile(r"[-/](\d{4})-(\d{2})-(\d{2})(?:/|$)"),  # …-knot-2024-04-29/
    re.compile(r"/(\d{4})/(\d{2})/(\d{2})/"),  # wordpress /2024/05/27/
)


def tinyfish_search(
    query: str, after_date: str, before_date: str, domain_type: str = "news"
) -> dict:
    """One TinyFish news search; returns the raw response dict."""
    key = os.environ.get("TINYFISH_API_KEY", "")
    if not key:
        raise RuntimeError("TINYFISH_API_KEY is not set (export .env.local)")
    r = httpx.get(
        SEARCH_URL,
        params={
            "query": query,
            "domain_type": domain_type,
            "after_date": after_date,
            "before_date": before_date,
        },
        headers={"X-API-Key": key, "User-Agent": USER_AGENT},
        timeout=60,
    )
    r.raise_for_status()
    return r.json()


def tinyfish_fetch(urls: list[str]) -> dict:
    """Fetch pages (batch); returns {"results": [...], "errors": [...]}."""
    key = os.environ.get("TINYFISH_API_KEY", "")
    if not key:
        raise RuntimeError("TINYFISH_API_KEY is not set (export .env.local)")
    r = httpx.post(
        FETCH_URL,
        json={"urls": urls},
        headers={"X-API-Key": key, "User-Agent": USER_AGENT},
        timeout=180,
    )
    r.raise_for_status()
    return r.json()


def _meeting_windows(date_from: str) -> list[tuple[str | None, str]]:
    """(prev_gc, gc) pairs for meetings on/after ``date_from``."""
    meetings = (
        pl.read_parquet(PROCESSED_DIR / "gc_decisions.parquet")
        .select(pl.col("announcement_date").cast(pl.String).alias("d"))
        .sort("d")["d"]
        .to_list()
    )
    out = []
    for i, d in enumerate(meetings):
        if d >= date_from:
            out.append((meetings[i - 1] if i else None, d))
    return out


def article_date(published: str | None, url: str) -> str | None:
    """ISO day for an article: the fetcher's published_date wins, else a
    date embedded in the URL (many news slugs carry one)."""
    if published:
        m = re.match(r"(\d{4})-(\d{2})-(\d{2})", published)
        if m:
            return m.group(0)
    for rx in _URL_DATE_RES:
        m = rx.search(url)
        if m:
            return "-".join(m.groups())
    return None


def _search_plan(person: str) -> list[tuple[str, str]]:
    """(query, domain_type) pairs, highest-recall first.

    Live-verified on the 2024-04-11 -> 06-06 window: unquoted short queries
    beat exact-phrase ones on the news index (the ``OR`` tails halved
    recall), and the ``web`` index is far richer than ``news`` for
    small-country governors (Centeno: 0 news vs 10 web results). Web
    results lack the publisher/date fields — the fetcher's
    ``published_date`` remains the authoritative window clip.
    """
    surname = _skeleton(person).split()[-1]
    return [
        (f"{person} ECB", "news"),
        (f"{person} ECB", "web"),
        (f"{surname} ECB rate", "news"),
        (f"{surname} ECB rate", "web"),
    ]


# title tokens that are institutional/connective rather than personal names —
# "ECB's Knot: …" must pass while "Lagarde and Knot…" must not
_TITLE_NOISE = {
    "ecb",
    "ecbs",
    "euro",
    "eurozone",
    "area",
    "bank",
    "banks",
    "central",
    "the",
    "of",
    "and",
    "or",
    "to",
    "mr",
    "ms",
    "mrs",
    "dr",
    "governor",
    "president",
    "chief",
    "economist",
    "says",
    "said",
    "in",
    "on",
    "for",
}

# never fetchable as press evidence: social platforms and video pages
_DOMAIN_BLOCKLIST = (
    "x.com/",
    "twitter.com/",
    "youtube.com/",
    "facebook.com/",
    "linkedin.com/",
    "instagram.com/",
    "tiktok.com/",
)

# a policy signal must appear in title+snippet before we spend a fetch —
# keeps court/political scandal coverage of a governor out of the corpus
# ("ECB" alone is NOT a signal: "represents Malta at ECB's meeting" is not
# monetary remarks). A few non-English stems keep foreign-language wires.
_POLICY_HINTS = (
    "rate",
    "inflation",
    "monetary",
    "interest",
    "easing",
    "tightening",
    "hike",
    "rente",
    "zins",
    "tasso",
    "taux",
    "juro",
    "stope",
)


def _has_policy_signal(title: str, snippet: str) -> bool:
    blob = f"{title} {snippet}".lower()
    return any(h in blob for h in _POLICY_HINTS)


def _is_subject(title: str, head: str, person: str, others: tuple[str, ...] = ()) -> bool:
    """The article is about the member.

    Two gates: (1) the headline names them — surname present, no other GC
    member headlining the story earlier, any personal-name-like token
    before the surname compatible with the first name ("ECB's Knot"
    passes, "Mark Knot" doesn't); (2) failing the headline, wire stories
    that headline the news instead of the speaker are accepted when the
    member is named prominently at the top of the article body.
    """
    t_tokens = _skeleton(title).split()
    h_tokens = _skeleton(head).split()
    p_tokens = _skeleton(person).split()
    if not p_tokens:
        return False
    last = p_tokens[-1]
    for other in others:
        o_last = _skeleton(other).split()[-1]
        if o_last == last:
            continue
        if o_last in t_tokens and (
            last not in t_tokens or t_tokens.index(o_last) < t_tokens.index(last)
        ):
            return False  # a different member headlines the story
    if last in t_tokens:
        before = [w for w in t_tokens[: t_tokens.index(last)] if w not in _TITLE_NOISE]
        if not before or any(_first_compatible(w, p_tokens[0]) for w in before):
            return True
    # headline is about the news, not the speaker: the member is named near
    # the head of the body — first name adjacent to the surname, or the
    # full name anywhere in the head
    if last in h_tokens:
        i = h_tokens.index(last)
        if " ".join(p_tokens) in " ".join(h_tokens) or any(
            _first_compatible(w, p_tokens[0]) for w in h_tokens[max(0, i - 2) : i]
        ):
            return True
    return False


def collect(
    search_fn=tinyfish_search,
    fetch_fn=tinyfish_fetch,
    date_from: str = DATE_FROM,
    out_path=None,
    fetched_path=None,
    search_dir=None,
    windows=None,
    corpus: pl.DataFrame | None = None,
    roster: pl.DataFrame | None = None,
    existing_pairs: set[tuple[str, str]] | None = None,
) -> pl.DataFrame:
    """Search + fetch press coverage for every uncovered (member, window);
    resumable via the fetched index. Collaborators are plain parameters
    (fakes in tests, defaults in production)."""
    from synthetic_council.collectors.cbs_speeches import _existing_pairs
    from synthetic_council.speech_union import _roster, union_corpus

    out_path = out_path or OUT_PATH
    fetched_path = fetched_path or FETCHED_PATH
    search_dir = search_dir or SEARCH_DIR
    windows = windows if windows is not None else _meeting_windows(date_from)
    corpus = corpus if corpus is not None else union_corpus().filter(pl.col("source") != "press")
    roster = (
        roster
        if roster is not None
        else _roster(pl.read_parquet(RAW_DIR / "gc_memberships_wayback.parquet"))
    )
    have = existing_pairs if existing_pairs is not None else _existing_pairs()

    existing = pl.read_parquet(out_path) if out_path.exists() else pl.DataFrame()
    rows: list[dict] = existing.to_dicts() if existing.height else []
    fetched_df = pl.read_parquet(fetched_path) if fetched_path.exists() else None
    fetched: dict[str, str] = (
        {r["url"]: r["outcome"] for r in fetched_df.iter_rows(named=True)}
        if fetched_df is not None
        else {}
    )
    others = tuple(sorted(set(roster["person"].to_list())))  # for headline rejection

    def _flush() -> None:
        df = pl.DataFrame(
            rows,
            schema={
                "date": pl.String,
                "speaker": pl.String,
                "author_text": pl.String,
                "title": pl.String,
                "url": pl.String,
                "contents": pl.String,
                "source": pl.String,
                "publisher": pl.String,
                "language": pl.String,
            },
        ).unique(subset=["speaker", "date", "url"])
        out_path.parent.mkdir(parents=True, exist_ok=True)
        df.write_parquet(out_path)
        pl.DataFrame({"url": list(fetched), "outcome": list(fetched.values())}).write_parquet(
            fetched_path
        )

    n_searches = 0
    for prev, gc in windows:
        win = corpus.filter(
            (pl.col("date") <= pl.lit(gc).str.to_date("%Y-%m-%d"))
            & (pl.col("date") > pl.lit(prev).str.to_date("%Y-%m-%d") if prev else pl.lit(True))
        )
        covered = set(win["speaker"].to_list())
        attendees = roster.filter(pl.col("meeting_date") == gc)["person"].to_list()
        todo = [p for p in attendees if p not in covered]
        window_touched = False
        for person in todo:
            pair_key = f"search:{person}:{gc}"
            if fetched.get(pair_key) == "done":
                continue
            window_touched = True
            kept = 0
            seen_urls: set[str] = set()
            for query, domain in _search_plan(person):
                if kept >= KEEP_PER_PAIR:
                    break
                time.sleep(SLEEP_S)
                try:
                    resp = search_fn(query, prev or date_from, gc, domain_type=domain)
                except httpx.HTTPError:
                    continue
                n_searches += 1
                search_dir.mkdir(parents=True, exist_ok=True)
                slug = hashlib.sha1(f"{person}|{gc}|{query}|{domain}".encode()).hexdigest()[:16]
                (search_dir / f"{gc}_{slug}.json").write_text(
                    json.dumps(resp, ensure_ascii=False, indent=1)
                )
                candidates = [
                    r
                    for r in resp.get("results", [])
                    if r.get("url") not in seen_urls
                    and not any(b in r.get("url", "") for b in _DOMAIN_BLOCKLIST)
                    and _has_policy_signal(r.get("title", ""), r.get("snippet", ""))
                ]
                seen_urls.update(r.get("url", "") for r in resp.get("results", []))
                to_fetch = [c["url"] for c in candidates][:MAX_FETCH_PER_PAIR]
                if not to_fetch:
                    continue
                time.sleep(SLEEP_S)
                try:
                    fr = fetch_fn(to_fetch)
                except httpx.HTTPError:
                    continue
                by_url = {r["url"]: r for r in fr.get("results", [])}
                for e in fr.get("errors", []):
                    fetched[e["url"]] = f"fetch-error:{e.get('status', e.get('error'))}"
                for c in candidates:
                    if c["url"] not in by_url or kept >= KEEP_PER_PAIR:
                        continue
                    item = by_url[c["url"]]
                    day = article_date(
                        item.get("published_date"), item.get("final_url") or c["url"]
                    )
                    text = (item.get("text") or "").strip()
                    if not day or not (prev or date_from) < day <= gc:
                        fetched[c["url"]] = "outside-window-or-undated"
                        continue
                    if len(text) < MIN_CHARS or not _is_subject(
                        item.get("title") or c["title"],
                        text[:1500],
                        person,
                        others,
                    ):
                        fetched[c["url"]] = "not-subject-or-too-short"
                        continue
                    if (person, day) in have:
                        fetched[c["url"]] = "covered-by-primary-source"
                        continue
                    rows.append(
                        {
                            "date": day,
                            "speaker": person,
                            "author_text": c.get("publisher") or c.get("site_name") or "",
                            "title": (item.get("title") or c["title"] or "")[:300],
                            "url": c["url"],
                            "contents": text,
                            "source": "press",
                            "publisher": c.get("publisher") or c.get("site_name") or "",
                            "language": item.get("language") or "",
                        }
                    )
                    fetched[c["url"]] = f"kept:{person}"
                    kept += 1
            fetched[pair_key] = "done"
        if window_touched:  # crash-resilient resume at window granularity
            _flush()
    _flush()
    df = pl.read_parquet(out_path).unique(subset=["speaker", "date", "url"]).sort("date")
    print(
        f"press speeches: {df.height} | persons={df['speaker'].n_unique()} | "
        f"searches={n_searches} | span={df['date'].min()}..{df['date'].max()}"
    )
    return df


if __name__ == "__main__":
    collect()
