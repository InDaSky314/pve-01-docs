# pve-01 / Project Media-Core

One document for the whole project: a single-node Proxmox homelab (`pve-01`)
whose main job is **Media-Core** — a self-hosted sports DVR + VOD stack
(Jellyfin + Threadfin + a custom Xtream-API sync) running in an LXC behind a
per-device Swiss VPN tunnel.

> ⚠️ This repo documents a private home network. Keep it **private** on
> GitHub. It contains internal addressing but **no secrets** — provider
> credentials and passwords live only on the server (see [Secrets](#secrets)).

**Status (2026-07-05): fully deployed and verified end-to-end.**
Remaining user-side niceties are listed in [Loose ends](#loose-ends).

---

## 1. Quick reference

| What | Where |
|---|---|
| Proxmox web UI | <https://192.168.9.11:8006> (root) |
| Jellyfin | <http://192.168.9.50:8096> (user `root`; password set 2026-07-05, change in UI) |
| Threadfin UI | <http://192.168.9.50:34400/web/> (web auth enabled — create the first user on next visit) |
| Router (GL-MT6000 "Flint 2") | <http://192.168.9.1> (password not in this repo) |
| Media container | `pct exec 105 -- bash` on pve-01 (CT has no SSH — disabled on purpose) |
| Stack home | `/srv/media-core` inside CT 105 |
| Health check | `curl -so /dev/null -w '%{http_code}' http://192.168.9.50:8096` → 200; same for `:34400/web/` |
| Sync (playlist/EPG/VOD) | `systemctl status media-core-sync.timer` in CT; manual run: `python3 /srv/media-core/sync/xtream-sync.py` |
| VPN check | `pct exec 105 -- wget -qO- https://am.i.mullvad.net/json` → must say Switzerland |

## 2. Hardware & host

- Intel Celeron N5105 mini-PC, 4 cores, 32 GB RAM, 4× 2.5GbE Intel NICs.
- Proxmox VE 9.2.4 on Debian 13 "trixie".
- **Unusual:** a full KDE Plasma 6.3 desktop (`task-kde-desktop`, SDDM,
  user `nate`) runs directly on the hypervisor, so the box doubles as a
  workstation. Consequences:
  - The N5105 iGPU belongs to the desktop → **no `/dev/dri` for Jellyfin**
    (H.264 IPTV direct-plays; DVR recording is a remux, no transcode). If HW
    transcoding is ever needed, bind-mount `/dev/dri/renderD128` into CT 105
    (LXC shares the render node; the desktop keeps working).
  - NetworkManager exists but only holds old Wi-Fi profiles; **wired
    networking is Proxmox ifupdown2** (`/etc/network/interfaces` + `ifreload -a`).
  - Host users: `root` (PVE), `nate` (KDE login).
- **Storage:**
  - `nvme0n1` 2 TB → LVM VG `pve`: root 96 G, swap 8 G, `local-lvm` thin
    pool ~1.7 TB (raw guest disks; watch `lvs -a` data% as the DVR fills).
  - `sda` 2 TB SATA SSD → dir storage `SSD` at `/mnt/pve/SSD` (qcow2 disks
    for VMs 102/104, ISO library, **vzdump backups**).
  - No off-host backups — everything lives in one chassis (known gap).
- **Host quirks:** the enterprise apt repo is enabled but unusable (401 on
  `apt update`) — disable with
  `mv /etc/apt/sources.list.d/pve-enterprise.sources{,.disabled}`.
  A DPkg hook (`/etc/apt/apt.conf.d/no-nag-script`) suppresses the
  subscription nag in the web UI.

## 3. Network

```
Internet
   │
Flint 2 (GL-MT6000, fw 4.9.0) ─ 192.168.9.1 ─ DHCP .100–.249, DNS, Wi-Fi "Big-GL"
   │        ├─ OpenVPN "Primary Tunnel" (Surfshark US)  — other devices as assigned
   │        └─ OpenVPN "VM103-Swiss"    (Surfshark CH, TCP 1443, kill switch ON)
   │                                     └─ bound to MAC BC:24:11:59:1F:60 ONLY
   ├── GL-BE3600 + UAP-AC-Lite (access points, same L2)
   └── pve-01  enp2s0 → vmbr0 → 192.168.9.11 (host, static)
          ├── CT 105 media-core → 192.168.9.50 (static DHCP lease by MAC)
          ├── VM 102 WIN11, VM 104 SRV-STD-2022 (DHCP pool)
          └── vmbr1–vmbr3 (unplugged spare NICs), vmbr4 (internal-only)
```

- **Split tunnel (verified 2026-07-05):** CT 105 egresses via Zurich
  (`146.70.134.252`, Surfshark CH — exit IP rotates within Surfshark's CH
  pool); the host and everything else do **not**. No DNS leak (CT DNS
  resolves through the tunnel).
- **Kill switch:** if the Swiss tunnel drops, CT 105 loses WAN entirely but
  keeps LAN (Jellyfin still serves recorded/local content). *No internet in
  the CT ⇒ check the tunnel on the router first, not the CT.*
- **The MAC is the linchpin:** `BC:24:11:59:1F:60` (inherited from the
  destroyed VM 103) carries both the `.50` lease and the VPN binding.
  **Never assign it to another guest.**
- The Flint 2 also runs an IoT subnet (`192.168.10.1/24`) and a guest
  network; the server belongs on the main LAN only.
- The old AXT1800 router (`192.168.8.1` LAN, migrated away 2026-07-05) is
  retired; optionally usable as another AP.

## 4. Guests

| ID | Name | Type | Resources | Notes |
|---|---|---|---|---|
| **105** | **media-core** | LXC, Debian 13, unprivileged | 2 vCPU / 8 GB / 512 M swap | The whole point of the box. `onboot=1`, `nesting=1,keyctl=1` (Docker inside). Rootfs 32 G + `mp0` 1 TB at `/srv/media-core` with **`backup=0`**, both on `local-lvm`. No SSH (use `pct exec`). |
| 102 | WIN11 | VM, Win 11 (UEFI+vTPM) | 4 vCPU / 8 GB | Desktop VM, qcow2 on `SSD`. Has a reclaimable `unused0` disk. |
| 104 | SRV-STD-2022 | VM, Server 2022 Eval | 2 vCPU / 8 GB | Lab VM, qcow2 on `SSD`. Install ISOs still attached. |

VMs 100 (pfSense), 101 (Zorn) and 103 (Docker) were **destroyed by the owner
on 2026-07-05**; the Flint 2 does all routing, and CT 105 replaced VM 103.

## 5. The Media-Core stack

### Architecture

```
IPTV provider (Xtream API, cf.teltv.xyz — get.php M3U download is DISABLED, HTTP 884)
   │  (all egress via Swiss tunnel)
   ▼
xtream-sync.py  (CT 105, systemd timer, daily 04:00)
   ├─→ threadfin/conf/playlist.m3u   498 sports channels (11 categories)
   ├─→ epg/epg.xml                   provider XMLTV filtered to those channels
   └─→ media/movies/**.strm          ~24,500 VOD movie links (EN + Netflix/Amazon/BluRay)
   ▼
Threadfin 1.2.37 (Docker, :34400) — HDHomeRun emulation, ffmpeg buffer,
   │                                 Tuner = 1  ← hard 1-stream brake
   ▼
Jellyfin 10.11.9 (Docker, host network, :8096)
   ├─ Live TV tuner:  HDHomeRun @ http://127.0.0.1:34400
   ├─ Guide:          XMLTV @ /epg/epg.xml
   ├─ Library:        "IPTV Cinema" → /media/movies (.strm)
   └─ DVR:            records to /media/recordings (remux, no transcode)
   ▼
Clients (Chromecast/web): Jellyfin app → Add server → http://192.168.9.50:8096
```

### Layout in CT 105 (`/srv/media-core`, the 1 TB backup-excluded mount)

```
docker-compose.yml       # jellyfin + threadfin (pinned versions)
.env                     # mode 600 — TZ + XTREAM_BASE/USER/PASS  ← SECRETS
sync/xtream-sync.py      # the generator (root:750); sync/config.json = category selection
threadfin/conf/          # threadfin settings + generated playlist.m3u
epg/epg.xml              # filtered guide (mounted read-only into jellyfin at /epg)
jellyfin/{config,cache}  # jellyfin state — note: excluded from vzdump with the rest of mp0
media/{movies,tvshows,recordings}
```

### Key configuration facts (as built)

- Images pinned: `jellyfin/jellyfin:10.11.9`, `fyb3roptik/threadfin:1.2.37`.
  Never `:latest` (Jellyfin's `latest` currently points at 12.0 RCs). The
  original manifest's `freetv/threadfin` and `jacobsnyder/m3u2strm` images
  do not exist; m3u2strm was replaced by `sync/xtream-sync.py` entirely
  because the panel's `get.php` (full M3U download) returns HTTP 884.
- **Threadfin quirks discovered the hard way:**
  - This Docker image reads config from `/home/threadfin/conf`
    (not `~/.threadfin` as its docs imply) — the compose mount matches.
  - The buffer engine is selected **per playlist** in
    `settings.json → files.m3u.<id>.buffer` (`"ffmpeg"` here); the global
    `"buffer"` key alone does nothing (symptom: `Buffer: true []` in the
    log and zero bytes to clients). Without a buffer, Threadfin cannot
    enforce the tuner limit.
  - ffmpeg buffer runs pure stream-copy (`-c copy` remux, no transcode).
- **Threadfin tuner = 1 is the account's hard brake** (provider
  `max_connections: 1`). Never raise it. Recording a game and watching that
  same recording simultaneously still uses one provider stream (Jellyfin
  splits it locally); tuning a *different* channel mid-recording is blocked
  by design.
- Channel budget: keep the playlist under ~500 channels (Threadfin/Plex
  soft limit, memory). Category selection lives in `sync/config.json`
  (regexes; currently `US| SPORT` + NFL/NBA/MLB/NHL/NCAAF/NCAAB/MLS/World
  Cup/UFC/Masters PPV = 498). Only ~45 named sports networks have EPG data;
  per-game PPV slots have none (their names carry the schedule instead).
- Jellyfin: wizard user is `root`. DVR path `/media/recordings`.
  Guide + tuner were added via API; the XMLTV source is the *filtered*
  local file, not the provider's 77 MB original.
- Provider subscription: "World 8K", 3 months from 2026-06-28
  (expires ~2026-10-27), 1 connection, `m3u8/ts/rtmp` output allowed.

### Secrets

Provider credentials (Xtream username/password embedded in every stream
URL) exist only in: `/srv/media-core/.env` (600), the generated
`playlist.m3u`/`.strm` files, and Threadfin logs — all inside CT 105.
**Never** in this repo, commit messages, or pasted logs. Jellyfin and
Threadfin web passwords are user-managed (Threadfin UI auth enabled
2026-07-05; first user gets created on next UI visit — do that soon).

## 6. Operations

- **Daily sync** runs at 04:00 (`media-core-sync.timer`); Threadfin re-reads
  the playlist at 04:30 (`settings.json → update`). Manual:
  `pct exec 105 -- python3 /srv/media-core/sync/xtream-sync.py` then
  restart threadfin or wait for its scheduled update.
- **Changing the channel lineup:** edit `sync/config.json`, run the sync,
  `docker restart threadfin`, then in Jellyfin run the "Refresh Guide"
  scheduled task.
- **Updating containers:** bump the pinned tag in `docker-compose.yml`,
  `docker compose up -d`. Check release notes; never `:latest`.
- **Backups:** vzdump CT 105 covers only the 32 G rootfs (OS + Docker
  engine). The 1 TB `mp0` is `backup=0` **by design** (recordings), which
  also means **Jellyfin/Threadfin config is not in vzdump** — after a
  restore, re-run the sync and restore `/srv/media-core` configs from this
  repo's documentation (or tar the small config dirs manually first).
- **Watch the thin pool** (`lvs -a` on the host) as recordings accumulate;
  1 TB is promised from the ~1.7 TB pool.
- **Troubleshooting order:** no WAN in CT → router VPN dashboard
  (`VM103-Swiss` tunnel; kill switch working as intended). Streams dead but
  WAN fine → provider/panel (test
  `player_api.php?...&action=get_live_categories`). Tuner errors in
  Jellyfin → `docker logs threadfin` (look for `Buffer: true [ffmpeg]` and
  `Tuner: 1/1` = a second stream was correctly refused).
- **Do not** run another guest with CT 105's MAC, raise the tuner count,
  map `/dev/dri` into Jellyfin, or start recordings you expect to keep
  while messing with the Swiss tunnel.

## 7. Loose ends

- [ ] Create the first Threadfin web-UI user (auth is on, account not yet made).
- [ ] Change the Jellyfin `root` password to something memorable (set
      programmatically during 2026-07-05 setup).
- [ ] Jellyfin's initial scan of ~24.5k `.strm` movies takes hours and
      hammers TMDB; let it finish before judging the "IPTV Cinema" library.
- [ ] Chromecast clients: install Jellyfin app → Add server manually →
      `http://192.168.9.50:8096`.
- [ ] Optional: TiviMate on the Chromecast for casual channel-surfing —
      but **close it before scheduled recordings** (1-connection account).
- [ ] Host housekeeping (pre-existing): disable enterprise apt repo, delete
      VM 102's `unused0` disk, consider off-host backups.

## 8. History

| Date | Event |
|---|---|
| ≤2024 | Box built: PVE + KDE desktop, pfSense experiments (Tailscale snapshots), desktop VMs. Old LAN `192.168.8.0/24` behind a GL-AXT1800. |
| 2026-07-04 | Media-Core project adopted (manifest imported). Router side prepared on the Flint 2: static lease `.50` for VM 103's MAC, Swiss OpenVPN tunnel `VM103-Swiss` (kill switch, MAC-bound). VM 103 found unfit (EOL Fedora, no Docker, raw disk). |
| 2026-07-05 | LAN cutover done (`pve-01` → `192.168.9.11`). Owner destroyed VMs 100/101/103. CT 105 built (inherits VM 103's MAC). Stack deployed; brief egress anomaly (whole LAN behind one US exit) fixed on the router; split tunnel verified. Provider activated; `get.php` found disabled → custom Xtream sync written. Threadfin per-playlist buffer quirk found & fixed. End-to-end stream through Jellyfin verified. Threadfin auth enabled, CT sshd disabled. Docs consolidated into this file. |

Historical deep-dives preserved in [`docs/archive/`](docs/archive/):
the original Media-Core manifest (imported verbatim) and the network
cutover runbook (step-by-step with rollback, now fully executed).
