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
