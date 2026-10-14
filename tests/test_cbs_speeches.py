"""Tests for the CBS-dataset collector (no network)."""

from __future__ import annotations

import polars as pl
import pytest

from synthetic_council.collectors import cbs_speeches as cs


@pytest.fixture
def variants() -> list[cs.Variant]:
    # (person, skeleton, surname, first, country, start, end)
    return [
        ("Klaas Knot", "klaas knot", "knot", "klaas", "NL", "2011-07-01", "2026-12-31"),
        ("Mário Centeno", "mario centeno", "centeno", "mario", "PT", "2020-07-20", "2026-12-31"),
        ("Boštjan Vasle", "bostjan vasle", "vasle", "bostjan", "SI", "2019-01-01", "2026-12-31"),
        ("Boris Vujčić", "boris vujcic", "vujcic", "boris", "HR", "2012-07-01", "2026-12-31"),
        (
            "Gediminas Šimkus",
            "gediminas simkus",
            "simkus",
            "gediminas",
            "LT",
            "2021-04-16",
            "2026-12-31",
        ),
        (
            "Yannis Stournaras",
            "yannis stournaras",
            "stournaras",
            "yannis",
            "GR",
            "2014-06-01",
            "2026-12-31",
        ),
    ]


class TestSkeleton:
    def test_folds_diacritics_and_drops_initials(self):
        assert cs._skeleton("Philip R. Lane") == "philip lane"
        assert cs._skeleton("Boštjan Vasle") == "bostjan vasle"
        assert cs._skeleton("Chrystella Georghadji") == "chrystella georghadji"


class TestSubseq:
    def test_is_subseq(self):
        assert cs._is_subseq("vuji", "vujcic")
        assert cs._is_subseq("imkus", "simkus")
        assert not cs._is_subseq("knot", "nagel")
        assert not cs._is_subseq("abc", "cab")


class TestResolveSpeaker:
    def test_exact_skeleton(self, variants):
        assert cs.resolve_speaker("Klaas Knot", "2019-05-02", "NL", variants) == "Klaas Knot"

    def test_damaged_first_name_surname_exact(self, variants):
        # "Mrio" = Mário with á deleted; surname intact
        assert cs.resolve_speaker("Mrio Centeno", "2021-03-04", "PT", variants) == "Mário Centeno"

    def test_damaged_surname_subsequence(self, variants):
        assert (
            cs.resolve_speaker("Gediminas imkus", "2022-01-10", "LT", variants)
            == "Gediminas Šimkus"
        )
        assert cs.resolve_speaker("Boris Vuji", "2023-06-01", "HR", variants) == "Boris Vujčić"

    def test_transliteration_variant(self, variants):
        # Greek Γιάννης appears as both Giannis and Yannis
        assert (
            cs.resolve_speaker("Giannis Stournaras", "2016-06-01", "GR", variants)
            == "Yannis Stournaras"
        )

    def test_country_mismatch_rejected(self, variants):
        assert cs.resolve_speaker("Mrio Centeno", "2021-03-04", "ES", variants) is None

    def test_out_of_tenure_rejected(self, variants):
        # Knot before his governorship
        assert cs.resolve_speaker("Klaas Knot", "2005-03-01", "NL", variants) is None

    def test_unrelated_surname_rejected(self, variants):
        assert cs.resolve_speaker("Christine Lagarde", "2021-03-04", "NL", variants) is None


class TestBuild:
    @staticmethod
    def _csv(tmp_path, rows: list[dict]) -> object:
        cols = [
            "URL",
            "PDF",
            "Title",
            "Subtitle",
            "Date",
            "Authorname",
            "Role",
            "Gender",
            "CentralBank",
            "Country",
            "text",
            "text_original",
            "Filename",
            "Language",
            "Source",
        ]
        df = pl.DataFrame(
            [{c: r.get(c) for c in cols} for r in rows], schema={c: pl.String for c in cols}
        )
        p = tmp_path / "cbs.csv"
        df.write_csv(p)
        return p

    def test_build_filters_dedups_and_drops_existing(self, tmp_path, variants):
        long = "Inflation remains below target. " + "x" * 900
        csv_path = self._csv(
            tmp_path,
            [
                {  # kept: roster governor, new day
                    "Date": "2021-03-04T00:00:00",
                    "Authorname": "Mrio Centeno",
                    "Role": "Governor",
                    "Country": "PRT",
                    "CentralBank": "Bank of Portugal",
                    "text": long,
                    "text_original": long,
                    "Language": "Portuguese",
                    "Title": "A speech",
                    "URL": "https://bportugal.pt/x",
                },
                {  # same person+day, translated copy loses to the row above
                    "Date": "2021-03-04",
                    "Authorname": "Mario Centeno",
                    "Role": "Governor",
                    "Country": "PRT",
                    "CentralBank": "Bank of Portugal",
                    "text": "short",
                    "text_original": None,
                    "Language": "English",
                },
                {  # dropped: deputy governor
                    "Date": "2021-05-04",
                    "Authorname": "Some Deputy",
                    "Role": "Deputy Governor",
                    "Country": "PRT",
                    "text": long,
                },
                {  # dropped: pre-2015
                    "Date": "2014-09-04",
                    "Authorname": "Klaas Knot",
                    "Role": "Governor",
                    "Country": "NLD",
                    "text": long,
                },
                {  # dropped: non-euro country
                    "Date": "2021-06-04",
                    "Authorname": "Klaas Knot",
                    "Role": "Governor",
                    "Country": "USA",
                    "text": long,
                },
                {  # dropped: text too short
                    "Date": "2021-07-04",
                    "Authorname": "Klaas Knot",
                    "Role": "Governor",
                    "Country": "NLD",
                    "text": "tiny",
                },
                {  # dropped: already in a primary source
                    "Date": "2022-08-04",
                    "Authorname": "Klaas Knot",
                    "Role": "Governor",
                    "Country": "NLD",
                    "text": long,
                },
                {  # dropped: ECB institution — covered by the ECB CSV
                    "Date": "2020-09-11",
                    "Authorname": "Christine Lagarde",
                    "Role": "Governor",
                    "Country": "ECB",
                    "text": long,
                },
            ],
        )
        existing = tmp_path / "existing.parquet"
        pl.DataFrame(
            {"speaker": ["Klaas Knot"], "date": ["2022-08-04"], "title": ["t"], "contents": ["c"]}
        ).write_parquet(existing)

        out = cs.build(
            csv_path=csv_path,
            out_path=tmp_path / "out.parquet",
            variants=variants,
            existing_paths=(existing,),
        )
        assert out.height == 1
        r = out.row(0, named=True)
        assert r["speaker"] == "Mário Centeno"
        assert r["date"] == "2021-03-04"
        assert r["source"] == "cbs"
        assert r["translated"] is True
        assert r["url"] == "https://bportugal.pt/x"
        assert out["contents"].str.len_chars().item() > 900
