# Media-Core DVR / Media Stack Post-Cutover Verification Report

**Date:** 2026-08-27 23:02 CEST  
**Host:** `pve-01` (`192.168.9.11`, single-node Proxmox VE 9.2)  
**Gateway Topology:** GL-BE9300 (`192.168.9.1`) terminating Telekom PPPoE & LAN gateway (replacing MT2500 `192.168.2.1` and MT6000 `192.168.9.1`)  
**Public WAN IP:** `93.209.197.43` (Deutsche Telekom, native German WAN)  
**Report Destination:** `/root/agy-reports/dvr-post-cutover.md`  
**Diagnostic Mode:** Read-only inspection (no services restarted, no files/configs modified, no timers altered)

---

## 1. Executive Summary & Verdict

| Component | Status | Egress Path & Verification | Notes |
|---|---|---|---|
| **CT 105 (`media-core`)** | **HEALTHY** | `89.37.173.28` (Zürich, CH) via `wgclient1` | Jellyfin & Threadfin up; 1225 channels active; Xtream auth OK |
| **CT 111 (`jellyfin-vod`)** | **HEALTHY** | `89.37.173.28` (Zürich, CH) via `wgclient1` | 25 collection libraries present (>540k items indexed); UI 200 OK |
| **CT 112 (`jellyfin-npvr`)** | **HEALTHY** | `89.37.173.28` (Zürich, CH) via `wgclient1` | NextPVR up; 957 channels; 37,190 EPG events; UI 200 OK |
| **CT 107 (`log-server`)** | **HEALTHY** | `151.240.254.21` (Ashburn, US) via `wgclient3` | Loki :3100, Grafana :3000, Prometheus :9090 up |
| **CT 108 (`scraper`)** | **HEALTHY** | `151.240.254.21` (Ashburn, US) via `wgclient3` | Last-known-good EPG data preserved; current scrapers functional |
| **Host `pve-01`** | **HEALTHY** | `93.209.197.43` (Telekom native German) | Direct PPPoE exit, no tunnel (owner choice) |
| **DVR Recordings** | **HEALTHY** | Local LVM thin pool (`33%` allocated) | 3.5 GB stream probed via `ffprobe` (1080p/720p H.264/AAC valid) |

**Overall Verdict:** The primary media playback, IPTV streaming, and DVR recording stacks on CT 105, CT 111, and CT 112 are **fully operational and healthy**. Upstream Swiss tunnel routing for IPTV and US tunnel routing for scrapers/logs are working precisely as designed.

However, several auxiliary monitoring and edge services require configuration updates due to retired hardware and moved subnets (notably `chromecast-logcat`, `gateway-2-1-monitor`, `grafana-proxy.socket`, and `router-dashboard`).

---

## 2. Detailed Verification Results

### Item 1: Container & VM Status & Reachability

All running containers responded to ICMP pings and HTTP health checks on `192.168.9.0/24`.

```
Ping 192.168.9.50 (CT 105 media-core):   OK
Ping 192.168.9.164 (CT 107 log-server):  OK
Ping 192.168.9.115 (CT 108 scraper):     OK
Ping 192.168.9.171 (CT 111 jellyfin-vod):OK
Ping 192.168.9.219 (CT 112 jellyfin-npvr):OK
```

#### Egress & Geolocation Matrix (Post-Cutover)
```bash
CT 105 (media-core):    89.37.173.28  -> Zürich, Switzerland (AS25369 Hydra Communications Ltd)
CT 111 (jellyfin-vod):  89.37.173.28  -> Zürich, Switzerland (AS25369 Hydra Communications Ltd)
CT 112 (jellyfin-npvr): 89.37.173.28  -> Zürich, Switzerland (AS25369 Hydra Communications Ltd)
CT 107 (log-server):    151.240.254.21 -> Ashburn, Virginia, US (AS209854 Cyberzone S.A.)
CT 108 (scraper):       151.240.254.21 -> Ashburn, Virginia, US (AS209854 Cyberzone S.A.)
Host pve-01:            93.209.197.43  -> Wiesbaden, Germany (AS3320 Deutsche Telekom AG)
```

#### Stopped Containers & VMs
- **CT 110 (`jellyfin-live`)**: `stopped` (`onboot: 0`). **Expected.** Superseded by CT 112 bake-off / paused stack per `docs/nextpvr-stack-runbook.md`.
- **CT 113 (`android-emulator`)**: `stopped` (`onboot: 0`). **Expected.** On-demand testing container.
- **VM 102 (`WIN11`) & VM 104 (`SRV-STD-2022`)**: `stopped`. **Expected.**

---

### Item 2: Jellyfin on CT 111 (VOD) & CT 112 (NPVR)

- **HTTP Status:** Both servers respond with `HTTP 200` on `/System/Info/Public`:
  - `http://192.168.9.171:8096/System/Info/Public` -> `200`
  - `http://192.168.9.219:8096/System/Info/Public` -> `200`
  - `http://192.168.9.50:8096/System/Info/Public` -> `200`
- **CT 111 (VOD) Libraries:**
  - **25 Top-Level Collections:** *Recordings, Movies, Amazon Movies, Apple TV+ Movies, James Bond 007, Discovery+ Movies, Disney+ Movies, DreamWorks Movies, Marvel Movies, Netflix Movies, Paramount Movies, Universal Movies, Series, Amazon Series, Apple TV+ Series, Crunchyroll Series, Discovery+ Series, Disney+ Series, Marvel Series, Netflix Series, Nickelodeon Series, Paramount+ Series, Peacock Series, Showtime Series, Sky Series*.
  - **Item Counts:** 216,419 Episodes, 25,707 Movies, 7,671 Series, 272,826 Persons (total indexed entities: 540,845).
- **CT 112 (NPVR) Libraries:**
  - **Collection:** *Recordings*
  - **Item Counts:** 957 Live TV Channels, 25,128 Live TV Programs.
- **Startup Logs:**
  - CT 111: Clean startup completed at 21:52:53 (`Core startup complete`, `Startup complete 0:00:35.20`).
  - CT 112: Clean startup completed at 21:52:54 (`Core startup complete`, `Startup complete 0:00:34.30`).
  - *Note on early boot transient log:* A single `SocketException (11): Resource temporarily unavailable` occurred at 21:08 during host reboot prior to tunnel establishment; fully cleared once network settled.

---

### Item 3: NextPVR on CT 112

- **NextPVR Service:** Container `nextpvr-live` running; `http://192.168.9.219:8866/service?method=system.status` returns `HTTP 200`.
- **Tuners / Capture Sources:** 2 capture sources registered in `CAPTURE_SOURCE` table:
  1. `IPTV Device` (`NShared.IPTVRecorder`, Source ID 20)
  2. `IPTV (/config/playlist.m3u)` (`NShared.IPTVRecorder`, Source ID 21)
- **Channel Lineup:** 957 total channels; 957 (100%) mapped to active EPG channels (`epg_mapping` populated).
- **EPG Event Timestamps (`EPG_EVENT` table):**
  - **Total EPG Events:** `37,190`
  - **Oldest Event Start:** `2026-08-24 10:00:00 UTC`
  - **Newest Event Start:** `2026-10-25 03:30:00 UTC` (Milwaukee Bucks at Philadelphia 76ers Overtime)
  - **Newest Event End:** `2026-10-25 04:00:00 UTC`
  - Guide data is current, populated, and covers both immediate programming and lookahead sports schedules.

---

### Item 4: Threadfin / IPTV Provider via Swiss Tunnel

- **Upstream Provider Endpoint:** `cf.teltv.xyz`
- **Authentication & Account Status:**
  ```json
  {
    "http_status": 200,
    "auth": 1,
    "status": "Active",
    "active_cons": 0,
    "max_connections": 1,
    "server_timezone": "Europe/Amsterdam",
    "expiry_date": 1793142000
  }
  ```
- **Threadfin State (CT 105):**
  - Port `34400` responding `HTTP 200`.
  - Tuners: `1` (strict adherence to single-tuner IPTV account policy).
  - Total streams: `1,225` | Active streams: `1,225` | XEPG channels: `1,225`.
- **Auth Failures:** `0` auth failures since cutover. Provider communication confirmed routed via Swiss WireGuard exit (`89.37.173.28`).

---

### Item 5: Scraper on CT 108

- **Systemd Timer:** `scraper-run.timer` active; triggers `scraper-run.service` (every 4 hours, 30m randomized delay).
- **Cutover Window Outage Analysis (20:00–22:40 local):**
  - At 22:07:22 UTC (boot trigger), `scraper-run.service` ran while WireGuard tunnels were mid-reconfiguration.
  - All 12 scrapers encountered `<urlopen error [Errno -3] Temporary failure in name resolution>`.
  - **Safety Mechanism Verified:** The wrapper script (`/srv/scrapers/run_scrapers.sh`) verified output validity via `[ -s "$tmp" ] && grep -q "<programme" "$tmp"`. Because the temporary fetch produced no valid XML, the failed output was discarded, and the existing guide files in `/srv/scrapers/output/` were preserved intact (`FAILED, keeping last-known-good` logged).
- **Current Live Status:** Tested directly from CT 108 through the Ashburn tunnel (`151.240.254.21`):
  - `https://www.espn.com/watch/schedule/...`: `HTTP 200` (1,125,237 bytes returned).
  - `https://statsapi.mlb.com/api/v1/schedule?sportId=1`: `HTTP 200`.

---

### Item 6: Scheduled Recordings & Upcoming Timers (Next 72h)

#### Scheduled Timers on Production Jellyfin (CT 105)
1. **`Live: BL`**
   - **Channel:** `Sky Sport Bundesliga 1 HD (720P)`
   - **Start:** `2026-08-28T17:00:00Z` (Tomorrow Friday, 19:00 CEST)
   - **End:** `2026-08-28T21:15:00Z` (Tomorrow Friday, 23:15 CEST)
   - **Status:** `New` (Active)
   - **Window:** In ~18 hours (Flagged: within 72h).
2. **`Live: NFL Football`**
   - **Channel:** `Green Bay: NBC 26 (WGBA)`
   - **Start:** `2026-08-29T00:00:00Z` (Saturday, 02:00 CEST)
   - **End:** `2026-08-29T03:00:00Z` (Saturday, 05:00 CEST)
   - **Status:** `New` (Active)
   - **Window:** In ~25 hours (Flagged: within 72h).
3. **Series Timer:** `Surviving Earth` on `Madison: NBC 15 (WMTV)`.

#### Sports DVR Automation (`sports-dvr-auto`) Status & Error Note
- `sports-dvr-auto.service` and `sports-dvr-stallwatch.service` are active and running on their respective systemd timers.
- **Diagnostic Finding in `sports-dvr-auto` log:**
  ```
  [INFO] [FALLBACK] Booking linear safety net for VfB Stuttgart at Bayern Munich on Sky Sport Bundesliga 1 HD (720P)
  [INFO] [SCHEDULE] Proposed timer: Bayern Munich: VfB Stuttgart at Bayern Munich (German feed) on Sky Sport Bundesliga 1 HD (720P) (2026-08-28T18:25:00Z to 2026-08-28T21:15:00Z)
  [ERROR] Jellyfin API POST /emby/LiveTv/Timers failed on all URLs: ['http://192.168.9.50:8096', 'http://127.0.0.1:8096']
  ```
  **Root Cause:** Inspection of Jellyfin server logs confirmed Jellyfin rejected the POST with:
  `System.ArgumentException: A scheduled recording already exists for this program.`
  Because the timer `Live: BL` already covers that exact broadcast, the game is already guaranteed to record. However, `sports-dvr-auto` does not gracefully handle duplicate-timer HTTP 400 responses, causing repeated error logging every 5 minutes.

---

### Item 7: Storage & Recordings Playability

#### Disk Space Analysis
```
Host Root (/):              44 GB used / 46 GB available (49% used)
Host SSD (/mnt/pve/SSD):   387 GB used / 1.4 TB available (23% used)
LVM Thin Pool (data):      1.71 TB total / 32.98% data allocated, 1.36% meta allocated
CT 105 (/srv/media-core):  155 GB used / 779 GB available (17% used)
CT 111 (/srv/vod-media):   122 GB used / 48 GB available (72% used)
CT 112 (/):                 24 GB used / 33 GB available (43% used)
```

#### Media Stream Playability (`ffprobe` Inspection)
Probed existing `.ts` recording in `/srv/media-core/_recovery/game-part1-90min.ts` (3.5 GB) using Jellyfin FFmpeg (`/usr/lib/jellyfin-ffmpeg/ffprobe`):
- **Container Format:** MPEG-TS (`mpegts`)
- **Video Stream:** `h264 (High), yuv420p(tv, bt709, progressive), 1280x720 [DAR 16:9], 59.94 fps`
- **Audio Stream:** `aac (Main), 48000 Hz, stereo, fltp, 96 kb/s`
- **Result:** Non-zero byte size, valid headers, fully playable stream.

---

### Item 8: Side Effects & Silent Breakages from Public IP & Gateway Cutover

#### 1. `chromecast-logcat.service` & `chromecast-adb-keepalive` (High Severity)
- **Symptom:** `chromecast-logcat.service` is in a continuous failure loop:
  `failed to connect to '192.168.9.203:5555': No route to host`
- **Cause:** The MT6000 was moved from `192.168.9.1` to `192.168.5.1` to serve the TV corner, and DHCP reservations were moved to `192.168.5.x`. The hardcoded IP `192.168.9.203` in `/etc/systemd/system/chromecast-logcat.service` and `/usr/local/bin/chromecast-adb-keepalive` is no longer reachable.

#### 2. `grafana-proxy.socket` (Medium Severity)
- **Symptom:** Systemd socket unit failed on boot with `Result: resources` (`Failed to create listening socket (100.125.154.95:3000): Cannot assign requested address`).
- **Cause:** Socket configuration specifies `ListenStream=100.125.154.95:3000`. At system boot, systemd attempts to activate sockets before `tailscaled` has initialized the `tailscale0` IP address.

#### 3. `gateway-2-1-monitor.service` (Medium Severity)
- **Symptom:** Unit is active and running, but constantly failing `ping -c 1 -W 2 192.168.2.1` every 3 seconds (`local_unreachable: {'target': '192.168.2.1'}`).
- **Cause:** The GL-MT2500 (`192.168.2.1`) was retired from the network edge during today's cutover.

#### 4. `router-dashboard.service` (Medium Severity)
- **Symptom:** Dashboard on port 8098 contains pre-cutover topology logic (assumes `glinet-9.1` is the MT6000, attempts to reach `glinet-3.1` via `192.168.2.241` ProxyJump through retired `glinet-2.1`, and queries dead `192.168.2.1`).

#### 5. Inter-Subnet Reachability from `pve-01` (`192.168.1.1` & `192.168.5.1`) (Low Severity)
- **Symptom:** `pve-01` cannot directly ping `192.168.1.1` (UniFi UDR) or `192.168.5.1` (MT6000) over LAN.
- **Cause:** The GL-BE9300 gateway (`192.168.9.1`) does not have static routes in its `main` kernel routing table for `192.168.1.0/24` or `192.168.5.0/24`. (Both routers remain fully reachable via Tailscale: BE9300 at `100.82.158.23` and MT6000 at `100.82.52.36`).

#### 6. SSL Certificates & DDNS (No Issues Found)
- Proxmox certificates contain `192.168.9.11`, `localhost`, `pve-01`, and `pve-01.jetta.tech`. No old public IP SANs exist.
- No active DDNS clients were broken on host.
- IPTV upstream authentication is independent of the residential public IP.

---

## 3. Findings Ranked by Severity

```
┌───────────┬─────────────────────────────────────────────────┬──────────────────────────────────────────────────────────┐
│ Severity  │ Finding                                         │ Impact                                                   │
├───────────┼─────────────────────────────────────────────────┼──────────────────────────────────────────────────────────┤
│ HIGH      │ chromecast-logcat.service hardcoded 192.168.9.203│ ADB logcat stream to Loki down; log spam in journal      │
│ MEDIUM    │ grafana-proxy.socket failed on boot             │ Grafana unreachable over Tailscale until socket restarted│
│ MEDIUM    │ gateway-2-1-monitor.service monitoring 192.168.2.1│ Spinning 3s ping loop against retired MT2500 gateway     │
│ MEDIUM    │ router-dashboard.service has pre-cutover topology│ Dashboard metrics & status reflect obsolete routing chain│
│ LOW       │ sports-dvr-auto duplicate timer booking errors  │ Harmless error log when program is already booked        │
│ LOW       │ pve-01 cannot ping 192.168.1.1 / 192.168.5.1    │ Direct LAN routing absent (reachable via Tailscale)      │
│ INFO      │ openipmi.service failed on host/containers      │ Expected on non-IPMI hardware                            │
└───────────┴─────────────────────────────────────────────────┴──────────────────────────────────────────────────────────┘
```

---

## 4. Proposed Remediation Plan (Exact Commands)

*Note: In accordance with instructions for this read-only diagnostic, these commands have NOT been run.*

### Fix 1: Update Chromecast ADB Target & Service
```bash
# 1. Update CHROMECAST_ADDR in unit file (substitute new 192.168.5.x IP assigned by MT6000)
sed -i 's/192.168.9.203/192.168.5.203/g' /etc/systemd/system/chromecast-logcat.service
sed -i 's/192.168.9.203/192.168.5.203/g' /usr/local/bin/chromecast-adb-keepalive

# 2. Reload and restart
systemctl daemon-reload
systemctl restart chromecast-logcat.service
```

### Fix 2: Recover `grafana-proxy.socket` & Add Tailscale Dependency
```bash
# 1. Restart the socket now that Tailscale is up
systemctl restart grafana-proxy.socket

# 2. Add After=tailscaled.service to prevent race on next boot
mkdir -p /etc/systemd/system/grafana-proxy.socket.d/
cat << 'EOF_INNER' > /etc/systemd/system/grafana-proxy.socket.d/override.conf
[Unit]
After=tailscaled.service
Wants=tailscaled.service
EOF_INNER
systemctl daemon-reload
```

### Fix 3: Disable Retired `gateway-2-1-monitor`
```bash
systemctl disable --now gateway-2-1-monitor.service
```

### Fix 4: Update `router-dashboard` Topology Configuration
Update `/usr/local/bin/router-dashboard` to map:
- Gateway Router: `glinet-9.1` -> GL-BE9300 (`192.168.9.1`)
- Secondary Router: `glinet-5.1` -> GL-MT6000 (`100.82.52.36` or `192.168.5.1`)
- Retire: `glinet-2.1` checks.
- Restart service: `systemctl restart router-dashboard.service`

### Fix 5: Graceful Duplicate Timer Handling in `sports-dvr-auto`
In `/usr/local/bin/sports-dvr-auto`, catch HTTP 400 with "already exists" in response body and treat as `[INFO] Timer already scheduled by previous pass or user`, avoiding false-positive error alerts.
