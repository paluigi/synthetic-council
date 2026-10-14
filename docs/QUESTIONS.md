# Open questions

## Sample coverage (RESOLVED in review, 2026-08-22)

1. ~~EA GDP vintages~~ — **solved**: ECB RTD `Q.S0.S.G_GDPM_TO_C.E` carries EA
   GDP release history from 2001-01-03 (PEEI only had 2014-10+). Both retained.
2. ~~DDR/PCR meaning~~ — **solved** via official `CL_PD_ITEM` codelist:
   DDR = domestic demand (excl. stocks), PCR = private consumption. Headline
   items corrected to HIC (HICP) / YER (real GDP) / URX (unemployment).
3. ~~Off-by-one announcement dates~~ — **solved**: pre-2015 foedb timestamps are
   evening batch times; true dates taken from press-release URL slugs and
   verified against 16 documented decisions.

## Awaiting user decision

1. **1999–2000 information set**: RTD vintage history starts 2001-01-03, so the
   first 44 meetings have no true HICP/GDP vintages. Start the evaluation sample
   at 2001 (recommended; keeps everything true-vintage) or backfill 1999–2000
   with first-available vintages flagged approximate?
2. **1999–2004 memberships**: earliest Wayback GC page is 2004-07. Seed the
   1999–2004 roster from ECB Annual Report appendices (~25 people, hand-curated)
   or accept a 2004+ simulation start?
3. **Country GDP**: no source with true country GDP vintages exists (Eurostat
   PEEI and ECB RTD are both aggregate-only). Keep country memos on
   sentiment (+unemployment/IP vintages from PEEI) and drop country GDP, or use
   latest-vintage country GDP masked by the t+45 release calendar
   (approximation)?
4. **Target variable**: direction (hold/hike/cut) or magnitude (bp)?
5. ~~**Voting rights**~~ — **resolved**: modelled explicitly from the accounts
   asterisks (2015+; `mpa_extract/participants.py`); used for the modal-voter
   decision cross-check and recorded per agent.
6. **Hawkishness priors**: LLM classification of member speeches
   (Hansen–Kazinnik style) as agent priors — v1 or later?
