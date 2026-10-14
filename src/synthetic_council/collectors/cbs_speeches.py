"""Collector: CBS dataset — Campiglio, Deyris, Romelli & Scalisi (2025).

35,487 speeches by 131 central banks, 1986–2023 (BIS hub + scraped NCB
websites + archives; non-English speeches machine-translated to English;
downloadable at https://cbspeeches.com/). Closes the governor-speech gap
at NCBs we cannot harvest directly (JS-rendered, bot-blocked or
unreachable — see docs/data-sources/ncb-websites.md) **for 2015–2023
only**: the dataset ends 2023-12, so 2024→ stays uncovered there.

Matching: rows with Role == "Governor" at euro-area institutions are
resolved against the Wayback membership tenures. Speaker names in the CSV
have their diacritic letters deleted outright ("Mrio Centeno",
"Boris Vuji" = Vujčić, "Gediminas imkus" = Šimkus), so matching runs
skeleton-exact → surname-exact with a compatible first name → surname
subsequence (CBS surname ⊆ roster surname, ≤2 letters shorter), always
under tenure + country checks. Same-person spelling variants
("Giannis"/"Yannis Stournaras") collapse on (person, day).

Rows already covered by the primary sources (ECB CSV, BIS hub, NCB
websites) are dropped on (speaker, date) — CBS never overrides a primary
source.

Output: data/raw/speeches/cbs_speeches.parquet
(date, speaker, author_text, title, url, contents, source='cbs',
language, translated) — the two extra columns are ignored by the union.

License: free for academic research and personal non-commercial use;
redistribution requires the authors' consent — the 686 MB CSV stays in
data/raw (gitignored) and is never committed. Cite Campiglio, Deyris,
Romelli & Scalisi (2025), "Warning words in a warming world: Central bank
communication and climate change", European Economic Review 178.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import date, timedelta

import httpx
import polars as pl

from synthetic_council.collectors.persons import canonicalise
from synthetic_council.config import RAW_DIR, USER_AGENT

CBS_CSV_URL = (
    "https://www.dropbox.com/scl/fi/n2tsmswnk4ymm3zz0wteg/CBS_dataset_v1.0.csv"
    "?rlkey=sdxk6b9xpid0xe7w7s4nm6s87&dl=1"
)
CSV_PATH = RAW_DIR / "CBS_dataset_v1.0.csv"
OUT_PATH = RAW_DIR / "speeches" / "cbs_speeches.parquet"

DATE_FROM = "2015"
MIN_CHARS = 400
TENURE_GRACE_DAYS = 180  # mirrors ncb_websites

# euro-area NCBs (+ BGR whose governor is in our roster) as CBS Country
# ISO-3 codes. ECB-institution rows are excluded: they are EB-member
# speeches already covered (with correct dates) by the official ECB CSV —
# CBS copies carry bis.org/review publication dates instead.
EURO_ISO3 = {
    "AUT": "AT",
    "BEL": "BE",
    "BGR": "BG",
    "CYP": "CY",
    "DEU": "DE",
    "ESP": "ES",
    "EST": "EE",
    "FIN": "FI",
    "FRA": "FR",
    "GRC": "GR",
    "HRV": "HR",
    "IRL": "IE",
    "ITA": "IT",
    "LTU": "LT",
    "LUX": "LU",
    "LVA": "LV",
    "MLT": "MT",
    "NLD": "NL",
    "PRT": "PT",
    "SVK": "SK",
    "SVN": "SI",
}

_EXISTING_PATHS = (
    RAW_DIR / "speeches" / "speeches.parquet",
    RAW_DIR / "speeches" / "bis_speeches.parquet",
    RAW_DIR / "speeches" / "ncb_web_speeches.parquet",
)


def download(path=CSV_PATH) -> None:
    """Stream the CBS CSV to ``path`` (~686 MB); skipped when present."""
    if path.exists() and path.stat().st_size > 100_000_000:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with (
        httpx.Client(
            headers={"User-Agent": USER_AGENT}, timeout=120, follow_redirects=True
        ) as client,
        client.stream("GET", CBS_CSV_URL) as r,
        open(path, "wb") as fh,
    ):
        r.raise_for_status()
        for chunk in r.iter_bytes(1 << 20):
            fh.write(chunk)


# --- speaker resolution -------------------------------------------------------

Iso2 = str
Variant = tuple[str, str, str, str, Iso2, str, str]
# (person, skeleton full name, surname, first name, country ISO2, start, end)


def _skeleton(name: str) -> str:
    """Lowercase ASCII letters only; single-letter tokens (middle initials)
    dropped. Diacritics folded ("Boštjan" → "bostjan") so undamaged roster
    names compare against the CSV's mangled spellings."""
    n = unicodedata.normalize("NFKD", canonicalise(name))
    n = "".join(c for c in n if not unicodedata.combining(c))
    n = re.sub(r"[^a-z ]", "", n.lower())
    return " ".join(w for w in n.split() if len(w) > 1)


def _is_subseq(small: str, big: str) -> bool:
    it = iter(big)
    return all(c in it for c in small)


def _levenshtein_within(a: str, b: str, cap: int = 2) -> bool:
    """True when edit distance(a, b) <= cap (early exit above the cap)."""
    if abs(len(a) - len(b)) > cap:
        return False
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, start=1):
        cur = [i]
        for j, cb in enumerate(b, start=1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        if min(cur) > cap:
            return False
        prev = cur
    return prev[-1] <= cap


def _first_compatible(cbs_first: str, roster_first: str) -> bool:
    """First names of the same person, allowing the CSV's deletions
    ("Mrio"/"Mario", "Botjan"/"Bostjan") and transliteration variants
    ("Giannis"/"Yannis"). Only consulted together with a surname match,
    tenure and country, so these tolerant rules stay safe."""
    if cbs_first == roster_first:
        return True
    if roster_first.startswith(cbs_first) or cbs_first.startswith(roster_first):
        return True
    if _is_subseq(cbs_first, roster_first) and len(roster_first) - len(cbs_first) <= 2:
        return True
    return len(cbs_first) >= 5 and _levenshtein_within(cbs_first, roster_first)


def _variants() -> list[Variant]:
    memberships = pl.read_parquet(RAW_DIR / "gc_memberships_wayback.parquet")
    out: list[Variant] = []
    for r in memberships.iter_rows(named=True):
        skel = _skeleton(r["person"])
        tokens = skel.split()
        if not tokens:
            continue
        out.append(
            (r["person"], skel, tokens[-1], tokens[0], r["country"] or "", r["start"], r["end"])
        )
    return out


def resolve_speaker(name: str, day: str, iso2: str, variants: list[Variant]) -> str | None:
    """Roster person for a CBS Authorname, or None.

    Passes: full-skeleton exact → surname exact with compatible first name
    → CBS surname a subsequence of the roster surname (≤2 letters shorter).
    Every candidate must cover ``day`` (tenure ± grace) and match ``iso2``
    when both countries are known.
    """
    skel = _skeleton(name)
    tokens = skel.split()
    if not tokens:
        return None
    first, surname = tokens[0], tokens[-1]

    def eligible(v: Variant) -> bool:
        _p, _f, _l, _fi, ctry, start, end = v
        end_grace = str(date.fromisoformat(end) + timedelta(days=TENURE_GRACE_DAYS))
        return start <= day <= end_grace and (not ctry or not iso2 or ctry == iso2)

    cands = [v for v in variants if eligible(v)]
    for person, full, *_ in cands:
        if skel == full:
            return person
    for person, _full, last, roster_first, *_ in cands:
        if surname == last and _first_compatible(first, roster_first):
            return person
    for person, _full, last, roster_first, *_ in cands:
        if (
            0 < len(last) - len(surname) <= 2
            and _is_subseq(surname, last)
            and _first_compatible(first, roster_first)
        ):
            return person
    return None


# --- build ---------------------------------------------------------------------


def _existing_pairs(paths=tuple(_EXISTING_PATHS)) -> set[tuple[str, str]]:
    """(speaker, day) already covered by the primary speech sources."""
    pairs: set[tuple[str, str]] = set()
    for p in paths:
        if not p.exists():
            continue
        df = pl.read_parquet(p)
        if "speakers" in df.columns:  # ECB all-speeches CSV (uncanonicalised)
            df = df.with_columns(
                pl.col("speakers").str.split("|").list.first().alias("speaker"),
                pl.col("date").cast(pl.String),
            ).with_columns(pl.col("speaker").map_elements(canonicalise, return_dtype=pl.String))
        if df.height:
            pairs.update(zip(df["speaker"], df["date"], strict=True))
    return pairs


def build(
    csv_path=CSV_PATH,
    out_path=OUT_PATH,
    variants: list[Variant] | None = None,
    existing_paths=tuple(_EXISTING_PATHS),
) -> pl.DataFrame:
    """Filter CBS to roster governors, dedup, and write the parquet."""
    variants = variants if variants is not None else _variants()
    df = (
        pl.scan_csv(csv_path, infer_schema_length=0, null_values=["", "NA"])
        .filter(
            pl.col("Role") == "Governor",
            pl.col("Country").is_in(list(EURO_ISO3)),
            pl.col("Date").str.slice(0, 4) >= DATE_FROM,
            pl.col("text").is_not_null(),
        )
        .with_columns(pl.col("Date").str.slice(0, 10).alias("day"))
        .collect()
    )
    persons = [
        resolve_speaker(r["Authorname"], r["day"], EURO_ISO3.get(r["Country"], ""), variants)
        for r in df.iter_rows(named=True)
    ]
    df = (
        df.with_columns(pl.Series("person", persons))
        .filter(
            pl.col("person").is_not_null(),
            pl.col("text").str.len_bytes() >= MIN_CHARS,
        )
        .with_columns(
            pl.col("text_original").is_not_null().alias("translated"),
            pl.col("text").str.len_bytes().alias("n"),
        )
    )
    # same-person spelling variants double-count speeches: one per (person,
    # day), original English preferred, then the longer text
    df = df.sort(
        ["person", "day", "translated", "n"], descending=[False, False, False, True]
    ).unique(subset=["person", "day"], keep="first", maintain_order=True)
    have = _existing_pairs(existing_paths)
    keep = [(p, d) not in have for p, d in zip(df["person"], df["day"], strict=True)]
    out = (
        df.filter(pl.Series(keep))
        .select(
            pl.col("day").alias("date"),
            pl.col("person").alias("speaker"),
            pl.col("Authorname").alias("author_text"),
            pl.col("Title").fill_null("").alias("title"),
            pl.col("URL").fill_null("").alias("url"),
            pl.col("text").alias("contents"),
            pl.lit("cbs").alias("source"),
            pl.col("Language").fill_null("").alias("language"),
            pl.col("translated"),
        )
        .sort("date")
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out.write_parquet(out_path)
    return out


if __name__ == "__main__":
    download()
    df = build()
    print(
        f"CBS speeches: {df.height} | persons={df['speaker'].n_unique()} | "
        f"span={df['date'].min()}..{df['date'].max()} | "
        f"translated={df['translated'].sum()}"
    )
    print(df.group_by("speaker").len().sort("len", descending=True).head(15))
