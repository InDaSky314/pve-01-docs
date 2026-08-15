# Sports DVR automation (`sports-dvr-auto`)

**Executable Location:** `/usr/local/bin/sports-dvr-auto`  
**Systemd Units:** `sports-dvr-auto.timer` (runs every 5 minutes), `sports-dvr-auto.service`  
**State Directory:** `/var/lib/dvr-dashboard/` (`recording-stall-state.json`, `recording-restore-state.json`, `dvr-automation-events.jsonl`)  
**Target Container:** CT 105 (`media-core`, Jellyfin REST API at `http://127.0.0.1:8096`)

```
                          ┌───────────────────────────┐
                          │   ESPN Public Site API    │
                          └─────────────┬─────────────┘
                                        │ (Fetch schedules for 5 teams)
                                        ▼
┌───────────────────┐     ┌───────────────────────────┐     ┌───────────────────────────┐
│ systemd timer     ├────►│  /usr/local/bin/          ├────►│ Jellyfin REST API         │
│ (every 5 min)     │     │  sports-dvr-auto          │     │ (http://127.0.0.1:8096)   │
└───────────────────┘     └─────────────┬─────────────┘     └─────────────┬─────────────┘
                                        │                                 │
                 ┌──────────────────────┼──────────────────────┐          │ (POST /LiveTv/Timers)
                 ▼                      ▼                      ▼          ▼
        ┌────────────────┐    ┌──────────────────┐   ┌──────────────────┐
        │ Auto-Scheduler │    │  Live Extender   │   │ Watchdog &       │
        │ (Lookahead 7d) │    │  & Post Trim     │   │ Multi-Stitcher   │
        └────────────────┘    └──────────────────┘   └─────────┬────────┘
                                                               │
                                                               ▼
                                                     ┌──────────────────┐
                                                     │ ffmpeg concat &  │
                                                     │ Segment Archive  │
                                                     └──────────────────┘
```

#### Core Components & Lifecycle

1. **Auto-Scheduling (Pre-Game Lookahead):**
   - Polls ESPN's site API (`/apis/site/v2/sports/{sport}/teams/{team}/schedule`) for 5 tracked teams (Packers, Badgers, Bucks, Brewers, and secondary Wisconsin sports).
   - Resolves target channels dynamically using network shortNames (`media.shortName`), explicit RSN mappings (`Milwaukee Brewers HD`, `Milwaukee Bucks HD`), and team local-market preferences (`LOCAL_MARKET_PREFERENCE = {"Packers": "Green Bay"}`).
   - Avoids scheduling streaming exclusives (`Peacock`, `Netflix`, `Prime Video`, `Apple TV`, `Hulu`).
   - Overrides defective XMLTV EPG program durations with realistic baselines (MLB: 3h15m, NFL/CFB: 3h45m, NBA: 2h45m).
   - Creates Jellyfin timers via `POST /emby/LiveTv/Timers` with `"IsSports": True` and pre-padding.

2. **Live Monitoring, Auto-Extension & Trimming:**
   - Evaluates active Jellyfin timers against live ESPN game states (`state == "in"` vs `state == "post"`).
   - When a game is active (`state == "in"`) and less than 15 minutes remain on the Jellyfin timer, extends the timer end time by +20 minutes.
   - When a game transitions to final (`state == "post"` or `completed == True`), trims the remaining timer end time to `now + 5 minutes`, freeing tuners and avoiding post-game noise.

3. **Stall Watchdog & Auto-Restore:**
   - Monitors active recordings (`Status == "InProgress"` via Jellyfin REST API).
   - Resolves live recording `.ts` paths on CT 105 via `resolve_active_recording_path()`.
   - Tracks file size growth across 5-minute ticks (`STALL_MIN_GROWTH_BYTES = 2,000,000`). If a recording stops growing well before its scheduled end, logs a stall and triggers an auto-restore timer creation on Jellyfin.
   - Preserves all Jellyfin timer metadata (`IsSports`, `IsMovie`, `Genres`, `Priority`) on restored timers to ensure recordings land in the correct library.

4. **Multi-Segment Chain Stitching & Archiving:**
   - `build_restore_chain()` builds complete N-segment restore chains across multiple stall/restore events for a single game.
   - Checks timeline continuity (`recording_file_mtime`) between consecutive segments to detect implausible overlaps.
   - Concatenates N segments into a single `(stitched).ts` file using `ffmpeg concat` demuxer inside CT 105.
   - `archive_segments()` moves raw segment files into `/media/segments-archive` (outside Jellyfin's virtual library paths), rendering raw segments invisible in the Jellyfin DVR UI while preserving data on disk.
   - Triggers Jellyfin `/Library/Refresh`.

5. **Archived Segment Cleanup Policy:**
   - `check_archived_segment_cleanup()` automatically deletes raw archived segments from `/media/segments-archive` when:
     - The owner deletes the primary stitched recording in Jellyfin UI (`file_exists_on_ct105(stitched_path)` is false), OR
     - 60 days have elapsed since stitching (`ARCHIVE_CLEANUP_FALLBACK_DAYS = 60`).

6. **Permanent Automation Event Logging:**
   - All extend, trim, stall, restore, stitch, and cleanup events append to `/var/lib/dvr-dashboard/dvr-automation-events.jsonl` via `log_event()`.
   - Accessible via CLI: `sports-dvr-auto --history 30`.

---

## 4. Open Questions & Ambiguities for Human Review

Before committing code or updating repo documentation, the owner / maintainer should review the following points:

1. **Packers Local Market Preference vs. National Cable Overrides:**
   - `LOCAL_MARKET_PREFERENCE` in `sports-dvr-auto` maps Packers games to Green Bay affiliates (`Green Bay: CBS 5 (WFRV)`, `Fox 11`, `NBC 26`, `ABC 2`).
   - *Question:* For Packers games broadcast simultaneously on national cable (e.g. ESPN / NFL Network) and a local Green Bay broadcast affiliate, is Green Bay local affiliate always preferred over national cable?

2. **Archived Segment Retention Fallback Window:**
   - `ARCHIVE_CLEANUP_FALLBACK_DAYS` is hardcoded to 60 days in `sports-dvr-auto`.
   - *Question:* Is 60 days appropriate for automatic purging of raw archived segments when the stitched recording hasn't been deleted manually, or should this window be configurable via `/etc/sports-dvr-auto.conf`?

3. **File Naming & Path Conventions in Repo:**
   - The deployed sports script lives at `/usr/local/bin/sports-dvr-auto` (no `.py` extension), whereas initial design files in `/root/agy-reports/` used `sports_dvr_auto.py`.
   - *Question:* When copying live scripts into the docs repo under `scripts/`, should the file be named `scripts/sports-dvr-auto` or `scripts/sports_dvr_auto.py`?

4. **Postfix Email Relay Dependency:**
   - `alert-responder.py` uses `/usr/sbin/sendmail` (relaying via host postfix configured for `kopr.notify@gmail.com:465`).
   - *Question:* Should `alert-responder.py` include direct SMTP fallback logic if host postfix is unavailable or restarting?
