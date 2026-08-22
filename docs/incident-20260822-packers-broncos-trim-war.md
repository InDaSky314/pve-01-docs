# Incident 2026-08-22 — Packers @ Broncos recording destroyed by cross-game trim war

**Outcome:** first half captured in 9 fragments, second half (from early Q2,
14:07 remaining) lost entirely. Salvage: 2h14m stitched file recovered (see
below). Three real bugs found and fixed in `sports-dvr-auto`.

## What the owner saw

The 8/21 preseason Packers @ Broncos game (NFL Network HD, 01:00 UTC timer)
appeared as 9 partial "Live: NFL Football" recordings; no stitched file, and
comskip processed fragments instead of the game. Owner suspected upstream
stream drops — **the feed was fine; every drop was self-inflicted.**

## Root cause 1 — live extender matched games to timers by time overlap alone

`run_live_extender()` matched ESPN scoreboard games against Jellyfin timers
purely by *"does the timer's window overlap the game's window?"* — no channel
or identity check. The Braves @ Brewers game (a game with **no timer at all**
— Brewers auto-record is OFF) went Final at 22:36 UTC while its ESPN window
still overlapped the Packers timer's window on the clock. Every 5-minute
cycle then "trimmed" the Packers timer to now+5min in the Braves game's name.
Each trim ended the recording → stall watchdog restored it → fresh timer
(same 02:00 UTC end) overlapped the Braves window again → trimmed again.
All 8 "stalls" in `dvr-automation-events.jsonl` that night line up with a
trim minutes earlier; Threadfin logged zero upstream errors. In the final
stretch the Packers *extend* and the Braves *trim* fought over the same timer
on alternating ticks; a trim won last (~01:57 UTC), the timer completed, fell
out of the watchdog's InProgress filter, and the second half never recorded.

**Fix:** new `candidate_channel_ids()` — a timer must be on a channel the
game could actually air on (RSN default, `NETWORK_CHANNEL_MAP` entry, or
channel-name containing a listed broadcast) before extend/trim will touch it.

## Root cause 2 — restore-timer confirmation raced Jellyfin, so stitching never ran

All 8 restores created real, working timers (segments exist on disk), yet all
8 logged `success: false — "created timer ID could not be confirmed"`: the
POST response carried no Id and the fallback search ran *immediately* after
the POST, before Jellyfin listed the new timer. With no confirmed
`restore_timer_id`, `recording-restore-state.json` never got a chain entry —
so `check_restore_stitching()` had nothing to stitch, and comskip (which
works fine — it processed every fragment as it finalized) never saw a
stitched game. Same mechanism left the Aug 16 "Brewers: MIL @ LAD" chain
stuck at "1/4 pending".

**Fix:** the confirmation search now retries up to 4× with 5s sleeps before
declaring failure.

## Root cause 3 — "Packers TV Network" unmapped; national cable beat local carriage

ESPN listed the game's broadcasts as `['NFL Net', 'Packers TV Network',
'KUSA-TV (9NEWS)']`. `resolve_channel_id()` walks broadcasts in ESPN's
order — `NFL Net` matched first; `Packers TV Network` wasn't in
`NETWORK_CHANNEL_MAP` at all. Worse, the 8/28 Cardinals @ Packers game lists
`Packers TV Network` **only** and was being skipped as Unmapped — it would
have been missed entirely. (The 2026-08-14 `LOCAL_MARKET_PREFERENCE` fix
only chooses *between affiliates of one broadcast string*; it never reorders
*between broadcasts*.)

**Fix:** mapped `"Packers TV Network"` → `"Green Bay: NBC 26 (WGBA)"` (the
Packers TV Network's Green Bay affiliate), and added
`TEAM_NETWORK_BROADCASTS` + a broadcast-priority sort in
`resolve_channel_id()` so a team's own/local carriage is tried before
national cable regardless of ESPN's list order. Verified: 8/21 game now
resolves to WGBA (was NFL Network HD); 8/28 game now schedules on WGBA (was
skipped); regular-season CBS/FOX games still resolve to Green Bay affiliates;
NFL-Net-only games still fall back to NFL Network HD.

## Salvage of the 8/21 recording

7 of 9 segments held real video (two were 11–12 KB corpses killed instantly
by the next trim). Production `perform_ffmpeg_concat` failed on the final
segment: its MKV pre-remux chokes on (a) two **phantom audio PIDs** (eng/spa,
0 channels) the provider muxes in, and (b) a torn AAC tail. Working recipe:
per-segment remux to **mpegts** (`-err_detect ignore_err -fflags
+genpts+discardcorrupt -c copy`), and for the last segment demux video
(`-map 0:0`) and audio (`-map 0:1` → raw ADTS) separately, remux together,
then concat. Result: 4.96 GB / 2h14m `(stitched).ts`, playability spot-checked
at 3 seek points, raw segments moved to `/media/segments-archive` via
`archive_segments()`, stitched file queued for comskip.

## Hardening (landed same day, 2026-08-22)

`preremux_segment_on_ct105` now survives the phantom-PID + torn-tail
combination automatically:

1. **`_probe_valid_stream_maps()`** — ffprobe the segment first and `-map`
   only the first video stream and the first audio stream with
   `channels > 0`, skipping the provider's phantom 0-channel PIDs. Falls
   back to unmapped copy if the probe itself fails.
2. Single-pass remux now runs with `-err_detect ignore_err
   -fflags +genpts+discardcorrupt`.
3. **`_preremux_split_stream_fallback()`** — if the single pass still fails,
   extract video (→ mpegts) and audio (→ raw ADTS) separately, then
   recombine with video copied and **audio re-encoded** (aac 160k). The
   re-encode is mandatory, not an optimization: torn AAC frames survive a
   bitstream copy and break the matroska mux, but the decoder skips them.
   The output must be genuine matroska — during testing, an mpegts-content
   temp made the downstream mkv concat **silently truncate the stitch at
   that segment** ("Can't write packet with unknown timestamp"), which
   would have been a worse failure than the one being fixed.

All three verified against the real 8/21 torn segment (recovered at its full
1980.9s), a healthy segment (fast path unchanged), and a mixed
healthy+recovered concat (correct 3608.3s total, no truncation).
