# Audit 2026-09-17 — lineup, EPG, recording infrastructure, artwork

Owner's brief (07:25, on the way to work): full audit, decisions delegated.
Claude Code manages; agy (gemini-3.8-flash-high) executes and reviews in
turn; every chunk is verified by the other before the next is released.
Email the owner when done.

## Budget
agy: 76% of its 5h window at 07:25 (refreshes ~10:09), 90% weekly.
Claude Code: 37% of session at 07:30 (resets ~11:49).
Rule: deterministic work runs as scripts, not as LLM turns. agy is spent on
research and review, where it adds something a script cannot.

## Workstreams

### WS1 — Lineup sanity: do the non-event channels carry footage?
Prompted by 2026-09-13: Big Ten served a static slate for 3h and nothing knew.
1a  [Claude, script]  Probe every curated non-event channel: 1 MB sample,
    fingerprint, freezedetect. Classify LIVE / SLATE / FROZEN / DEAD.
    Sequential, yields to any live viewer (single tuner). ~600 channels.
1b  [agy, review]     Attack the classifier: false positives (static-image
    channels that are legitimately static), false negatives.
1c  [Claude, build]   Act: dead -> gap in config.json; slates -> alert;
    a weekly timer that re-runs the probe and alerts on regressions.

### WS2 — EPG: maximise real guide data
Coverage today 32.6% of channels. BTN and the RSNs publish nothing.
2a  [Claude, script]  Measure: which channels have synthetic-only EPG, grouped
    by network. Rank the gaps by how much the DVR cares (sports first).
2b  [agy, research]   For the top gaps, find public XMLTV sources that carry
    them (epgshare, iptv-org, mjh.nz, network-specific). Exact URLs, channel
    ids, sample lines. This is research; do it after agy's window resets.
2c  [Claude, build]   Wire sources + epg_aliases; measure the lift. agy reviews.

### WS3 — Recording infrastructure: still GTG after a week of change?
3a  [Claude, script]  Checklist with evidence: timers armed; MCT dry-run for
    every booking; tunnel/content/pre-empt guards present; comskip queue and
    cleanup; disk; power override; digest parses. One 2-minute real capture
    end to end, since the lineup just changed under everything.
3b  [agy, review]     Review the evidence, not the claims.

### WS4 — Sports artwork
Owner: "most of the auto-generated artwork is random screen grabs."
4a  [Claude, script]  Audit: where does each poster come from (programme
    image / channel logo / frame grab)? Sample every sports recording.
4b  [agy, plan]       Design a deterministic workflow: ESPN gives team logo
    URLs per fixture; compose "Away @ Home · date" cards; league logo
    fallback; only then a frame grab. No generative step in phase 1.
4c  [Claude, build]   Implement in mct's poster step; backfill existing
    recordings; agy reviews.

## Sequencing
07:50  1a + 2a + 3a + 4a scripts (Claude, deterministic, parallel where the
       tuner allows -- 1a owns the tuner; the rest do not touch it)
~09:00 agy 1b review + 4b design (light, fits the current window)
10:09  agy window resets -> 2b research, 3b review
then   2c / 4c builds, agy reviews, email.

## Outcome (written 2026-09-17 morning; details in the email of the same day)

| WS | Result |
|----|--------|
| 1 | 630 non-event channels probed straight from the provider (`/root/audit-20260917/lineup-probe.tsv`, seeded into `/var/lib/dvr-dashboard/lineup-probe/`). Event-slot groups idle on a Thursday morning account for most SLATE hits; linear channels not LIVE at 08:00–09:00 CEST were re-probed. Productionised as `lineup-probe` + weekly timer (Thu 14:00 CEST); mails when a linear channel is bad two runs running. |
| 2 | agy WS2b research verified id-by-id against the live epgshare files; 17 aliases wired (Big Ten Overflow 2/3, CHSN ×3, BBC Earth via CA2, TCM, AHC, FXM, Law&Crime, Vice, El Rey, Magnolia, GAF, SHOxBET, FanDuel TV, MeTV scraper). All 17 now carry real data. Remaining gaps are FAST/loop channels (Prime 24/7, music, shopping). |
| 3 | Timers armed, Badgers MCT booking dry-resolves to Peacock PPV 016, Packers + Bayern timers present, exit Zurich / 0 bounces, disk 22 % free. No change needed. |
| 4 | Deterministic team-logo cards (`sports_card.py`) rendered **at booking time** for every wanted game into `/var/lib/dvr-dashboard/cards/<espn id>/`, applied by `sports-artwork-sweep` (15 min) when a recording lands in In Progress or Sports - CF, matching on team names or stadium + date; ESPN re-derivation second; optional Claude fixture resolver third (needs `ANTHROPIC_API_KEY` in CT105 `.env`). MCT still renders inline at capture start. agy 4c review findings all fixed (cache lockout, per-recording thumb naming, rescue thumb, comskip stems, `_team_tokens`, per-item Jellyfin refresh by path). |
