# End-to-End Recording Rehearsal & Verification Report

**Date:** Friday, 28 August 2026 (08:16 CEST)  
**Host:** `pve-01` (Proxmox VE 9.2)  
**Target Media Stack:** CT 105 (`media-core`, 192.168.9.50) — Jellyfin + Threadfin via Swiss WireGuard egress  

---

## 1. Executive Summary & Ranked Risks

A full live end-to-end rehearsal was conducted across both channels, tuning real streams through the exact recording path, analyzing tuner contention, verifying write permissions, testing EPG/ProgramId resolution, and inspecting the automation state machines.

### Risk Ranking (Highest Consequence to Lowest)

| Rank | Severity | Issue / Finding | Impact & Mitigation |
|:---:|:---:|:---|:---|
| **1** | **MEDIUM-HIGH** | **Timer Name Mismatch in PPV Probe Logic (`Live: BL` vs `(German feed)`)** | In Jellyfin, the existing German timer is named `"Live: BL"`. `sports-dvr-auto` expects disposable fallbacks to end with `(German feed)`. Because of this name mismatch:<br>1. Every 5 min, `sports-dvr-auto` attempts to create a fallback timer and logs `ppv_fallback_scheduled FAILED` because Jellyfin rejects the duplicate `ProgramId` (benign while no English slot exists).<br>2. If an English PPV slot is dynamically resolved ~6h before kickoff, `run_ppv_probe_commit()` will start the English probe but will **NOT** cancel `"Live: BL"`, leading to two timers competing for a single IPTV tuner at 19:50 CEST.<br>**Safety Net:** If no English PPV slot is labeled (the expected baseline for Friday Bundesliga), the probe never fires and `"Live: BL"` records cleanly from 18:55 to 23:45 CEST. If a probe does fire and fails, `"Live: BL"` was never cancelled and remains armed. |
| **2** | **LOW** | **Single Tuner Account Constraint (1 Stream Max)** | Threadfin advertises `TunerCount: 1` and the Xtream provider account allows only 1 concurrent stream. Any accidental manual viewing on CT 105 during 18:55–23:45 CEST or 01:55–05:30 CEST will contend with the active recording. |
| **3** | **PASS / ZERO RISK** | **Tuner Overlap Between Events** | Event 1 ends at 23:45 CEST (with padding); Event 2 starts at 01:55 CEST (with padding). There is a **2h 10m (130-minute) clean window** between them. |
| **4** | **PASS / ZERO RISK** | **Disk Storage Space** | 779 GB available on `/srv/media-core`. Total estimated storage for both padded HD captures is **~18.42 GB** (**~42.3x headroom**). |
| **5** | **PASS / ZERO RISK** | **Channel Tunability & Decodability** | Both channels tuned via Threadfin over the Swiss WireGuard tunnel, producing non-zero, healthy, fully-decodable video and audio with 0 demux/decode errors. |
| **6** | **PASS / ZERO RISK** | **Write Path Permissions** | Jellyfin process runs as root (UID 0); `/media/recordings/Sports` verified writable from inside the container. |
| **7** | **PASS / ZERO RISK** | **EPG / ProgramId Validity** | Both `b84f5c859144f14e08641610353ecde9` and `c71ed0faf19726f7f57dd4c4432581ec` resolve to the exact expected matches and air times. |
| **8** | **PASS / ZERO RISK** | **Host Shutdown Guard & Power Override** | Keep-awake override is active until `Tue 06:00` (`2026-09-01T06:00:00+02:00`). `dvr-clean-shutdown` dry-run confirmed host will stay up. |

---

## 2. Real Channel Tuning & Stream Verification

Both channels were tuned through the exact path a Jellyfin recording uses: `http://127.0.0.1:34400/stream/<id>` inside CT 105 via Threadfin and the Swiss WireGuard tunnel. Short ~25s samples were captured, inspected via `ffprobe`, fully decoded via `ffmpeg -f null`, and immediately deleted.

### Event 1: Bayern Munich v VfB Stuttgart
* **Channel:** `Sky Sport Bundesliga 1 HD (720P)` (ChannelNumber `1001`, ExternalChannelId `hdhr_1001`)
* **Jellyfin ChannelId:** `a7c46bb1eb1b1fdcd57caa020657707f`
* **Threadfin Stream URL:** `http://127.0.0.1:34400/stream/326357761d574d24e44c9d14b0fb77b5`
* **Test Capture Command:**
  ```bash
  pct exec 105 -- docker exec jellyfin /usr/lib/jellyfin-ffmpeg/ffmpeg -t 25 \
    -i "http://127.0.0.1:34400/stream/326357761d574d24e44c9d14b0fb77b5" \
    -c copy -y /tmp/sample_sky.ts
  ```
* **ffprobe Results:**
  * **Container:** MPEG-TS (`mpegts`)
  * **Video Codec:** `hevc` (Main Profile, progressive)
  * **Resolution / FPS:** `1920x1080` @ 50 fps (Full HD, despite the "720P" legacy name)
  * **Audio Codec:** `aac` (LC, 48000 Hz, stereo, 127 kbps, language: `deu`)
  * **Sample Size / Duration:** 16,312,008 bytes (~15.56 MB) over 25.205s
  * **Observed Bitrate:** 5,177,387 bps (**~5.18 Mbps**)
  * **Decode Check (`ffmpeg -v error -i /tmp/sample_sky.ts -f null -`):** **0 errors (100% clean, fully decodable)**
* **Cleanup:** File `/tmp/sample_sky.ts` deleted immediately.

### Event 2: Arizona Cardinals @ Green Bay Packers
* **Channel:** `Green Bay: NBC 26 (WGBA)` (ChannelNumber `106`, ExternalChannelId `hdhr_106`)
* **Jellyfin ChannelId:** `3f3e6cca9a2e5273f4144a5003ef719e`
* **Threadfin Stream URL:** `http://127.0.0.1:34400/stream/17f75d93274c948cebc9f445589180f6`
* **Test Capture Command:**
  ```bash
  pct exec 105 -- docker exec jellyfin /usr/lib/jellyfin-ffmpeg/ffmpeg -t 25 \
    -i "http://127.0.0.1:34400/stream/17f75d93274c948cebc9f445589180f6" \
    -c copy -y /tmp/sample_nbc26.ts
  ```
* **ffprobe Results:**
  * **Container:** MPEG-TS (`mpegts`)
  * **Video Codec:** `h264` (High Profile, progressive)
  * **Resolution / FPS:** `1920x1080` @ 59.94 fps
  * **Audio Codec:** `aac` (Main Profile, 48000 Hz, stereo, 95 kbps, language: `eng`)
  * **Sample Size / Duration:** 14,308,116 bytes (~13.64 MB) over 25.076s
  * **Observed Bitrate:** 4,564,596 bps (**~4.56 Mbps**)
  * **Decode Check (`ffmpeg -v error -i /tmp/sample_nbc26.ts -f null -`):** **0 errors (100% clean, fully decodable)**
* **Cleanup:** File `/tmp/sample_nbc26.ts` deleted immediately.

---

## 3. Tuner Contention & Scheduling Audit

### Tuner Capacity
* **Threadfin (`/srv/media-core/threadfin/conf/settings.json`):** `"tuner": 1`
* **Threadfin Discovery (`/discover.json`):** `"TunerCount": 1`
* **Jellyfin Config (`/config/config/livetv.xml`):** Points to Threadfin (`http://127.0.0.1:34400`).
* **Available Concurrent Streams:** Exactly **1 stream**.

### Recording Windows & Timeline Verification

```
Time (CEST)       18:55                                     23:45         01:55                   05:30
                  │                                         │             │                       │
Event 1 (Bayern)  ├─────────────────────────────────────────┤             │                       │
                  │ (18:55 CEST to 23:45 CEST)              │             │                       │
                                                                          │                       │
[GAP: 130 MIN]                                              └─────────────┘                       │
                                                                          │                       │
Event 2 (Packers)                                                         ├───────────────────────┤
                                                                          │ (01:55 CEST to 05:30) │
```

* **Event 1 (Bayern v Stuttgart):**
  * Nominal window: Fri 28 Aug 19:00–23:15 CEST (17:00–21:15 UTC)
  * Pre-Padding: 300s (starts **18:55 CEST** / 16:55 UTC)
  * Post-Padding: 1800s (ends **23:45 CEST** / 21:45 UTC)
* **Event 2 (Cardinals @ Packers):**
  * Nominal window: Sat 29 Aug 02:00–05:00 CEST (Sat 00:00–03:00 UTC)
  * Pre-Padding: 300s (starts **01:55 CEST** / Fri 23:55 UTC)
  * Post-Padding: 1800s (ends **05:30 CEST** / Sat 03:30 UTC)
* **Contention Check:** Between Event 1 end (23:45 CEST) and Event 2 start (01:55 CEST), there is an unencumbered gap of **2 hours and 10 minutes (130 minutes)**. The two recordings do not overlap.

### Competing Scheduled Jobs Audit
* `dvr-clean-shutdown.timer`: Next trigger is Sat 00:45 CEST. Override file `/var/lib/dvr-dashboard/override-until` is set to `2026-09-01T06:00:00+02:00` (Tuesday 06:00 CEST). Dry-run output: `override active until Tue 06:00 -- staying up`. Furthermore, `dvr-clean-shutdown` reads Jellyfin timers and detects the Packers recording starting at 01:55 CEST, which would block shutdown even without the override.
* `media-core-sync.timer` (12:01 CEST) and `media-core-xepg.timer` (12:25 CEST): Scheduled in the middle of the day, completely outside recording windows.
* `media-core-ppv.timer` (hourly at minute :07): Executes `/srv/media-core/sync/ppv-refresh.py`. It only calls the Xtream REST API for metadata, generates XMLTV slices, and notifies Threadfin/Jellyfin. It does **not** tune video or restart Threadfin.
* `epg-sync-ct112.timer` (12:28 CEST): Outside recording windows; CT 112 is separate.
* `Jellyfin Scheduled Tasks`: `Refresh Guide` runs at 14:50 CEST, `Scan Media Library` runs at 15:05 CEST. Neither tunes live streams.

---

## 4. Disk Storage & Bandwidth Sizing

### Grounded Sizing Calculation

| Event | Nominal Duration | Padded Duration | Bitrate (Observed) | Padded Size |
|:---|:---:|:---:|:---:|:---:|
| **Bayern Munich v Stuttgart** | 4h 15m (15,300s) | 4h 45m (17,100s) | 5.18 Mbps (0.648 MB/s) | **~11.07 GB** |
| **Cardinals @ Packers** | 3h 00m (10,800s) | 3h 35m (12,900s) | 4.56 Mbps (0.570 MB/s) | **~7.35 GB** |
| **Total Required** | 7h 15m | 8h 20m | — | **~18.42 GB** |

### Storage Headroom
* **Mountpoint:** `/srv/media-core` (`/dev/mapper/pve-vm--105--disk--1`)
* **Total Volume Size:** 984 GB
* **Used:** 155 GB
* **Available Space:** **779 GB** (17% utilization)
* **Headroom Ratio:** $\frac{779\text{ GB}}{18.42\text{ GB}} =$ **~42.3x safety margin**. Storage capacity is abundant and safe.

---

## 5. Write Path & Container Permissions

* **Container User & GID:** Jellyfin runs as `root:root` (UID 0, GID 0) inside the container.
* **Recordings Directory:** `/srv/media-core/media/recordings` (bind-mounted to `/media/recordings` in container).
* **Verification Test:**
  * Executed `docker exec jellyfin sh -c 'touch /media/recordings/Sports/.write_test.tmp && ls -l /media/recordings/Sports/.write_test.tmp && rm -f /media/recordings/Sports/.write_test.tmp'`
  * Verified file creation and confirmed visibility on CT 105 at `/srv/media-core/media/recordings/Sports/.write_test.tmp`.
  * Exit code 0, test file cleaned up immediately.

---

## 6. EPG & ProgramId Validity

Both timer ProgramIds were queried directly via the Jellyfin REST API (`/LiveTv/Programs/<id>`):

### ProgramId 1: `b84f5c859144f14e08641610353ecde9`
```json
{
  "Id": "b84f5c859144f14e08641610353ecde9",
  "Name": "Live: BL",
  "ChannelId": "a7c46bb1eb1b1fdcd57caa020657707f",
  "ChannelName": "Sky Sport Bundesliga 1 HD (720P)",
  "ChannelNumber": "1001",
  "StartDate": "2026-08-28T17:00:00.0000000Z",
  "EndDate": "2026-08-28T21:15:00.0000000Z",
  "Overview": "Aus der Allianz Arena München, Deutschland.",
  "Genres": ["Sports", "Soccer"],
  "Tags": ["Premiere", "Sports"],
  "IsSports": true,
  "TimerId": "b8d81cd897c317d55f66ece3e565ad20",
  "Status": "New"
}
```
* **Status:** Valid, matches Sky Sport Bundesliga 1 HD 19:00–23:15 CEST today.

### ProgramId 2: `c71ed0faf19726f7f57dd4c4432581ec`
```json
{
  "Id": "c71ed0faf19726f7f57dd4c4432581ec",
  "Name": "Live: NFL Football",
  "ChannelId": "3f3e6cca9a2e5273f4144a5003ef719e",
  "ChannelName": "Green Bay: NBC 26 (WGBA)",
  "ChannelNumber": "106",
  "StartDate": "2026-08-29T00:00:00.0000000Z",
  "EndDate": "2026-08-29T03:00:00.0000000Z",
  "Overview": "The Green Bay Packers play host to the Arizona Cardinals in an NFL preseason game at Lambeau Field...",
  "Genres": ["Sports", "Football"],
  "Tags": ["Premiere", "Sports"],
  "IsSports": true,
  "TimerId": "4866966b3e2dfceb6da89941f9ee2471",
  "Status": "New"
}
```
* **Status:** Valid, matches Green Bay NBC 26 02:00–05:00 CEST tonight (Sat morning).

---

## 7. Deep Analysis of the PPV Probe & Fallback Mechanism

### Question: Could a PPV Probe Failure Leave Us With NO Recording?
**Plain Answer: NO.** Under no circumstance does a probe failure delete or leave the system without a recording. However, an important edge case regarding timer names was discovered during the audit.

### How the Logic Works & Why It Fails Safe
1. **Create-Before-Cancel Discipline:**
   * In `run_ppv_probe_commit()`: The English timer is created first and verified in Jellyfin's timer list *before* any cancellation of the German fallback is attempted.
   * If the English timer creation fails for any reason, the German fallback is untouched.
2. **Revert on Probe Failure:**
   * If the English probe fails (i.e. writes `< 20 MB` or times out after 4 minutes):
     ```python
     if not fallback:
         if not ensure_dynamic_ppv_fallback(game, jf_channels, get_existing_timers(), dry_run=dry_run):
             logging.error("[PROBE] %s: could not re-book the German fallback -- KEEPING the English timer rather than leaving the match unrecorded.", game["name"])
             continue
     if english:
         cancel_live_timer(english["Id"], dry_run=dry_run)
     ```
   * The code explicitly checks that the German fallback is successfully booked in Jellyfin before cancelling the English timer. If re-booking fails, it keeps the English timer as a last resort.

### The Live Finding: Name Mismatch Between Timer & Automation
* The automation defines `PPV_FALLBACK_MARKER = "(German feed)"`.
* When searching for existing fallbacks to evaluate, it searches for `fb_name = f"{team}: {game['name']} (German feed)"` (`Bayern Munich: VfB Stuttgart at Bayern Munich (German feed)`).
* The actual timer in Jellyfin is named `"Live: BL"`.
* **Consequences:**
  1. `ensure_dynamic_ppv_fallback()` runs every 5 minutes and tries to create `Bayern Munich: VfB Stuttgart at Bayern Munich (German feed)` with ProgramId `b84f5c859144f14e08641610353ecde9`. Jellyfin rejects this duplicate ProgramId with HTTP 400/409, logging `ppv_fallback_scheduled FAILED`. This is benign because the `Live: BL` timer already exists and protects the slot.
  2. If an English PPV slot is labeled ~6h before kickoff:
     * `run_ppv_probe_commit()` will find `english` timer, but `fallback` will be `None` (because `Live: BL` does not match `fb_name`).
     * `run_ppv_probe_commit()` will **NOT** cancel `Live: BL`.
     * `Live: BL` will start recording at 18:55 CEST.
     * At 19:50 CEST, the English probe will attempt to start. With only 1 tuner in Threadfin, this would cause tuner contention. The English probe would fail to capture bytes (`0 bytes`), time out after 4 minutes, and fail its revert (leaving `Live: BL` recording continuously).
  3. If **NO** English PPV slot is labeled (which was verified overnight and is the normal case for Friday Bundesliga):
     * The PPV probe logic is skipped entirely (`resolve_dynamic_ppv_channel` returns `None`).
     * The `Live: BL` timer executes normally from 18:55 to 23:45 CEST on `Sky Sport Bundesliga 1 HD`.

---

## 8. State Changes & Verification Audit

In compliance with project instructions, no production configs, tunnels, routes, or timers were modified.

| Action / File | Nature of Change | Purpose | Verification Method |
|:---|:---|:---|:---|
| `/tmp/sample_sky.ts` (in Jellyfin container) | Created (25s) & Deleted | Sample stream capture for Sky Sport Bundesliga 1 HD | ffprobe JSON check, ffmpeg null-decode (0 errors), confirmed deleted |
| `/tmp/sample_nbc26.ts` (in Jellyfin container) | Created (25s) & Deleted | Sample stream capture for Green Bay NBC 26 WGBA | ffprobe JSON check, ffmpeg null-decode (0 errors), confirmed deleted |
| `/media/recordings/Sports/.write_test.tmp` | Created & Deleted | Test write permissions in recordings volume | `ls -l` inside container & on CT 105, confirmed deleted |
| `/root/agy-reports/recording-rehearsal.md` | Created | Report deliverable | Verified written to disk |
