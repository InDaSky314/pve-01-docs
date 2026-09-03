# Knowing when to stop a Bundesliga capture (MCT)

**Status 2026-09-03:** signal identified and bounded, latency being measured Saturday.
Not yet wired into MCT.

## The problem

MCT records for a fixed duration. `sports-dvr-auto` solves this for Jellyfin by polling ESPN
(`run_live_extender`) and pushing the timer end while the game reads in progress — but
**Bundesliga is not on ESPN**, so that path gives MCT nothing for the fixture we most care
about. Padding blindly is the alternative, and padding costs tuner time on a single-connection
account.

## The signal

OpenLigaDB — already the dashboard's Bundesliga fixture source (`OPENLIGADB_BASE`,
`fetch_openligadb_bayern`) — carries live state, not just kickoff times:

| field | use |
|---|---|
| `matchIsFinished` | the stop signal |
| `goals[].matchMinute` | live progress; confirms the match is actually running |
| `matchResults` | fills in as the match proceeds |
| `lastUpdateDateTime` | last edit of the record — **not** a match-end timestamp |

Bind by `matchID`, not by team names: `https://api.openligadb.de/getmatchdata/<matchID>`
returns the single match. Saturday's is `83172` (FC Schalke 04 v FC Bayern München,
kickoff 2026-09-05T16:30:00Z = 18:30 CEST). The dashboard should store `matchID` on the
booking so the poller never has to re-match on names.

## Why latency had to be measured, not assumed

On completed matchday 1, `lastUpdateDateTime` landed **116–125 min after kickoff** — about
1–10 min after a ~115 min final whistle, which looks usable. But one match
(Bayern v Stuttgart) showed **6,906 min** — a correction days later. So `lastUpdateDateTime`
tracks *any* edit and cannot stand in for "the match ended", and it is not the same event as
`matchIsFinished` flipping. Retrospective data only ever shows the final state, never the
moment of transition.

Cutting a recording on a signal whose timing is inferred rather than observed is how you lose
the end of a match. So the flip gets measured live first.

## The measurement

`/usr/local/bin/oldb-latency-probe` samples match `83172` every 60s, appends every state
*change* to `/var/lib/dvr-dashboard/oldb-latency-83172.jsonl`, and exits when
`matchIsFinished` goes true. Scheduled by `oldb-latency-probe.timer` for
**2026-09-05 19:00 CEST** (after the 18:30 kickoff, before the ~20:25 expected whistle),
bounded by `RuntimeMaxSec=3h`.

This costs nothing: the match is already being recorded by Jellyfin on a fixed timer
(17:30–21:15 CEST — 60 min lead, 50 min margin past the whistle), and the host is held up by
both that timer and the override to 2026-09-09.

## Wiring it in, once the latency is known

* poll `getmatchdata/<matchID>` every ~2 min during an MCT capture
* stop only once the match has plausibly run (`now > kickoff + ~100 min`) **and**
  `matchIsFinished` is true — this rejects a stale or pre-populated flag
* **never extend past the next Jellyfin timer or MCT booking minus its padding.** One tuner:
  an MCT capture running long silently destroys the next recording
* if the API is unreachable or the match is not found, fall back to nominal duration plus a
  fixed pad — never extend blind
