# Brewers recording + auto-restore/stitch: real status (2026-08-10 analysis)

Checked directly from the pve-01 host and CT105 — not relying on GUI/dashboard status.

## Did the Brewers game record all the way through?

**No.** `Brewers  MIN @ MIL 2026_08_09_20_05_00.ts` (in `/srv/media-core/media/recordings/Other/`,
not `Sports/` — its own folder mismatch, separate minor issue) is **9,317s (~2h35m)**,
confirmed via `ffprobe` inside the jellyfin container. Started 20:05:00, file stopped
growing at 22:34 (mtime). A 9-inning MLB broadcast is typically 3–3.5h — this is the
same truncation pattern described in the original bug report, not a naturally short game.

Threadfin's container log shows exactly one line for this recording — the channel-name
line at 20:00:00 — and **nothing else, no error, at any point including the 22:34 stop**.
Same "zero error logged anywhere" signature as the original incident.

## Did auto-restore + stitch catch it?

**No — and this needs a real look, not just a shrug.** Evidence:

- `sports-config.json` shows `"Brewers": false` — auto-record for Brewers was already
  toggled OFF by 19:04, an hour before this recording started. The recording that
  happened was **not** created by the auto-scheduler (likely a manual/one-off
  schedule) — worth confirming with the owner.
- The watchdog+stitch code (`/usr/local/bin/sports-dvr-auto`, mtime Aug 9 18:01) was
  live and running its 5-minute cycle continuously through the entire game window —
  logs show ticks at 20:05, 20:10, 20:15... straight through 22:34 and beyond, every
  one logging "Checking for stalled recordings" and "Checking restore timers for
  stitching".
- Despite that, `/var/lib/dvr-dashboard/recording-stall-state.json` is `{}` (empty) —
  **this recording's timer ID never once entered the watchdog's stall-tracking state**,
  meaning it was never seen as a stall candidate at all, not "seen but not yet
  triggered."
- Root cause, from reading `check_stalled_recordings()` directly: it only evaluates
  timers where Jellyfin's own `timers.json` reports `Status == "InProgress"`. If this
  recording's timer never showed that status to Jellyfin's LiveTV integration (e.g.
  scheduled through a path that bypasses Jellyfin's timer tracking, or the upstream
  status flipped away from "InProgress" for some other reason while the file was still
  technically being held open), the watchdog has a structural blind spot: **it can only
  protect recordings Jellyfin itself believes are still in progress**, not recordings
  visible only at the Threadfin/file level.

## Bottom line for the owner

- The build/review that happened (2 issues found and fixed pre-deploy, cancel-then-
  recreate proven against a live throwaway timer) was real and solid, but that was a
  **synthetic** test. This is the first real production case since deploy, and **the
  system did not catch it** — worth root-causing why this recording's status never
  showed "InProgress" to Jellyfin before trusting it for the next real game.
- No Brewers games have recorded since (auto-record is currently off), so there's been
  no second live data point.
- Recommend: before re-enabling Brewers auto-record, check how *this specific*
  recording was actually scheduled (manual one-off vs. auto-scheduler) and whether
  Jellyfin's timers.json ever listed it as InProgress during its run — that's the
  concrete next step to close the gap, not a re-review of the stitch logic itself
  (which was never reached).
