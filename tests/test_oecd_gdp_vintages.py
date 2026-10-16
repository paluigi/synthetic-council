"""OECD GDP vintage collector tests (pure logic, no network)."""

from __future__ import annotations

import datetime as dt

import polars as pl
import pytest

from synthetic_council.collectors import oecd_gdp_vintages as og
from synthetic_council.collectors.country_macro import _gdp_oecd_splice


def test_edition_to_avail_first_of_next_month():
    assert og.edition_to_avail("202608") == dt.date(2026, 9, 1)
    assert og.edition_to_avail("202204") == dt.date(2022, 5, 1)
    assert og.edition_to_avail("199912") == dt.date(2000, 1, 1)  # December rollover


def test_iso3_mapping_covers_oecd_ea_members():
    assert og.OECD_TO_ESTAT["GRC"] == "EL"  # the one trap: Eurostat uses EL
    assert len(og.OECD_TO_ESTAT) == 17
    # EA aggregate and the three non-OECD members stay on the Eurostat path
    assert not ({"EA", "CY", "MT", "HR"} & set(og.OECD_TO_ESTAT.values()))


def test_within_edition_yoy(tmp_path):
    """y/y is computed from levels in the SAME edition only, lag = same
    quarter previous year (regression: an inverted lag joined q against
    q+4, producing future-over-past ratios)."""
    og.RAW_ALL_CSV = tmp_path / "all_editions.csv"
    pl.DataFrame(
        {
            "geo": ["DE", "DE", "DE", "IT", "DE", "DE"],
            "edition": ["202306"] * 4 + ["202406", "202406"],
            "period": ["2021-Q2", "2022-Q2", "2023-Q2", "2023-Q2", "2021-Q2", "2022-Q2"],
            "level": [100.0, 110.0, 115.0, 50.0, 105.0, 110.0],
        }
    ).write_csv(og.RAW_ALL_CSV)
    vtg = og.build_vintages()
    v = {
        (r["edition"], r["geo"], r["period"]): r["value"]
        for r in vtg.iter_rows(named=True)
    }
    assert v[("202306", "DE", "2022-Q2")] == pytest.approx(10.0)  # 110/100
    assert v[("202306", "DE", "2023-Q2")] == pytest.approx(100 * (115 / 110 - 1))
    # edition 202406 revised the base year: growth is computed within it,
    # never mixing levels across editions
    assert v[("202406", "DE", "2022-Q2")] == pytest.approx(100 * (110 / 105 - 1))
    # IT 2023-Q2 has no 2022-Q2 in the edition -> no y/y, no cross-geo fill
    assert ("202306", "IT", "2023-Q2") not in v


def test_asof_latest_visible_edition_no_lookahead():
    """An edition is visible only from the 1st of the month after its label;
    at a meeting the newest VISIBLE edition's newest quarter is shown."""
    vtg = pl.DataFrame(
        {
            "edition": ["202204", "202204", "202206", "202206"],
            "avail_from": [dt.date(2022, 5, 1)] * 2 + [dt.date(2022, 7, 1)] * 2,
            "geo": ["DE", "DE", "DE", "DE"],
            "period": ["2021-Q3", "2021-Q4", "2021-Q4", "2022-Q1"],
            "value": [2.0, 1.8, 1.9, 3.7],
        }
    )
    dates = pl.DataFrame(
        {"announcement_date": [dt.date(2022, 4, 7), dt.date(2022, 6, 9), dt.date(2022, 7, 21)]}
    )
    out = og.asof_gdp(dates, vtg).sort("announcement_date")
    by_date = dict(
        zip(out["announcement_date"].to_list(), out["ref_period"].to_list(), strict=True)
    )
    # April: no edition visible yet (first avail 2022-05-01)
    assert dt.date(2022, 4, 7) not in by_date
    # June: edition 202204 -> newest quarter 2021-Q4
    assert by_date[dt.date(2022, 6, 9)] == "2021-Q4"
    # July: edition 202206 -> newest quarter 2022-Q1, vintage value 3.7
    assert by_date[dt.date(2022, 7, 21)] == "2022-Q1"
    jul = out.filter(pl.col("announcement_date") == dt.date(2022, 7, 21))
    assert jul["value"][0] == 3.7


def _row(d, geo, ref, val):
    return {
        "announcement_date": d,
        "geo": geo,
        "indicator": "gdp_yoy",
        "ref_period": ref,
        "value": val,
    }


def test_gdp_splice_rules():
    d1, d2 = dt.date(2022, 7, 21), dt.date(2015, 1, 22)
    oecd = pl.DataFrame(
        [
            _row(d1, "DE", "2022-Q1", 3.7),  # vintage value at the meeting
            _row(d1, "FR", "2022-Q1", 4.5),
        ]
    )
    fallback = pl.DataFrame(
        [
            _row(d1, "DE", "2022-Q1", 3.8),  # same period: OECD wins
            _row(d1, "FR", "2022-Q2", 4.1),  # newer flash quarter: Eurostat wins
            _row(d1, "CY", "2022-Q1", 5.0),  # no OECD coverage: Eurostat wins
            _row(d2, "DE", "2014-Q3", 1.4),  # pre-OECD meeting: Eurostat wins
        ]
    )
    out = _gdp_oecd_splice(oecd, fallback)
    got = {
        (r["announcement_date"], r["geo"]): (r["ref_period"], r["value"])
        for r in out.iter_rows(named=True)
    }
    assert got[(d1, "DE")] == ("2022-Q1", 3.7)
    assert got[(d1, "FR")] == ("2022-Q2", 4.1)
    assert got[(d1, "CY")] == ("2022-Q1", 5.0)
    assert got[(d2, "DE")] == ("2014-Q3", 1.4)
    assert out.height == 4  # one row per (date, geo)


def test_gdp_splice_empty_oecd_passthrough():
    fallback = pl.DataFrame([_row(dt.date(2022, 7, 21), "DE", "2022-Q1", 3.8)])
    out = _gdp_oecd_splice(pl.DataFrame(), fallback)
    assert out.height == 1 and out["value"][0] == 3.8
