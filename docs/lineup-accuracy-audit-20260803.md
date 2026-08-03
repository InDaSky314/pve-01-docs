# Lineup accuracy audit — 2026-08-03/04

Triggered by two questions from the owner: *is "CSN" accurate?* and *is it
actually Bally or FanDuel?* Both turned out to be worth asking, and the answer
changed the plan.

Method throughout: **do not reason about the name, look at the stream.** Every
claim below is either a frame grabbed from the live feed or a dated source.

---

## 1. The regional sports networks are dead, not misnamed

Bally Sports was renamed **FanDuel Sports Network** on 2024-10-21. Its
operator, Main Street Sports Group, then **ceased operations in mid-April
2026** after missing rights payments — the end of the traditional RSN model.

Confirmed independently on this system: **40 of 41** Bally/FanDuel-branded
feeds return a byte-identical 12,461-byte black frame. The single survivor,
`US: BALLY SPORTS SOCAL HD`, carries a **FanDuel Sports Network** bug on
screen — which is what proves the rebrand rather than merely citing it.

**agy got this wrong in the dangerous direction.** Asked to research stale
brands, it returned "US: BALLY SPORTS WISCONSIN HD → FanDuel Sports Network
Wisconsin" at *high* confidence. That rename would have pointed 40 channels at
a brand that no longer exists. The stream probe caught it. Research alone was
not enough; dead air alone was not enough; the two together settled it.

## 2. CSN has three different successors

| Lineup name | Actually | How established |
|---|---|---|
| CSN Chicago / Chicago Plus | **Chicago Sports Network (CHSN)** | CHSN bug on screen, White Sox @ Rays |
| CSN Philadelphia / Plus | **NBC Sports Philadelphia** | NBC Sports bug on screen |
| CSN Washington | **Monumental Sports Network** | rebranded 2023 |
| CSN Bay Area / Boston / California | **NBC Sports** \<region\> | still operating 2026 |

All 8 CSN feeds are alive. Comcast SportsNet became NBC Sports Regional in
2017, so these names have been wrong for nine years.

## 3. The CW affiliates — a trap, and one I fell into

All eight **CBS-owned CW affiliates went independent in September 2023**:
KBCW, KMAX, KSTW, WKBD, WPCW, WPSG, WTOG, WUPA. That is exactly the set whose
station logos were installed earlier on 2026-08-03, matched by call sign.

Six of six were therefore wrong — right network, wrong station. Worse:
**`US: CW SAN FRANCISCO HD` is carrying KPBS San Diego**, a different station
in a different market on a different network.

Reverted to the CW mark plus a market label, which claims nothing that cannot
be supported. **A generic logo is not a failure; a specific wrong one is.**

## 4. Milwaukee — the question that mattered most

The provider carries **53,658** live streams. Searched all of them.

* **MY24 / WVTV / WCGV: not carried.** Nor any Rincon statewide affiliate.
  The Bucks' own DTC service has not launched. None of the four routes in the
  owner's research is available through this provider.
* **`NBA: MILWAUKEE BUCKS ᴴᴰ` (633369) — live**, an NBA League Pass team feed.
* **`US: MLB MILWAUKEE BREWERS` (1904210) — live**, an MLB.TV team feed.
  Matches reality: the Brewers moved to MLB-produced distribution for 2026.
* Neither is in the lineup. **The only Wisconsin sports channels we do carry —
  120 and 121, Bally Sports Wisconsin — are both dead.**

Caveat: League Pass and MLB.TV black out in-market games. These containers
egress from Zürich, so the provider sees out-of-market requests, which is
likely why they resolve at all. A live-game sample is scheduled for 03:00 CEST
against Pirates @ Brewers to settle whether the feed carries the game or only
the slate.

---

## Removal criteria

Set by the owner: remove a channel only when it is **backed by research and
dead air**. Encoded in `scripts/dead-classify.py` as:

1. **dead air twice** — black or no-video in the fast sweep *and* in a slow
   serial confirm pass. The fast sweep's no-video count jumped 3 → 82 partway
   through, which is the shape of a provider connection limit, not 79
   simultaneous deaths. One failure is a measurement, not a verdict.
2. **not an event-pool slot** — `Soccer PPV 42`, `UEFA 16`, `NHL 07` and the
   Bundesliga ranges are dark *by design* between fixtures. Removing them
   would delete the working PPV system.
3. **research** — a citation per group. Anything without one is reported as
   "needs research" and never auto-removed.

## Naming

Owner's choice: Title Case, quality suffix retained, group prefixes dropped
(`US:`, `GO:`, `PRIME:`, `DE:`, `UK:`), market prefixes kept (`Green Bay:`
identifies the feed; `US:` does not).

One trap worth recording: **the provider's names are entirely uppercase, so
capitalisation carries no information.** A first attempt detected acronyms and
call signs by pattern and produced "National Geographic WILD" and "Animal
Planet WEST" — because `WILD` and `WEST` match any call-sign shape. Acronyms
now come from an explicit list, and call signs are recognised only by being
parenthesised, which is positional and therefore reliable.

Four name collisions arise and are resolved explicitly, never silently:
`NBA TV HD`, `MLB Network`, `MeTV`, `MTV HD`.
