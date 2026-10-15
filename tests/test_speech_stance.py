"""Tests for the speech-grounded profile orientation section (offline)."""

from __future__ import annotations

from datetime import date as d

import polars as pl

from synthetic_council.member_profiles import speech_stance as ss


def _corpus(n: int, speaker: str = "Klaas Knot") -> pl.DataFrame:
    return pl.DataFrame(
        {
            "speaker": [speaker] * n,
            "date": [d(2020 + i // 12, i % 12 + 1, 5) for i in range(n)],
            "title": [f"speech {i}" for i in range(n)],
            "contents": ["text " + "x" * 50] * n,
            "source": ["bis"] * n,
            "url": [""] * n,
        }
    )


class TestSelect:
    def test_small_corpus_returned_whole(self):
        rows = ss.select_speeches(_corpus(3), "Klaas Knot")
        assert len(rows) == 3

    def test_large_corpus_evenly_spread_includes_ends(self):
        rows = ss.select_speeches(_corpus(40), "Klaas Knot")
        assert len(rows) == ss.MAX_SPEECHES
        assert rows[0]["title"] == "speech 0"
        assert rows[-1]["title"] == "speech 39"

    def test_no_speeches(self):
        assert ss.select_speeches(_corpus(3), "Someone Else") == []


class TestApplyToProfile:
    def _profile(self, tmp_path, person="Klaas Knot"):
        (tmp_path / f"{person}.md").write_text(
            "# Member profile — Klaas Knot\n\n**Country:** NL\n\n"
            "## Monetary policy stance\n\n(no evidence collected)\n\n"
            "## Career and professional background\n\n(no evidence collected)\n\n"
            "\n---\nProvenance footer\n",
            encoding="utf-8",
        )

    def test_inserts_before_first_trait_and_preserves_rest(self, tmp_path):
        ss.PROFILES_DIR = tmp_path
        self._profile(tmp_path)
        changed = ss.apply_to_profile("Klaas Knot", "You have warned against easing too fast.")
        text = (tmp_path / "Klaas Knot.md").read_text()
        assert changed
        assert text.index(ss.HEADING) < text.index("## Monetary policy stance")
        assert "You have warned against easing too fast." in text
        assert "(no evidence collected)" in text  # untouched sections
        assert "Provenance footer" in text

    def test_replacement_is_idempotent(self, tmp_path):
        ss.PROFILES_DIR = tmp_path
        self._profile(tmp_path)
        ss.apply_to_profile("Klaas Knot", "first version")
        ss.apply_to_profile("Klaas Knot", "second version")
        text = (tmp_path / "Klaas Knot.md").read_text()
        assert text.count(ss.HEADING) == 1
        assert "second version" in text and "first version" not in text
        assert text.count("## Monetary policy stance") == 1

    def test_missing_profile_is_noop(self, tmp_path):
        ss.PROFILES_DIR = tmp_path
        assert ss.apply_to_profile("Nobody Here", "text") is False

    def test_sparse_profile_inserts_before_footer(self, tmp_path):
        # profiles without trait sections: the section must land before the
        # provenance footer, or memo.profile_text (which reads only up to
        # the footer) would drop it
        ss.PROFILES_DIR = tmp_path
        (tmp_path / "Sparse Member.md").write_text(
            "# Member profile — Sparse Member\n\n**Country:** XX\n\n"
            "**Roles:** Governor\n\n\n---\nProvenance footer\n",
            encoding="utf-8",
        )
        ss.apply_to_profile("Sparse Member", "You lean hawkish.")
        text = (tmp_path / "Sparse Member.md").read_text()
        assert text.index(ss.HEADING) < text.index("\n---\n")
        assert "You lean hawkish." in text
        assert "Provenance footer" in text
