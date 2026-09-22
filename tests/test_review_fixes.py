"""Tests for the bugs found in the 2026-08-22 data review."""

from __future__ import annotations

from datetime import date

import polars as pl

from synthetic_council.collectors.mpd_rounds import HEADLINE, round_date
from synthetic_council.collectors.vintage_growth import asof_growth, vintage_growth


def test_mpd_round_decoding():
    # {W,G,S,A}{yy} = March/June/September/December
    assert round_date("W23") == "2023-03-01"
    assert round_date("G22") == "2022-06-01"
    assert round_date("S22") == "2022-09-01"
    assert round_date("A22") == "2022-12-01"
    assert round_date("W00") == "2000-03-01"
    assert round_date("A99") is None  # legacy/undatable
    assert round_date("XX5") is None


def test_headline_items_are_official_codes():
    # CL_PD_ITEM: HIC=HICP, YER=real GDP, URX=unemployment (NOT DDR/PCR)
    assert HEADLINE == ["HIC", "YER", "URX"]


def test_announcement_date_from_url_slug():
    """The off-by-one fix: dates must come from URL slugs, not pre-2015 batch
    timestamps (22:00/23:00 CET the evening before)."""
    from synthetic_council.collectors.decisions import _slug_date

    url = "https://www.ecb.europa.eu/press/pr/date/2008/html/pr081008.en.html"
    assert str(_slug_date(url)) == "2008-10-08"
    assert str(_slug_date(".../pr150122_1.en.html")) == "2015-01-22"
    # pre-2000 releases: yy > 90 -> 19yy
    assert str(_slug_date(".../pr990408.en.html")) == "1999-04-08"
    # ecb.mp slugs are authoritative for ALL eras: 2016-2018 unhashed URLs
    # also carry evening batch timestamps (ecb.mp170427 -> 2017-04-27, not the
    # 2017-04-26 timestamp); hashed modern URLs simply match their timestamps.
    u2 = "https://www.ecb.europa.eu/press/pr/date/2023/html/ecb.mp231214~9846e62f62.en.html"
    assert str(_slug_date(u2)) == "2023-12-14"
    u3 = "https://www.ecb.europa.eu/press/pr/date/2017/html/ecb.mp170427.en.html"
    assert str(_slug_date(u3)) == "2017-04-27"


def test_vintage_growth_asof_picks_newest_period():
    """Regression: as-of must take the newest (revdate, period), not just the
    newest revdate — otherwise meetings resolve to stale reference periods."""
    events = pl.DataFrame(
        {
            "period": ["2005-Q2", "2014-Q3", "2014-Q3"],
            "revdate": ["2008-08-06", "2014-10-17", "2015-01-15"],
            "growth": [1.5, 0.8, 0.79],
        }
    ).with_columns(pl.col("revdate").str.to_date())
    meetings = pl.DataFrame(
        {"announcement_date": [date(2015, 1, 21), date(2008, 10, 8)]},
        schema={"announcement_date": pl.Date},
    )
    out = asof_growth(events, meetings)
    assert out.height == 2
    jan2015 = out.filter(pl.col("announcement_date") == date(2015, 1, 21))
    assert jan2015["period"][0] == "2014-Q3"
    assert abs(jan2015["growth"][0] - 0.79) < 1e-9
    oct2008 = out.filter(pl.col("announcement_date") == date(2008, 10, 8))
    assert oct2008["period"][0] == "2005-Q2"


def test_vintage_growth_uses_level_as_known_at_t():
    """YoY at release event (p, t) divides by the lagged level as known at t —
    not by the vintage-mate row (which may have been revised on its own)."""
    releases = pl.DataFrame(
        {
            "period": ["2007-Q2", "2008-Q2", "2008-Q2"],
            "value": [100.0, 103.0, 104.0],
            "revdate": ["2007-08-01", "2008-09-03", "2008-11-05"],
        }
    ).with_columns(pl.col("revdate").str.to_date())
    events = vintage_growth(releases, "period", "value", lag=4)
    first = events.filter(pl.col("revdate") == date(2008, 9, 3))
    assert first.height == 1
    assert abs(first["growth"][0] - 3.0) < 1e-9  # 103/100 - 1
    revised = events.filter(pl.col("revdate") == date(2008, 11, 5))
    assert abs(revised["growth"][0] - 4.0) < 1e-9  # 104/100 - 1
