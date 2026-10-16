"""Coverage guarantees for the member-profile replication package.

Every distinct person in the accounts-extracted participants table must
have exactly one canonical profile file — no missing personas, no
duplicate-identity files.
"""

from __future__ import annotations

import polars as pl
import pytest

from synthetic_council.collectors.persons import canonicalise
from synthetic_council.config import PROCESSED_DIR

PROFILES = PROCESSED_DIR / "member_profiles"


@pytest.mark.skipif(
    not (PROCESSED_DIR / "account_participants.parquet").exists(),
    reason="participants parquet not built on this machine",
)
class TestParticipantProfileCoverage:
    def test_every_participant_has_exactly_one_profile(self):
        participants = pl.read_parquet(PROCESSED_DIR / "account_participants.parquet")
        persons = sorted({canonicalise(p) for p in participants["person"].to_list()})
        missing = [p for p in persons if not (PROFILES / f"{p}.md").exists()]
        assert not missing, f"participants without a profile: {missing}"

    def test_no_duplicate_identity_files(self):
        files = {p.stem for p in PROFILES.glob("*.md")}
        for name in files:
            # a file whose name canonicalises away is a duplicate identity
            assert canonicalise(name) == name, (
                f"non-canonical profile file '{name}.md' (canonical: '{canonicalise(name)}')"
            )
        assert "To be appointed" not in files

    def test_no_placeholder_profiles(self):
        for p in PROFILES.glob("*.md"):
            assert p.stem != "To be appointed"


@pytest.mark.skipif(
    not (PROCESSED_DIR / "memos_ea").exists(),
    reason="memos not built on this machine",
)
class TestMemoCoverage:
    def test_every_rate_meeting_participant_has_a_memo(self):
        """The accounts (scraped from ecb.europa.eu/press/accounts) are the
        source of truth for attendance: every participant of every rate
        decision meeting must have a memo in both arms — including acting
        governors and alternates the Wayback table missed."""
        import polars as pl

        parts = pl.read_parquet(PROCESSED_DIR / "account_participants.parquet").with_columns(
            pl.col("meeting_date").cast(str)
        )
        dec = set(
            pl.read_parquet(PROCESSED_DIR / "gc_decisions.parquet")
            .with_columns(pl.col("announcement_date").cast(str))["announcement_date"]
            .to_list()
        )
        rows = [r for r in parts.iter_rows(named=True) if r["meeting_date"] in dec]
        assert rows, "no rate-meeting participants found"
        for arm in ("memos_ea", "memos_ea_country"):
            missing = [
                (r["meeting_date"], r["person"])
                for r in rows
                if not (
                    PROCESSED_DIR / arm / f"{r['meeting_date']}_{r['person'].replace(' ', '_')}.md"
                ).exists()
            ]
            assert not missing, f"{arm}: participants without memos: {missing[:5]}"


class TestKnownNonMembers:
    def test_gap_members_carry_country(self):
        """Members never captured by the Wayback snapshots still resolve to
        their NCB role/country (native bank names + known-members table)."""
        path = PROCESSED_DIR / "account_participants.parquet"
        if not path.exists():
            pytest.skip("participants not built")
        import polars as pl

        parts = pl.read_parquet(path)
        for person, country in [
            ("Margarita Delgado", "ES"),
            ("Tuomas Välimäki", "FI"),
            ("Māris Kālis", "LV"),
            ("Sandra Švaljek", "HR"),
            ("Tina Žumer", "SI"),
        ]:
            rows = parts.filter(pl.col("person") == person)
            if rows.height:
                assert rows["country"].unique().to_list() == [country], person


class TestProfilePersonaCard:
    def test_roles_line_kept_country_dropped(self, tmp_path, monkeypatch):
        """The memo persona keeps the verified **Roles:** line but drops the
        **Country:** scaffolding (memo.profile_text)."""
        from synthetic_council import memo

        (tmp_path / "Ada Test.md").write_text(
            "# Member profile — Ada Test\n\n"
            "**Country:** XX\n\n"
            "**Roles:** Governor, Test Bank, 2020-01-01 → 2026-12-31\n\n"
            "## Approach to the price stability mandate\n\n"
            "Hawkish leanings.\n",
            encoding="utf-8",
        )
        monkeypatch.setattr(memo, "PROFILES_DIR", tmp_path)
        card = memo.profile_text("Ada Test")
        assert "**Roles:** Governor, Test Bank" in card
        assert "**Country:**" not in card
        assert "## Approach" in card
