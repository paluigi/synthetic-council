"""GC member profile building.

Inputs per member (speeches deliberately excluded from profiles):

- deterministic repo facts (memberships: roles, tenures, country);
- Tavily per-trait Q&A (raw answers + sources archived);
- Wikipedia page extract (raw archive).

Plus the per-meeting speech-stance memo sections (from the strict-window
speech join) — those are memo content, not profile content.

Single entry point: ``scripts/build_profiles.py`` — one reviewable markdown
per member under data/processed/member_profiles/.
"""

from synthetic_council.member_profiles.speech_sections import build_speech_sections

__all__ = ["build_speech_sections"]
