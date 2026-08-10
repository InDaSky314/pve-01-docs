# DVR stall-watchdog fix — root cause confirmed and deployed (2026-08-10)

## Root cause (agy diagnose, independently verified against real data)

`check_stalled_recordings()` in `/usr/local/bin/sports-dvr-auto` sourced
timers exclusively from `read_live_timers()` — a `pct exec 105 -- cat` of
Jellyfin's **on-disk** `timers.json`. That file's `Status` field only ever
takes `New`/`Completed`/`Cancelled` (Jellyfin's `TimerStatus` enum) — it
**never** contains `"InProgress"`. `"InProgress"` only exists as a
dynamically-computed value on Jellyfin's live REST API
(`GET /emby/LiveTv/Timers`, via the already-existing `get_existing_timers()`
function, used elsewhere in the same script for the live-extender).

The watchdog's filter — `if t.get("Status") != "InProgress": continue` —
therefore dropped **every timer, every 5-minute tick**, since it was reading
from a data source that structurally could never satisfy that check.
`/var/lib/dvr-dashboard/recording-stall-state.json` stayed `{}` through the
entire Aug 9 Twins@Brewers stall as a direct result.

Verified independently (not just trusting agy's report):
- The real on-disk timer for this recording: `Id
  f9b188292ce24859b183b59e4764a550`, `RecordingPath` matching the actual
  truncated file, `"Status": "Completed"` — confirmed live via `pct exec 105
  -- cat .../timers.json`, byte-for-byte match to what agy quoted.
- Jellyfin's own log line, byte-for-byte match:
  `[2026-08-10 00:10:00.153 +02:00] [INF] ... Recording stopped: ".../Brewers  MIN @ MIL 2026_08_09_20_05_00.ts"`
  (i.e. Jellyfin itself believed the recording ran fine until its padded end
  time — it never saw the 22:34 stream death; only the file's actual bytes
  reveal the truncation).
- `get_existing_timers()` really exists (line 371) and really calls the REST
  API; `is_overlapping_timer()` elsewhere in the same file already correctly
  filters on `("Completed", "Cancelled", "Error")` against the on-disk
  vocabulary — i.e. the codebase already "knew" disk and API use different
  status vocabularies in one place but not in `check_stalled_recordings()`.
- **A second, fully independent E2E test harness** (`test-watchdog-e2e.service`
  / `/root/test-watchdog-restore-stitch-e2e.sh`, built the day before this
  investigation, unrelated to agy's report) had already caught this same
  failure for real the night before: `FAIL: Auto-restore did not trigger
  within 30 minutes of the induced stall` (2026-08-09 22:45:41), against a
  disposable synthetic recording — same bug, independently discovered by a
  completely different mechanism.

## Fix deployed

`check_stalled_recordings()` now sources active timers from
`get_existing_timers()` (the API, where `InProgress` is real), enriches
`RecordingPath` from the on-disk file if the API DTO omits it, and falls
back to a disk-based time-window check (`StartDate <= now <= EndDate`,
`Status not in (Completed, Cancelled)`) if the API is unreachable. Backup of
the pre-fix script: `/root/agy-reports/sports-dvr-auto-pre-watchdog-fix-20260810.bak`.

Verified before and after deploy:
- `python3 -m py_compile` clean.
- Manual dry-run (`--stall-check-only`, no `--apply`) as root: clean exit 0,
  no exceptions.
- Real systemd invocation (`systemctl start sports-dvr-auto.service`, the
  exact same `--apply` command the timer uses): exit 0/SUCCESS, clean log,
  no errors.
- Re-ran the independent E2E harness (`test-watchdog-e2e.service`) against
  the fixed script for a positive real-world confirmation — see
  `/var/log/watchdog-e2e-test.log` for the result (takes ~45-90 min,
  runs unattended, emails status at each milestone).

## Still open

- Confirm the E2E re-run passes end to end (stall detected, restore created,
  stitch completes) once it finishes.
- The original truncated Aug 9 recording itself
  (`/srv/media-core/media/recordings/Other/Brewers  MIN @ MIL/...ts`,
  ~2h35m) was never restored/stitched and still sits truncated — the game is
  long over, so nothing to auto-restore for it now; flagging in case the
  owner wants it manually re-acquired some other way (rebroadcast/replay if
  the provider offers one) or just deleted.
- Once the E2E re-run confirms clean, safe to consider re-enabling Brewers
  auto-record in `sports-config.json` (currently `false`).
