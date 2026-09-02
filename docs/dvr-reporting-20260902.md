# DVR schedule sourcing and automated reporting — 2026-09-02

Follows the media-core health sprint of 2026-08-31 (`docs/media-core-sprint-20260831.md`).
Claude Code briefed and verified; agy executed each build in `build` mode. Every item had a
recorded pre-state and a back-out plan before it ran.

## What prompted it

The owner asked why Bayern Munich had no schedule on the DVR dashboard. It turned out the
2026-09-05 Schalke–Bayern match was real, three days away, **and had no recording timer** —
he would have missed it. Investigating that surfaced two further problems and one hardware
behaviour that matters more than any of the software.

## 1. ESPN cannot supply Bundesliga fixtures — replaced with OpenLigaDB

Full detail in `lessons-learned.md`. Summary: `soccer/ger.1/teams/132/schedule?seasontype=2`
returns **0 events**, and the scoreboard only carries the current matchday slice, so a fixture
three days out is invisible. The dashboard's config was never wrong.

`https://api.openligadb.de/getmatchdata/bl1/<year>` is free, unauthenticated, and returns the
full season — 306 matches, 34 of them Bayern. Bayern's schedule went **1 → 35 fixtures**.
UEFA Champions League stays on ESPN, whose scoreboard genuinely does return a full slate.

Fixtures now appear **whether or not the EPG has them yet** — the season runs months ahead of
the ~54-day guide window. A fixture with no broadcast match shows `not in guide yet` rather
than being dropped, which is what previously made the whole schedule look empty.

Where the EPG *does* have it, the fixture is enriched with the real broadcast: channel number,
channel name, and the actual coverage window. Two traps are documented in `lessons-learned.md`
— match on the **sub-title** (the title is the generic `Fußball: Bundesliga`), and require the
programme window to **contain the kickoff**, because Sky opens coverage an hour early and
because `Klassiker der Woche: … (2014/2015)` archive reruns otherwise match on name alone.

## 2. Channel number and affiliation in the UI

Owner request: show what channel a recording is on, in the terms Wholphin shows. Wholphin is a
Jellyfin client, so its channel number **is** Jellyfin's `ChannelNumber`. Both the recordings
list and the schedule rows now render `"<num> <name>"` — e.g. `1011 Sky Sport Bundesliga HD`,
`101 Madison: NBC 15 (WMTV)`. The network affiliation is already inside the channel name, so it
is surfaced as-is rather than parsed out and risked.

## 3. "other recording overlaps" was a label bug

A timer only carries the `"<Team>:"` name prefix when `sports-dvr-auto` created it, so any
hand-made timer displayed as a foreign overlap **on its own game**. Fixed generally — channel
comparison first, then the team's leading token for the ESPN-sourced US teams that carry no
resolved channel. See `lessons-learned.md` for why the per-team string-matching version that
was tried first is the wrong shape.

## 4. Post-recording quality report (`dvr-recording-report`)

Runs every 5 minutes; reports each completed recording **exactly once**, and only after
post-processing has actually finished (it gates on the comskip queue, the runner lock, and the
work dirs — not on a timer guess).

Reports verdict-first — GOOD / CHECK THIS / FAILED — then: title, subtitle, channel number and
name, scheduled vs actual runtime, comskip breaks and commercial minutes removed, whether a
stall/restore/stitch fired, and both file paths. **Both copies are always preserved**; the
report says so explicitly so it can never be read as "the original was replaced".

On anomaly only, it dispatches **agy in diagnose mode** to root-cause across the Jellyfin log,
tuner contention (this account has ONE concurrent stream), boot history, and provider health,
and includes the finding in the mail.

**Known characteristic, not a bug.** The runtime baseline is the timer's unpadded window. A
recording created from a programme grades correctly. A **manual** timer set far longer than the
actual broadcast will grade short and be flagged — that is what happened to the 2026-08-28
Bayern–Stuttgart recording (3h18m against a hand-set 4h28m window). Nothing was wrong with it;
agy's post-mortem said so correctly. Expect this on generously-padded manual timers, and read
the post-mortem before acting.

Strictly non-destructive: it only reads. A recursive diff of the recordings tree before and
after a full test run showed only the runner lock's timestamp changed.

## 5. Pre-flight digest and backup restorability

See `scripts/` for both. The pre-flight digest exists because of the 21-hour outage below — a
morning mail listing the next 48 hours, power-window violations, single-tuner conflicts, and
**tracked games with no timer set** would have caught both the outage's consequences and the
Bayern near-miss. The backup verifier exists because vzdump reporting "finished successfully"
is not evidence the archive is restorable, and nobody had ever opened one.

## 6. The hardware behaviour that outranks all of it

**The host only boots on an AC power transition.** From 2026-09-01 22:13 to 2026-09-02 19:18 it
was off for ~21 hours and every recording in that window was lost. `dvr-clean-shutdown` had
powered it down cleanly at 22:10 in anticipation of a mains cut that never came — and with no
cut, there is no restore, so nothing brought it back.

The monitoring stack cannot see this: everything it scrapes lives on the host that is off.

A multi-day hold is set through the override file, **not** `dvr-shutdown-hold`, which cannot
mask a unit that exists as a real file in `/etc/systemd/system`. The dashboard API clamps
overrides to the next cutoff, so write the file directly and verify with the script's own dry
run. Currently held to **2026-09-09 12:00** at the owner's request:

```bash
cat /var/lib/dvr-dashboard/override-until      # 2026-09-09T12:00:00+02:00
/usr/local/bin/dvr-clean-shutdown --dry-run    # override active until Wed 12:00 -- staying up
```

Restore normal behaviour by removing/expiring that file when the mains timer is back in use.

## Still open

* **The mains timer is a dumb mechanical one.** The Shelly smart plugs remain an unpurchased
  shopping item in `home-network-power-automation.md`. Until then, a missed power cycle is
  silent and costs every recording that day. This is the weakest link in the chain and no
  amount of scheduling software fixes it.
* **Detecting a missed boot has to come from outside the host.** Nothing on pve-01 can alert
  on pve-01 being off.
* Tonight's DFB-Pokal match (VfL Osnabrück v Bayern, 2026-09-02) is **not carried live** on
  this lineup — only a Sportschau highlights slot. `not in guide yet` was accurate.
