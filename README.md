# pve-01 / Project Media-Core

One document for the whole project: a single-node Proxmox homelab (`pve-01`)
whose main job is **Media-Core** — a self-hosted sports DVR + VOD stack
(Jellyfin + Threadfin + a custom Xtream-API sync) running in an LXC behind a
per-device Swiss VPN tunnel.

> ⚠️ This repo documents a private home network. Keep it **private** on
> GitHub. It contains internal addressing but **no secrets** — provider
> credentials and passwords live only on the server (see [Secrets](#secrets)).

**Status (2026-08-04): two Live TV stacks running side by side, both on the
same 957-channel lineup, verified identical at the layer the user sees.**
Names carry no provider group tags, every channel has artwork, and the dead
regional sports networks have been removed. Movies/Series libraries live.
See `docs/handoff-20260804.md` for the current state and
[Loose ends](#loose-ends) for what is outstanding.

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
| **Deploy this stack yourself** | `docs/deploy-your-own-stack.md` — a prompt for a fresh Claude Code session |
| CT 112 Jellyfin (NextPVR stack) | <http://192.168.9.219:8096> |
| DVR dashboard (games, recordings, keep-awake) | <http://100.125.154.95:8099> — auth in `/etc/dvr-dashboard.auth` |
| Grafana | <http://pve-01.tail8f3e6.ts.net:3000> — reachable across the tailnet |
| Icon host (channel artwork, both stacks) | <http://192.168.9.11:8100/index.json> |
| Suspend tonight's shutdown | `dvr-shutdown-hold on` — **never** stop the timer, see lessons |
| Verify a lineup change | `scripts/icon-verify-enduser.py`, `scripts/icon-crosscheck-stacks.py` |
| Health check | `curl -so /dev/null -w '%{http_code}' http://192.168.9.50:8096` → 200; same for `:34400/web/` |
| Sync (playlist/EPG/VOD) | `systemctl status media-core-sync.timer` in CT; manual run: `python3 /srv/media-core/sync/xtream-sync.py` |
| Threadfin auto-recovery | `systemctl status media-core-healthcheck.timer` (every 5 min) + `media-core-guard.timer` (every 1 min, pre-recording) in CT |
| VPN check | `pct exec 105 -- wget -qO- https://am.i.mullvad.net/json` → must say Switzerland |
| Recordings SMB share | `\\192.168.9.50\recordings` — user `tivimate` (password not in this repo) |

## 2. Hardware & host

- Intel Celeron N5105 mini-PC, 4 cores, 32 GB RAM, 4× 2.5GbE Intel NICs.
- Proxmox VE 9.2.4 on Debian 13 "trixie".
- **Unusual:** a full KDE Plasma 6.3 desktop (`task-kde-desktop`, SDDM,
  user `nate`) runs directly on the hypervisor, so the box doubles as a
  workstation. Consequences:
  - The N5105 iGPU drives the desktop **and, since 2026-07-18, Jellyfin
    QSV transcoding**: `renderD128` is passed into CT 105 (`dev0:
    /dev/dri/renderD128,gid=992` in the LXC config) and into the
    jellyfin container (compose `devices` + `group_add: 992`) — the
    render node is shared, the desktop keeps working. Enabled because
    real `libx264` CPU transcodes were found in the logs (720p downscales
    of DVR recordings) crushing the weak 4-core Celeron. Jellyfin accel:
    QSV, hw decode h264/hevc(10-bit)/vp9/mpeg2 (Jasper Lake has no AV1),
    hw encode on, HEVC encode + tonemapping off.
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
   │        └─ WireGuard `wgclient1`    (Surfshark CH Zurich `peer_1501`, kill switch ON)
   │                                     └─ bound to MAC BC:24:11:59:1F:60 (CT 105)
   │                                        + 1c:53:f9:26:34:e9 (Chromecast, .203)
   ├── GL-BE3600 + UAP-AC-Lite (access points, same L2)
   └── pve-01  enp2s0 → vmbr0 → 192.168.9.11 (host, static)
          ├── CT 105 media-core → 192.168.9.50 (static DHCP lease by MAC)
          ├── VM 102 WIN11, VM 104 SRV-STD-2022 (DHCP pool)
          └── vmbr1–vmbr3 (unplugged spare NICs), vmbr4 (internal-only)
```

- **Swiss tunnel is WireGuard as of 2026-07-14** (was OpenVPN-over-TCP,
  which capped at ~10 Mbit/s). Measured through-tunnel throughput
  **102 Mbit/s at 22% router CPU** — the MT7986 was never the bottleneck,
  the TCP-in-TCP OpenVPN transport was. The old Swiss OpenVPN profile is
  kept disabled as one-click rollback; the US OpenVPN tunnel is untouched.
- **⚠️ Firmware gotcha — wrong-peer binding:** when the Surfshark WG
  profile was loaded, the GL.iNet firmware bound `wgclient1` to
  `peer_7124`, a **Chicago (US)** endpoint — IPTV egressed via the US
  until it was re-bound (`uci set network.wgclient1.config='peer_1501'`,
  commit, `ifdown`/`ifup wgclient1`). **After any VPN change on the
  router, always verify egress country** (the Quick-reference VPN check
  must say Switzerland). Config backups from before the change:
  `/root/router-backups/*.bak.20260714` on pve-01 (kept out of this repo —
  they contain credentials).
- **Panel-desync gotcha (found + fixed 2026-07-15):** re-binding the peer
  via `uci` left the VPN-dashboard card for `media-core(ch)` showing
  "Please select a configuration" — the routing engine reads
  `via`/`via_type`/`peer_id`, but the panel UI renders the card from two
  extra bookkeeping fields it writes itself. Fixed by adding
  `group_id='5308'` (Surfshark WG group) and `client_id='1501'` (Zurich
  peer) to the rule; runtime was never affected. **Never click "Please
  select a configuration" on a desynced card** — the panel would rewrite
  the rule and can pick the wrong peer. If a rule is ever edited by hand,
  set `group_id`/`client_id` to match, or expect a blank card.
- **Split tunnel (re-verified 2026-07-14):** CT 105 egresses via Zurich
  (exit IP rotates within Surfshark's CH pool, `89.37.173.x` /
  `138.199.6.x` seen); the host and everything else do **not**. No DNS
  leak (CT DNS resolves through the tunnel).
- **Kill switch (re-verified 2026-07-14 on WireGuard):** with `wgclient1`
  downed, CT 105's WAN egress times out entirely (curl exit 28, zero
  bytes — no fallback to the US tunnel or raw WAN) but keeps LAN
  (Jellyfin still serves recorded/local content). *No internet in
  the CT ⇒ check the tunnel on the router first, not the CT.*
- **The MAC is the linchpin:** `BC:24:11:59:1F:60` (inherited from the
  destroyed VM 103) carries both the `.50` lease and the VPN binding.
  **Never assign it to another guest.** The same router policy rule
  (`media-core(ch)`) also binds the Chromecast (`1c:53:f9:26:34:e9`,
  192.168.9.203) to the Swiss tunnel with the same kill switch.
- **Router SSH:** pve-01's root key is installed on the Flint 2 —
  `ssh root@192.168.9.1` works non-interactively (set up 2026-07-14 for
  the WireGuard migration; used for all `uci` work). Also reachable over
  Tailscale as `big-gl` / **`100.82.52.36`** from any tailnet device
  (pve-01 is on the tailnet as `pve-01` / `100.125.154.95`, so the same
  key works over that path) — useful as a second route if LAN-side
  access breaks, and for off-site admin. It does **not** survive a
  factory reset: the node identity in `/etc/tailscale/tailscaled.state`
  is wiped, so a rebuild still starts from the LAN-side bootstrap in the
  runbook. That state file is now included in router backups, so a
  *restore* rejoins the tailnet without re-authenticating.
- **Router syslog forwarding (2026-07-19):** `log_ip`/`log_port`/
  `log_proto` in `uci show system` point at CT 107 (`192.168.9.164:514`
  udp) — the router's own local log buffer is tiny (~64 KB), this gives
  it durable history. See §7 Observability for the full setup and how
  to revert.
- **Router backups (2026-07-25):** `/root/bin/router-backup.sh` on the
  host, weekly via `router-backup.timer` (Sun 03:30 + jitter), 10
  snapshots kept under `/root/router-backups/<UTC-timestamp>/`. Each is
  a native `sysupgrade -b` archive plus the opkg delta vs the factory
  ROM (which the archive does *not* carry) and a human-readable
  summary. Verified to include **WireGuard private keys**, so a restore
  needs no VPN-provider re-login. Snapshots hold secrets — 600/700,
  stored outside this repo, never committed.
  **Rebuilding from scratch:
  [`docs/router-rebuild-runbook.md`](docs/router-rebuild-runbook.md)** —
  restore steps including how to regain initial access (a factory reset
  puts the router back on `192.168.8.1`, where pve-01 at
  `192.168.9.11` cannot reach it), verification that deliberately does
  not trust the router's own VPN status indicators, and the config
  gotchas that cost hours on 2026-07-25.
- The Flint 2 also runs an IoT subnet (`192.168.10.1/24`) and a guest
  network; the server belongs on the main LAN only.
- The old AXT1800 router (`192.168.8.1` LAN, migrated away 2026-07-05) is
  retired; optionally usable as another AP.

## 4. Guests

| ID | Name | Type | Resources | Notes |
|---|---|---|---|---|
| **105** | **media-core** | LXC, Debian 13, unprivileged | 2 vCPU / 8 GB / 512 M swap | The whole point of the box. `onboot=1`, `nesting=1,keyctl=1` (Docker inside). Rootfs 32 G + `mp0` 1 TB at `/srv/media-core` with **`backup=0`**, both on `local-lvm`. No SSH (use `pct exec`). |
| **107** | **log-server** | LXC, Debian 13, unprivileged | 2 vCPU / 4 GB / 512 M swap | Loki + Grafana + Alloy (2026-07-19) — see §9. `onboot=1`, `nesting=1,keyctl=1`. Rootfs 32 G on `local-lvm`, DHCP networking (no special MAC binding needed, unlike 105). No SSH (use `pct exec`). |
| **108** | **scraper** | LXC, Debian 13, unprivileged | 1 vCPU / 1 GB / 512 M swap | Built 2026-07-21 — home for bespoke EPG scrapers that can't run from CT 105 (their target sites' anti-bot/WAF systems flag the Swiss VPN IP). MAC `BC:24:11:28:55:77` policy-routed on the router through a **third**, dedicated tunnel (Surfshark OpenVPN, `us-lax` UDP — distinct from both CT 105's Swiss WireGuard and the host's own `us-ltm` OpenVPN). Runs `/srv/scrapers/run_scrapers.sh` on a sparse/jittered systemd timer (`scraper-run.timer`, ~4h ± 30 min, staggered per-channel) and serves output via a local HTTP server (`scraper-http.service`, port 8090) that CT 105's `xtream-sync.py` reads as an ordinary `external_epg` source. `onboot=1`, DHCP networking. No SSH (use `pct exec`). |
| 102 | WIN11 | VM, Win 11 (UEFI+vTPM) | 4 vCPU / 8 GB | Desktop VM, qcow2 on `SSD`. Has a reclaimable `unused0` disk. |
| 104 | SRV-STD-2022 | VM, Server 2022 Eval | 2 vCPU / 8 GB | Lab VM, qcow2 on `SSD`. Install ISOs still attached. |

VMs 100 (pfSense), 101 (Zorn) and 103 (Docker) were **destroyed by the owner
on 2026-07-05**; the Flint 2 does all routing, and CT 105 replaced VM 103.

## 5. The Media-Core stack

### Architecture

```
IPTV provider (Xtream API, cf.teltv.xyz — get.php M3U download DISABLED, HTTP 884)
   │  all egress via the Swiss tunnel — 156.146.62.42. The provider refuses
   │  anything else, so this is a hard constraint on every change.
   ▼
xtream-sync.py  (CT 105, systemd timer, daily 12:01)
   │
   ├── channel_naming.py ── display-name rules, applied where a provider name
   │                        becomes a display name. Drops the group tag
   │                        ("US:", "GO:", "DE:"), Title Cases, corrects
   │                        renamed brands. Gated by "modernise_names".
   │                        Imported by build-rename-map.py too, so the
   │                        preview and the real thing cannot drift.
   │
   ├─→ threadfin/conf/playlist.m3u    957 channels, regional number blocks
   │                                  (tvg-chno). tvg-logo points at the icon
   │                                  host, not the provider.
   ├─→ epg/epg.xml                    <channel> + programmes, backfilled from
   │                                  external XMLTV
   ├─→ media/movies/**.strm           ~24,800 across 11 libraries
   └─→ media/shows/**.strm            ~7,400 shows across 13 libraries
   │
   ├──────────────────────────────┬───────────────────────────────────────────┐
   ▼                              ▼                                           │
PRODUCTION  (CT 105)          CT 112  "jellyfin-npvr"                          │
Threadfin :34400              NextPVR :8866                                    │
  HDHomeRun emulation           imports the same playlist; artwork is FILES    │
  artwork = tvg-logo URL        on disk, name-keyed, populated once at import  │
   ▼                              ▼                                           │
Jellyfin :8096 (CT 105)       Jellyfin :8096 (CT 112)                          │
   └─ DVR → /media/recordings    └─ the only stack that has ever recorded      │
                                                                               │
icon-host.service (host, :8100) ───────────────────────────────────────────────┘
   serves /root/icon-archive/extracted, keyed by display name with : / |
   stripped. The single source of channel artwork for BOTH stacks — which is
   why a rename orphans icons and they must be re-keyed together.

Clients: Jellyfin app → http://192.168.9.50:8096 (production)
                     → http://192.168.9.219:8096 (CT 112 / NextPVR)
```

### Layout in CT 105 (`/srv/media-core`, the 1 TB backup-excluded mount)

```
docker-compose.yml       # jellyfin + threadfin (pinned versions)
.env                     # mode 600 — TZ + XTREAM_BASE/USER/PASS  ← SECRETS
sync/xtream-sync.py      # the generator (root:750); sync/config.json = category selection
sync/run-series.py       # helper: run only the series step (manual backfill)
sync/threadfin_ctl.py    # shared: verified Threadfin restart + recording-in-progress guard
sync/pre-recording-guard.py  # media-core-guard.timer (1 min): watch new recordings, auto-recover if stuck (detect-then-fix since 2026-07-19)
sync/healthcheck.py      # media-core-healthcheck.timer (5 min): auto-recover a wedged Threadfin
sync/cache/series/       # per-show get_series_info cache (keyed by last_modified)
threadfin/conf/          # threadfin settings + generated playlist.m3u
epg/epg.xml              # filtered guide (mounted read-only into jellyfin at /epg)
jellyfin/{config,cache}  # jellyfin state — note: excluded from vzdump with the rest of mp0
media/{movies,shows,recordings}
media/movies-{netflix,amazon,appletv,disney,   # per-service/studio branded VOD libraries
              marvel,paramount,universal,dreamworks,bond,discoveryplus}
media/shows-{netflix,amazon,appletv,disney,    # per-service branded series libraries
             marvel,paramountplus,peacock,showtime,sky,discoveryplus,
             crunchyroll,nickelodeon}
```

### Channel map v8 — the guide numbering protocol (2026-07-10)

Owner spec (v8, replaces v7): guide priority order **Locals → News →
Cable → Sports → 24/7 → German**, compact-hundreds numbering, built from
the owner's hand-picked channel list (review-artifact CSV export,
`refined-live-channels-no-espn.csv` — 1,599 explicit stream ids + the
whole `US| SOCCER PPV` panel group; ESPN dropped by owner choice). v8
(2026-07-10) reorganized the locals into **per-city groups**, network
order ABC → NBC → FOX → CBS → PBS within each city; everything from US
News (200) onward is unchanged from v7. Live and verified in Threadfin
XEPG + Jellyfin (1,856/1,856 in the lineup as of v8; **996/996 as of the
2026-07-18 guide-speed trim** — see Operations → Guide size trim):

**Updated to v9 (2026-07-21 night)** — owner asked to front-load the
channels covering the Packers/Badgers/Bucks/Brewers right after the
Wisconsin locals, dedupe Green Bay's backup feeds, and push the non-
Wisconsin ("overflow") locals back to right after US Cable. Everything
below is the current, live table; v8's original layout is preserved in
git history if ever needed.

| Block | Range in use | # | Content (group label in clients) |
|---|---|---|---|
| 100–104 | 100–103 | 4 | **Madison Locals** — ABC 27, NBC 15 (WMTV), FOX 47 (WMSN), CBS 3 (WISC) |
| 105–114 | 105–109 | 5 | **Green Bay Locals** — ABC WBAY, NBC WGBA, FOX WLUK, CBS WFRV, PBS WPNE (deduped to one feed per network 2026-07-21 — the 3 DirecTV CITY backup/"Alt" feeds moved to Overflow Locals) |
| 115–119 | 115–118 | 4 | **Milwaukee Locals** — ABC 12 (WISN), NBC 4 (WTMJ), FOX 6 (WITI), CBS 58 (WDJT) |
| 120–199 | 120–134 | 15 | **Wisconsin Sports** (added 2026-07-21) — Bally/FanDuel Sports Wisconsin ×2 (Bucks + Brewers regional home), Big Ten Network ×4 (Badgers), NFL Network + RedZone, NBA TV, MLB Network ×2, ESPN/ESPN2/ESPNU/ESPNEWS. See the 2026-07-21 (night) changelog for the full deep-dive on which of these actually carry which team's games — national ABC/CBS/NBC/FOX broadcasts (most Packers/Badgers games) have no separate national channel in this lineup and are only reachable via the locals above. |
| 200–299 | 200–256 | 57 | **US News** — majors first (CNN, MSNBC, FOX News, ABC/CBS News, …), rest A–Z |
| 300–479 | 300–479 | 180 | **US Cable** — A–Z (A&E … USA Network, incl. Big Brother feeds) |
| 480–509 | 480–502 | 23 | **Overflow Locals** (added 2026-07-21) — the non-Wisconsin city local blocks (New York, Chicago, Denver, Los Angeles, 5 each) plus Green Bay's 3 demoted "Alt" backup feeds |
| 510–529 | 510–527 | 18 | **HBO Max** originals channels (shifted from 490 to make room for Overflow Locals) |
| 530–599 | 530–536 | 7 | **BBC & Discovery** — BBC News/World/Parliament, Discovery+ 4K, BBC Earth (shifted from 520) |
| 600–649 | 600–636 | 37 | **Bally Sports** — remaining RSN feeds (Wisconsin moved to the Wisconsin Sports block above) |
| 650–679 | 650–666 | 17 | **NFL** — event slots 01–15 + 4K (Network + RedZone moved to Wisconsin Sports) |
| 680–699 | 680–697 | 18 | **MLB** — event slots 01–18 (Network ×2 moved to Wisconsin Sports) |
| 700–739 | 700–715 | 16 (31 configured, panel-deduped) | **NBA** — event slots 01–15 (NBA TV moved to Wisconsin Sports; panel carries every slot twice) |
| 740–799 | 740–760 | 21 | **NHL** — Alternate, Network, slots 01–18 + 4K |
| 800–839 | 800–836 | 37 | **UEFA** event slots |
| 840–879 | 840–873 | 34 | **UK Football** (Live Football event slots) |
| 880–899 | 880–898 | 19 | **BBC Streams** 1–19 (event streams) |
| 900–999 | 900–933 | 34 | **Bundesliga** — Sky Sport Bundesliga tiers + Mobil feeds |
| 1000–1299 | 1000–1199 | 200 | **Soccer PPV** — whole panel group, slot names stable ("Soccer PPV 042") |
| 1300–1499 | 1300–14xx | 131 | **DirecTV Stream** — "GO:" 24/7 streaming channels (Big Ten ×4 + ESPN family ×4 moved to Wisconsin Sports 2026-07-21) |
| 1500–2099 | 1500–15xx | 47 | **Prime 24/7** — PRIME looping channels, sports-only since 2026-07-18 (see below) |
| 2100–2499 | *(empty)* | 0 | **Cinema TV** — removed entirely 2026-07-18 (see below); block kept as reserved headroom |
| 2500–2549 | 2500–2536 | 37 | **German Public & Regional** — v6 carryover (HR first), regex-selected |
| 2550+ | 2550–2586 | 37 | **German Cable & Entertainment** — v6 carryover, regex-selected |

Headroom inside each block is deliberate — new provider channels join at
the end of their block without renumbering anything else. Reordering
guide *priority* = changing a block's `start_chno` (guide order is
channel-number order).

**How it's enforced (authoritative path).** The numbering lives in
`sync/config.json → live_selections`: an *ordered* list where each entry
has a `group` label plus either an **explicit `ids` list** (v7 owner
picks — listed order is channel order; robust against name churn on
event slots) or a `category` regex with optional `name`/`name_exclude`
regexes (Soccer PPV group, German carryover blocks), and a `start_chno`.
Entries may also carry `slot` (stable display-name label for event-slot
channels — the panel renames "MLB 12 | Brewers x Cardinals start:…" all
day, but Threadfin keys channels by name, so the playlist always says
"MLB 12" and the event lives in the EPG) and `epg_mode: "ppv"` (parse
the current event out of the channel name into guide entries). The sync
walks the list in order, assigns `tvg-chno` sequentially from each
block's `start_chno`, warns if a block overflows into the next one, and
writes the numbers into `playlist.m3u`. Threadfin (XEPG mode) adopts
`tvg-chno` as the HDHomeRun `GuideNumber`, which is what Jellyfin sorts
the guide by. **To change the lineup you edit `config.json` and re-run
the sync — never renumber by hand in the UIs.**

**EPG layers (v7; unchanged in v8).** In order of preference per channel: (1) provider
XMLTV (~178 channels); (2) external free XMLTV — epgshare01
US_LOCALS1/US2/US_SPORTS1/UK1/DE1 plus i.mjh.nz PlutoTV/SamsungTVPlus/
Plex/Roku (FAST-channel mirrors; ~155 channels), matched by call sign /
normalized name / `epg_aliases`; (3) **PPV event parsing** — for
`epg_mode: "ppv"` slots the sync parses event title + UTC start/stop
straight out of the channel name (~340 slots); (4) **synthesized looping
guide** — every remaining channel gets 4-hour repeating entries titled
from the channel name (~1,143 channels, mostly Prime/Cinema/GO 24/7
loops), so the guide never shows empty cells. Layers 3+4 are generated
locally — zero extra panel API load. The **hourly PPV refresh**
(`sync/ppv-refresh.py`, `media-core-ppv.timer`, :07 every hour except
04:xx) re-reads the live stream list (one API call), rewrites guide
entries for the slots recorded in `sync/cache/ppv-xids.json`, and — only
when an event actually changed — updates `epg.xml`, POSTs Threadfin
`update.xmltv` (API enabled in `settings.json`, port 34400), and
triggers Jellyfin's Refresh Guide.

**Threadfin-UI equivalent (manual blueprint).** If a channel ever has to
be bucketed ad hoc without touching the sync (or to rebuild the map on a
stock Threadfin), the same scheme maps onto Threadfin's tools like this —
Filter page: create one *Custom filter* per block with a case-insensitive
regex on the provider playlist, e.g.:

| Block | Threadfin custom-filter regex (against provider names) |
|---|---|
| WI locals | `US\| (NBC\|FOX\|CBS\|ABC\|CW\|NEWS).*(WISN\|WTMJ\|WITI\|WDJT\|WBAY\|WFRV\|WLUK\|WGBA\|WISC\|WKOW\|WMTV\|MILWAUKEE\|GREEN BAY\|MADISON\|WISCONSIN)` |
| DE public/regional | `DE\| GENERAL.*(HR HD\|DAS ERSTE\|ZDF\|3SAT\|ARTE\|PHOENIX\|WDR\|NDR\|MDR\|SWR\|BR FERNSEHEN\|RBB)` |
| US news+cable | `US\| NEWS ` and `US\| ENTERTAINMENT.*(A&E\|AMC\|DISCOVERY\|FX\|TBS\|TNT\|USA NETWORK\|SYFY\|TLC…)` |
| Premium sports | `US\| BALLY SPORTS`, `US\| SPORT.*(ESPN\|BIG TEN\|FOX SPORTS\|SEC\|NFL\|MLB\|DAZN)`, `DE\| DAZN EXCLUSIVE` |
| UK TV/sports | `UK\| GENERAL ᴴᴰ`, `UK\| NEWS ᴴᴰ`, `UK\| SPORT ᴴᴰ ⱽᴵᴾ`, `UK\| TNT SPORT ᴴᴰ ⱽᴵᴾ` |
| DE cable | `DE\| GENERAL.*(RTL\|SAT\.1\|PROSIEBEN\|KABEL 1\|VOX\|WELT\|N-TV\|DMAX)` |
| DE sports | `DE\| SPORT HD`, `DE\| BUNDESLIGA HD` |

then Mapping page: filter by group → select-all → *Bulk edit* → set
*Starting channel number* to the block start (300 for US sports, etc.) —
Threadfin renumbers the selection sequentially. That is exactly what the
sync automates; the full, exact regexes (including the quality/duplicate
`name_exclude` rules that keep e.g. `(SD)`, `(720P)`, `+1`, west-coast
feeds and `###` separators out) are the ones in `config.json`, which is
the single source of truth.

**Jellyfin guide management (family/Chromecast view).**
- The guide is sorted by channel number out of the box (HDHomeRun
  `GuideNumber` = our `tvg-chno`); no per-client setup is needed for the
  ordering itself.
- Per user: Profile → Display → *Home screen* — put **Live TV** (Guide)
  first for family members; Admin → Users can preset this. On first app
  launch on the Chromecast, sign in as the family user, tick *Remember
  me* — the app lands on Home with the guide section on top.
- Guide → sort menu: keep **"By number"** (a client that was switched to
  A–Z will look like "a giant alphabetical mess" — that's a client-side
  toggle, not the server).
- Favorites: long-press a channel → *Add to favorites*; the "Favorite
  Channels" row then leads the Live TV home section on every client.
- After lineup changes, clients may cache the old order until the app is
  fully restarted (or Jellyfin's guide is refreshed — the nightly cascade
  does this at 04:30).

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
  - **Channel numbers (2026-07-05):** Threadfin runs in **XEPG mode**
    (`settings.json → epgSource: "XEPG"`); it auto-adopts the playlist's
    `tvg-chno` as `x-channelID`, which becomes the HDHomeRun
    `GuideNumber` Jellyfin sorts by. Gotcha: new channels arrive
    **inactive** (dropped from the lineup) — `sync/activate-xepg.py`
    (media-core-xepg.timer, 04:25) activates them; it stops/starts
    threadfin because Threadfin rewrites xepg.json on shutdown. The
    Threadfin-side XMLTV mapping stays "-" — Jellyfin gets its guide
    from /epg/epg.xml directly.
- **Threadfin tuner = 1 is the account's hard brake** (provider
  `max_connections: 1`). Never raise it. Recording a game and watching that
  same recording simultaneously still uses one provider stream (Jellyfin
  splits it locally); tuning a *different* channel mid-recording is blocked
  by design.
- Channel budget: keep the playlist under ~500 channels (Threadfin/Plex
  soft limit, memory). Selection lives in `sync/config.json` as an
  **ordered list of `live_selections`** — each has a clean `group` label
  (what clients see), a `category` regex on provider category names, and an
  optional `name` regex on channel names (that's how the Wisconsin locals
  are cherry-picked out of the giant NBC/CBS/ABC/FOX/CW affiliate
  categories by call sign/city). Playlist order = channel-number order, so
  Wisconsin comes first. `live_name_exclude` drops `###` separator
  channels and low-quality duplicate feeds.
- **Guide & artwork mechanics:** the sync writes an XMLTV `<channel>`
  element for *every* channel with `display-name` identical to the tuner
  channel name plus the provider's logo as `<icon>` — Jellyfin auto-maps by
  name and shows channel artwork (~90% coverage). Provider programme data
  exists for only ~145 channels; sync v4 (2026-07-05) backfills the rest
  from **external XMLTV** (free daily dumps at `epgshare01.online`,
  per-selection `epg_region` → `external_epg` source list in
  `config.json`). Matching is by US call sign (`(WITI)` → `WITI-DT`)
  or loosely-normalized name (country prefix, quality tags, `(MOBIL)`
  variants stripped; SPORTS≡SPORT), with manual overrides in
  `config.json → epg_aliases` (e.g. `C-SPAN 1` → `CSPAN`,
  `CW 18 [MILWAUKEE]` → `WVTV`). Some external channels are empty shells
  (a `<channel>` entry, zero programmes), so every match candidate is
  kept and a channel binds to the first candidate that actually carries
  programmes. Genuinely unmatchable: event/PPV channels (TUDN EXTRA,
  Bally PPV — names encode the schedule), defunct-brand channels (CSN),
  and channels no public EPG carries.
  ⚠️ Jellyfin caches the parsed guide at `/cache/xmltv/` — after changing
  the EPG's structure, clear it (`docker exec jellyfin rm -rf /cache/xmltv`)
  and run the Refresh Guide task, or you'll be staring at stale mappings.
- ⚠️ **Jellyfin 10.11 empty-PresentationUniqueKey gotcha:** after a bulk
  library import, user-scoped listings may show only a fraction of the
  movies (admin/API count is fine). Cause: rows are inserted with an
  empty `PresentationUniqueKey` and user queries group by it, collapsing
  all unkeyed items; the key only gets stamped as the (slow) metadata
  scan touches each item. Fix: stop jellyfin, then in `jellyfin.db`:
  `UPDATE BaseItems SET PresentationUniqueKey = lower(replace(Id,'-',''))
  WHERE Path LIKE '/media/movies/%.strm' AND (PresentationUniqueKey IS
  NULL OR PresentationUniqueKey='')` — the key is just the dash-less
  lowercase item Id for movies. (Hit 2026-07-05: users saw ~1k of 20.7k.)
- Movie artwork comes from TMDB, keyed off the normalized
  `Title (Year)` folder/file names the sync generates, plus the **Fanart**
  plugin (installed 2026-07-05; extra backdrops/logos from fanart.tv) and
  the bundled OMDb/Studio Images providers. The full-library metadata
  fetch takes hours after big renames; stale old-name entries are purged
  by the scan's Clean Database post-task.
- **VOD/series category selection is EXCLUDE-based since 2026-07-08**
  (owner-supplied lists): `config.json → vod_exclude_categories` /
  `series_exclude_categories` name the panel categories to drop;
  **every other category — including ones the provider adds later — is
  included automatically**. The old include keys stay in the config but
  now only set dedupe priority: `vod_category_prefixes` (`EN - `) and
  the `series_categories` order still decide which copy of a duplicated
  title wins. Selection went from ~30 → 66 VOD and 24 → 52 series
  categories (movies ~20.7k → ~27.7k).
- VOD dedupe priority (title+year key, case-insensitive):
  **HD copies beat 4K/Dolby prints** (`HIGH_BITRATE` regex in the sync —
  the ~10 Mbit/s VPN can't sustain high-bitrate remuxes), then
  **English-first** (`EN - *` categories before the rest), so when a
  title exists in several categories the playable English copy wins;
  titles without an English twin are kept.
- **TV series (2026-07-06):** `config.json → series_categories` (ordered,
  English first — same first-category-wins dedupe as VOD; since
  2026-07-08 it is only the priority head — all non-excluded panel
  categories are appended after it, HD variants before 4K/Dolby) drives
  `build_series()`: per show a `Title (Year)/` folder with `tvshow.nfo`,
  `Season NN/Title SxxEyy.strm` + a minimal episode `.nfo` (episode title
  from the provider). `get_series_info` responses are cached in
  `sync/cache/series/<id>.json` keyed by the provider's `last_modified`,
  so the nightly run only re-fetches changed shows (first full backfill
  takes ~1–2 h over the VPN; nightly deltas are minutes). Panel quirk:
  `episodes` usually comes back as a dict keyed by season but is a
  **list of season-lists for some titles** (season 0 = specials — real,
  don't index-shift it); the sync handles both. Episodes that vanish
  upstream are cleaned per show; whole-library deletes go through the
  same <70% prune guard as VOD. Genre/artwork organization comes from
  TMDB just like movies: the "Series" and "Movies" libraries
  both expose a **Genres** view in Jellyfin once metadata converges —
  that (plus filters/sort in each library) is the "organized by genre"
  browsing surface; no folder-level genre split is needed or wanted
  (TMDB genres change; folders would go stale).
- Jellyfin: wizard user is `root`. DVR path `/media/recordings`.
  Guide + tuner were added via API; the XMLTV source is the *filtered*
  local file, not the provider's 77 MB original. **Metadata savers are
  disabled** on both media libraries (2026-07-06): the media mounts are
  read-only, so the default NFO saver spammed `Read-only file system`
  errors on every metadata fetch — savers off, NFO *readers* still on
  (the sync writes the NFOs).
- Provider subscription: "World 8K", 3 months from 2026-06-28
  (expires ~2026-10-27), 1 connection, `m3u8/ts/rtmp` output allowed.

### SMB recordings share (2026-07-07)

Samba (`smbd`, enabled + active) runs **inside CT 105** and exports one
share so the owner's TiviMate boxes can record straight onto the server:

- Share: `\\192.168.9.50\recordings` → `/srv/media-core/media/recordings`
  (read/write, `create mask 0644`), i.e. on the 1 TB `mp0` mount that is
  `backup=0` by design — recordings are **not** in the vzdump backups.
- Auth: CT-local user `tivimate` (uid 1000) is the only `valid users`
  entry; its Samba password was set by the owner and is **not in this
  repo**. Config hardening: `server min protocol = SMB2`,
  `disable netbios = Yes` (port 445 only), printers off, standalone
  server role.
- The same directory is bind-mounted **rw** into the Jellyfin container
  at `/media/recordings` (also Jellyfin's own DVR path), so SMB-written
  recordings appear in Jellyfin alongside DVR output. TiviMate writes
  into its own subfolders (`Tivimate/`, `Sports/`).
- Verified working from the client on 2026-07-07.
- Config lives at `/etc/samba/smb.conf` in the CT; check with
  `pct exec 105 -- testparm -s`.

### Secrets

Provider credentials (Xtream username/password embedded in every stream
URL) exist only in: `/srv/media-core/.env` (600), the generated
`playlist.m3u`/`.strm` files, and Threadfin logs — all inside CT 105.
The Jellyfin automation API key lives in
`/srv/media-core/.jellyfin_api_key` (600) — same rules.
**Never** in this repo, commit messages, or pasted logs. Jellyfin and
Threadfin web passwords are user-managed (Threadfin UI auth enabled
2026-07-05; first user gets created on next UI visit — do that soon).

## 6. Operations

- **Nightly cascade (all times CET, deliberately ordered):** 04:00 sync
  (`media-core-sync.timer`: playlist + EPG incl. external backfill + VOD;
  on success it also API-triggers Jellyfin's Refresh Guide + library scan
  if they're idle) → 04:15 Threadfin re-reads the playlist
  (`settings.json → update`) → 04:25 XEPG activation of new channels
  (`media-core-xepg.timer`) → 04:30 Jellyfin Refresh Guide (daily
  trigger) → 04:45 Jellyfin library scan (daily trigger).
- **Hourly PPV guide refresh:** `media-core-ppv.timer` runs
  `sync/ppv-refresh.py` at :07 every hour *except* 04:xx (the cascade
  owns that window). One `get_live_streams` call; rewrites event-slot
  guide entries only when an event changed, then Threadfin
  `update.xmltv` + Jellyfin Refresh Guide. No-op runs log
  `no event changes, guide untouched`.
- **Streaming bandwidth ceiling — lifted 2026-07-14:** the Swiss tunnel
  now runs WireGuard at a measured **102 Mbit/s** (22% router CPU, held
  steady even with a TiviMate stream running). The old OpenVPN-over-TCP
  transport capped at ~10–20 Mbit/s and made Blu-ray/4K remuxes
  (15–80 Mbit) buffer; those should now direct-play. Follow-ups still
  pending (see Loose ends): raise `LibraryScanFanoutConcurrency` 2 → 4
  and remove any client bitrate caps set during the OpenVPN era. The
  next ceiling, if any, is the router's WiFi-client uplink — untested.
  Manual sync:
  `pct exec 105 -- python3 /srv/media-core/sync/xtream-sync.py` then
  restart threadfin or wait for its scheduled update.
- **Sync reliability guards (v4, extended 2026-07-06):** provider API
  calls retry 3× with backoff, **including on HTTP-200-with-empty-list
  responses** (the panel intermittently answers `[]` for category/stream
  listings — observed live on `get_series_categories` 2026-07-06); the
  VOD *and* series prune steps refuse to delete anything if the provider
  returns <70% of what's already on disk; the **live playlist** has the
  same guard — if the provider returns <70% of the channels currently in
  `playlist.m3u`, the sync aborts and keeps yesterday's playlist rather
  than handing Threadfin an empty lineup. A guarded run logs `SKIPPING
  prune` / `keeping the existing playlist` — check
  `journalctl -u media-core-sync.service`.
- **Jellyfin API access for automation:** key in
  `/srv/media-core/.jellyfin_api_key` (600, inside CT only — created
  directly in the `ApiKeys` table). The sync uses it for the post-run
  refresh triggers.
- **Changing the channel lineup:** edit `sync/config.json`, run the sync,
  then `curl -X POST -d '{"cmd":"update.m3u"}' http://127.0.0.1:34400/api/`
  (Threadfin's API is enabled as of v7; `docker restart threadfin` works
  too). If numbers/blocks changed, run `sync/renumber-xepg.py` (existing
  XEPG entries keep their old numbers otherwise), then
  `sync/activate-xepg.py` (new channels arrive inactive), then
  `{"cmd":"update.xmltv"}` and Jellyfin's "Refresh Guide" task (the
  sync's post-run trigger fires too early for lineup changes — Threadfin
  hasn't reloaded yet — so re-trigger it).
- **Updating containers:** bump the pinned tag in `docker-compose.yml`,
  `docker compose up -d`. Check release notes; never `:latest`.
- **Backups:** vzdump CT 105 covers only the 32 G rootfs (OS + Docker
  engine). The 1 TB `mp0` is `backup=0` **by design** (recordings), which
  also means **Jellyfin/Threadfin config is not in vzdump**. That gap is
  now closed by `media-core-config-backup.timer` **on the host** (03:30
  nightly, before the 04:00 sync): `/root/bin/media-core-config-backup.sh`
  streams a tar out of the CT to `/mnt/pve/SSD/media-core-backups/`
  (separate physical disk from the CT's NVMe thin pool; newest 7 kept,
  ~1.5 GB each). Captured: `jellyfin/config` **minus the ~105 GB
  regenerable `metadata/` artwork cache**, a consistent
  `sqlite3 .backup` snapshot of `jellyfin.db` (safe while Jellyfin
  runs), `threadfin/conf` (minus its own backup zips), `sync/` (minus
  the regenerable series cache), `docker-compose.yml`, and `.env`
  (secrets — archives are mode 0600). Restore: untar over
  `/srv/media-core`, rename `jellyfin.db.snapshot` back to
  `data/jellyfin.db`, `docker compose up -d`, let the next scan rebuild
  artwork.
- **Watch the thin pool** (`lvs -a` on the host) as recordings accumulate;
  1 TB is promised from the ~1.7 TB pool.
- **Troubleshooting order:** no WAN in CT → router VPN dashboard
  (`wgclient1` WireGuard tunnel; kill switch working as intended — and
  check the egress **country**, not just that the tunnel is up: the
  firmware once bound the wrong peer, see Network). Streams dead but
  WAN fine → provider/panel (test
  `player_api.php?...&action=get_live_categories`). Tuner errors in
  Jellyfin → `docker logs threadfin` (look for `Buffer: true [ffmpeg]` and
  `Tuner: 1/1` = a second stream was correctly refused).
- **Guide empty / Threadfin unreachable on 34400 (ephemeral-port bug) —
  fixed properly 2026-07-16:** a rapid `docker stop` → `docker start` of
  Threadfin can make it come up on a random ephemeral port instead of
  34400 (`curl http://192.168.9.50:34400/web/` refuses; `docker logs
  threadfin` shows `Web Interface: http://172.18.0.2:/web/` with an
  **empty port**). A fixed `sleep 5` before `docker start` (added
  2026-07-13) was believed to have fixed this but only reduced the odds
  — it recurred 07-14 and again 07-16 (this time undetected for **17+
  hours**: every hourly PPV run from 04:25 to 21:08 logged `threadfin
  update.xmltv failed: Connection reset by peer` and nobody was
  watching). Replaced with `sync/threadfin_ctl.py`
  (`start_threadfin_verified()`): polls `/web/` for up to 20 s after each
  start attempt and retries the stop/start cycle (bounded, 3 attempts)
  instead of assuming a fixed delay was enough; `activate-xepg.py` and
  `renumber-xepg.py` both use it now. A `media-core-healthcheck.timer`
  (every 5 min) also auto-recovers Threadfin if it's ever found
  unreachable, so an ephemeral-port relapse gets caught in minutes
  instead of silently sitting broken — see "DVR recording reliability"
  below.
- **DVR recording reliability (diagnosed + fixed 2026-07-16):** two
  scheduled recordings ("Live: FIFA World Cup 2026", 07-14 and 07-15)
  both landed as ~20 KB stub files. Root cause, confirmed in
  `docker logs threadfin`: Threadfin's single-connection tuner slot was
  left marked busy by an earlier stream that ended abnormally (a
  "zombie" session — no matching "connection has ended" cleanup log),
  and refused the DVR's connection at record-start time (`No new
  connections available. Tuner = 1`). Separately, the nightly cascade
  restarting Threadfin at 04:15/04:25 unconditionally is a second,
  related risk for any recording spanning that window (not yet observed
  to cause a failure, but the fix below removes the risk anyway). Fix:
  - `sync/threadfin_ctl.py` adds `stop_threadfin_safe()` /
    `recording_in_progress()` — anything that wants to restart Threadfin
    now checks Jellyfin's `/emby/LiveTv/Recordings?IsInProgress=true`
    first and **skips the restart** if a recording is active (fails
    safe: an API error is treated as "yes, a recording is running").
    `activate-xepg.py` and `renumber-xepg.py` both go through this now,
    so the nightly cascade can never kill an in-progress recording.
  - `sync/pre-recording-guard.py` (new) + `media-core-guard.timer`
    (every 1 min): originally, for any Jellyfin recording timer starting
    within the next 4 minutes, force a clean Threadfin restart first,
    guaranteeing the tuner is free (clears a zombie session, or frees
    the tuner if a live Jellyfin Live TV viewer is holding it). **This
    preemptive design was replaced 2026-07-19** — see Operations →
    "Recording-start watchdog redesign" — because it restarted Threadfin
    (and so dropped any live viewer) before *every* recording whether or
    not anything was actually wrong.
  - **Not covered:** TiviMate connects straight to the IPTV provider,
    bypassing Threadfin entirely, so none of the above can free the
    tuner if TiviMate itself is what's holding the provider's 1-connection
    cap. Neither confirmed failure was actually caused by TiviMate, but
    it's a real additional way to hit the same wall. **Future plan, not
    yet built** (owner-approved in principle): a router-level firewall
    rule that temporarily blocks TiviMate's device
    (`192.168.9.203`, MAC `1c:53:f9:26:34:e9` — same VPN-policy binding
    as the Chromecast) from reaching the provider during the
    pre-recording guard window, using pve-01's existing root SSH key to
    the Flint 2 (see Network). Would need to (a) confirm this MAC is
    reliably TiviMate and not shared with casting use, (b) add/remove
    the block from `pre-recording-guard.py` alongside the Threadfin
    restart, (c) decide how long before/after the recording to hold it.
- **DVR defaults (2026-07-18):** global recording padding is **2 min
  pre / 60 min post** (`livetv` config `PrePaddingSeconds=120` /
  `PostPaddingSeconds=3600`) — sports run long; stub-free endings beat
  disk space. Set per owner spec; individual timers can still override.
- **QSV hardware transcoding (2026-07-18):** see Hardware & host for the
  passthrough chain. **Two-stage enablement:** hw *decode* works
  immediately; hw *encode* on Jasper Lake is low-power (VDEnc) only,
  which needs HuC firmware — `options i915 enable_guc=2` is staged in
  `/etc/modprobe.d/i915-guc.conf` (initramfs rebuilt) and takes effect
  at the **next host reboot**, after which enable Jellyfin's
  `EnableIntelLowPowerH264HwEncoder` (+Hevc) encoding options and verify
  a transcode log shows `h264_qsv`. ⚠️ Do **not** enable the low-power
  encoder options while `huc_info` still says disabled — sessions would
  fail outright instead of falling back to libx264. Check:
  `cat /sys/kernel/debug/dri/0000:00:02.0/gt0/uc/huc_info`.
- **Guide size trim (2026-07-18):** owner reported slow guide loading on
  the Android TV app. Root numbers: 1,856 channels, a 20.7 MB `epg.xml`
  (29,299 programmes), and the server's own nightly "Refresh Guide" task
  took ~20 min. Jellyfin has no per-user channel-hiding feature (checked —
  parental controls are rating-based only), so the only real lever is
  the lineup itself. Over half the lineup (1,045 of 1,856 channels) was
  the three "24/7 looping" blocks (Prime 24/7, Cinema TV, DirecTV
  Stream) — synthesized/looping filler, not appointment viewing, but
  **two of the three are not sports-free**: DirecTV Stream and Prime
  24/7 both carry real linear sports channels (ESPN family, NFL/NHL/NBA/
  MLB Network, regional sports nets, UEFA Champions League, Real Madrid
  TV, NESN, etc.) mixed in with general entertainment. Cut, after
  channel-by-channel review (not just keyword matching — an early pass
  nearly cut UEFA Champions League and Real Madrid TV on a keyword miss):
  - **Cinema TV** (308 channels): removed entirely — verified sports-free.
  - **Prime 24/7** (580 → 47): kept every sports channel, cut the rest.
  - **DirecTV Stream** (157 → 139): cut only 18 channels that were exact
    duplicates already carried elsewhere in the lineup (incl. NFL Network
    and NHL Network, which are already at channels 650/741 in the
    dedicated NFL/NHL blocks) — kept everything else, including
    Disney/Nickelodeon/Cartoon Network/STARZ Encore/TUDN, which are
    **not** duplicated anywhere else in the lineup and would have been
    lost if the whole block had been cut.
  - Net: **1,856 → 996 channels (46% smaller)**, `epg.xml` 20.7 MB →
    13.1 MB, no verified loss of sports content.
  - Rollback: `sync/config.json.pre-20260718-guide-trim` on CT 105 has
    the pre-trim selection; restore it, re-run the sync, then
    `renumber-xepg.py` → `activate-xepg.py` → `update.xmltv` → Jellyfin
    Refresh Guide (same procedure as any lineup change, see "Changing
    the channel lineup" above).
  - Gotcha hit during this change: `xtream-sync.py`'s live-playlist
    prune guard (`PRUNE_GUARD = 0.70`) correctly refused to write a
    46%-smaller playlist, assuming provider breakage. For this one
    *intentional* reduction it was overridden in-memory for a single run
    (`PRUNE_GUARD` patched on an imported copy of the module, never
    written to the file on disk) rather than edited in place — the
    permanent guard is untouched and still protects future nightly runs
    against a real truncated provider response.
- **Per-service VOD/series libraries (2026-07-18):** the sync now splits
  service-branded provider categories out of the general Movies/Series
  pool into their own Jellyfin libraries: **Netflix / Amazon / Apple TV+
  / Disney+ × Movies / Series** (8 new libraries; dirs
  `media/movies-<svc>` and `media/shows-<svc>`, mounted read-only into
  the Jellyfin container — the compose file lists each mount explicitly,
  so **a new service needs a new mount line + container recreate**).
  Mechanics, all in `xtream-sync.py`:
  - `SERVICE_PATTERNS` (category-name prefix regexes: `^NETFLIX`,
    `^AMAZON`, `^APPLE\+`, `^DISNEY\+`) decide the bucket; anything
    unmatched stays in the general pool. To add a service: extend
    `SERVICE_PATTERNS`/`SERVICE_SLUG`, add the compose mount, create the
    Jellyfin library (clone LibraryOptions from an existing one via
    `/Library/VirtualFolders` — metadata savers OFF, NFO readers on).
  - **Dedupe is per-library** (owner's choice): a title on both Netflix
    and Amazon appears in both libraries; within each library the same
    HD-before-4K/Dolby + EN-first priority picks the best print. The
    same title may also still exist in the general pool if a general
    category carries it — that's intended ("appear everywhere it's
    actually available").
  - `SERIES_CACHE` is shared across buckets (keyed by `series_id`), so a
    show in two libraries is still fetched from the panel only once.
  - The prune guards run per-library.
  - First split (2026-07-18): 24,521 movies (general 19,024 / Netflix
    4,289 / Amazon 629 / Disney+ 462 / Apple TV+ 117) and 7,307 shows
    (general 4,216 / Netflix 1,960 / Disney+ 385 / Apple TV+ 384 /
    Amazon 362). The one-time redistribution pruned 3,171 titles out of
    the general movies pool (they moved to their service library), which
    required a one-run in-memory `PRUNE_GUARD` override — same
    technique, and same reasoning, as the guide trim above.
  - **Second wave (2026-07-18, same day):** 12 more brands split out per
    owner (14 libraries — Marvel and Discovery+ get both kinds):
    Marvel M+S, Paramount+ S, Paramount Pictures M ("Paramount Movies"),
    Peacock S, Showtime S, Sky S, Discovery+ M+S, Crunchyroll S,
    Nickelodeon S, Universal M, DreamWorks M, James Bond 007 M. Now
    24,694 movies / 7,328 shows across 25 Jellyfin libraries. Every
    library (both waves) has a custom flat brand-inspired tile icon,
    generated by a script kept off-server (SVG → PNG → uploaded via
    `/Items/{id}/Images/Primary`); regenerate/extend by re-running the
    generator. ⚠️ **Provider quirk:** `get_vod_streams` gives each
    stream exactly one `category_id`, so a "collection"-style panel
    category only yields the titles whose *primary* category it is —
    "JAMES BOND 007" produced just **1 movie** (the other Bond films'
    primary category is a general EN one, so they remain in the general
    Movies library). Studio libraries only ever see what the panel
    primarily files under them.
  - **HBO: not possible as a VOD library** — the panel has no English
    HBO/Max VOD or series category (only `ESPAÑA HBO MAX`, Spanish,
    excluded). HBO content here is live-only (channels 490–507 + live
    HBO feeds).
  - ⚠️ After the first scan of the new libraries, check the
    empty-`PresentationUniqueKey` gotcha (above) against the new
    `/media/movies-%` and `/media/shows-%` paths — same bulk-.strm-import
    mechanics as when the main libraries were built.
- **TiviMate recording guard (2026-07-18):**
  `threadfin_ctl.recording_in_progress()` now also returns True when a
  `.ts` file under TiviMate's write folders (`media/recordings/Tivimate/`,
  `media/recordings/Sports/`) was modified in the last 60 s — TiviMate
  records straight to the SMB share, so an actively-growing file is the
  only signal it exists (it bypasses Threadfin/Jellyfin entirely, no API
  to ask). Result: no Threadfin restart from any script interrupts an
  in-progress TiviMate recording. Still not covered: guaranteeing the
  provider connection is *free* when a TiviMate recording starts (its
  schedule is unknowable from the server) — that's the router-block plan
  in Loose ends.
- **Threadfin ffmpeg auto-reconnect (2026-07-19):** a World Cup recording
  died at exactly 95 minutes in — `docker logs threadfin` showed
  `[ERROR] FFMPEG error (Streaming was stopped by third party
  transcoder (FFmpeg / VLC)) - EC: 1204` with no further detail
  (`ffmpeg.options` runs at `-loglevel error`). Investigated and ruled
  out everything local before concluding it was the provider itself:
  Threadfin container never restarted (RestartCount 0), no OOM, no
  automation touched it (`media-core-guard`/`healthcheck` both correctly
  no-opped through the window), and no client was transcoding (the
  Chromecast was direct-playing — QSV wasn't even in the path). The real
  bug: **`ffmpeg.options` had no HTTP reconnect flags**, so any upstream
  drop — a provider-side stream rotation, a momentary VPN blip — killed
  the pull permanently instead of the process riding through it. Fixed:
  added `-reconnect 1 -reconnect_streamed 1 -reconnect_on_network_error 1
  -reconnect_delay_max 5` before `-i [URL]` in `settings.json`. Verified
  post-fix with a direct pull from Threadfin's own `/stream/<id>` URL
  (get the real per-channel URL from `/lineup.json` — a guessed URL
  shape gets a misleading `EC: 1203 "URL not found in any playlist"`,
  not a real test). Recovery procedure used on the night (documented in
  case it recurs before every recording naturally benefits from the
  fix): hard-link the partial `.ts` to a safe path first (survives even
  if the timer's file later gets touched), `DELETE` the zombie timer via
  `/emby/LiveTv/Timers/{id}`, then `POST` a fresh timer from
  `/emby/LiveTv/Timers/Defaults?programId=<id>` with extra
  `PostPaddingSeconds` — this produces a `" - 1.ts"` continuation file,
  not a single seamless recording, so a dropped-and-recovered game is
  two files to watch back to back.
- **Recording-start watchdog redesign (2026-07-19):** owner reported the
  Android app pausing/dropping for a few seconds right around every
  recording's start (and separately, around every recording's stop).
  Confirmed two distinct causes:
  - **Start:** `media-core-guard.service` logs lined up exactly —
    `pre-recording-guard.py`'s original design (2026-07-16) restarted
    Threadfin ~4 min before *every* recording, unconditionally, to
    guarantee a clean tuner. That restart drops any live stream
    currently flowing through Threadfin, including a live viewer's.
    **Fixed** by rewriting the script from preemptive to
    **detect-then-fix**: do nothing before a recording starts; once its
    scheduled start passes, sample the output file's size twice ~15 s
    apart; if it's genuinely growing (>200 KB and increasing — well
    above the ~20 KB stub size from a refused stream), leave it alone —
    the common case now has **zero** live-viewing disruption. Only if
    it isn't growing does it intervene, and the intervention mirrors the
    manual recovery from the same night's earlier incident: `DELETE`
    the dead timer, a guarded Threadfin restart, `POST` a fresh timer
    from `/emby/LiveTv/Timers/Defaults?programId=<id>` with the original
    padding preserved. Trades ~1-2 min of detection lag in the rare
    stuck case for no disruption in the common one. Verified with 4
    synthetic scenarios (healthy/growing, stuck/stalled, outside the
    check window, already-resolved) plus a live no-op run confirming
    Threadfin's container never restarts when nothing is due. State file
    format changed (`guard-watch-state.json`, keyed by timer id, replaces
    `guard-handled-timers.json`) — old file preserved as `.pre-20260719-guard-redesign`.
  - **Stop:** this one isn't ours to fix. Jellyfin shares one underlying
    live stream between a live viewer and a same-channel recording
    (`AllowStreamSharing: true` — intentional, it's why watching and
    recording together doesn't need a second tuner slot). Confirmed via
    a clean natural-end data point (no manual intervention): at the
    recording's `EndDate`, Jellyfin logs `Live stream consumer count is
    now 0` then `Closing live stream` — tearing down the *shared* stream
    object, which forces a still-watching client to silently reconnect.
    This is Jellyfin's own live-stream lifecycle management; turning off
    stream sharing would make things worse (a channel already recording
    would flatly refuse a live viewer, tuner=1). Documented as an
    accepted, minor, unavoidable side effect rather than "fixed."
- **Panel anti-abuse (learned 2026-07-06):** hammering the Xtream API
  (the first series backfill ran at ~10 req/s) gets the account/IP
  temp-banned — the panel then answers **HTTP 403 to everything,
  including live streams** (symptom: Threadfin ffmpeg exits with
  EC 1204 and zero bytes; direct stream curl → 403). It lifts on its
  own after a cool-down. The series fetch now sleeps 0.5 s between
  `get_series_info` calls — don't lower it, and don't run extra API
  probes while a backfill is running.
- **Do not** run another guest with CT 105's MAC, raise the tuner count,
  map `/dev/dri` into Jellyfin, or start recordings you expect to keep
  while messing with the Swiss tunnel.

## 7. Observability (CT 107, "log-server") — built 2026-07-19

Built after the channel-117 diagnosis exposed a real blind spot: the
router's own log buffer is tiny (~64 KB, minutes of history) and
`wg show` only reports current tunnel state — "was the VPN actually
stalled" was structurally unanswerable after the fact. This closes
that gap and gives every log source on the network a durable home.

**Stack:** Grafana Loki + Grafana + Grafana Alloy, docker-compose at
`/srv/log-server/docker-compose.yml` inside CT 107. Chosen over
ELK/Graylog specifically for homelab resource constraints — Loki
indexes only labels, not full text, so it's a fraction of
Elasticsearch's footprint. Chosen Alloy over Promtail because Promtail
is EOL as of 2026-03-02; Alloy is the maintained successor and also
does metrics/traces, so it's not a log-only dead end if this grows
into fuller observability later.

- **Access:** Grafana at `http://192.168.9.164:3000` (admin / password
  in `/root/.grafana_admin_password` on pve-01, 600, root-only — not
  pasted in chat or committed anywhere, same handling as every other
  credential on this box). Loki API at `http://192.168.9.164:3100`.
- **Image tags (pinned, never `:latest`):** `grafana/loki:3.7.3`,
  `grafana/grafana:11.6.16`, `grafana/alloy:v1.17.1`.
- **Sources feeding it, all verified live:**
  - **Router syslog** — `uci set system.@system[0].log_ip=192.168.9.164`
    (+ `log_port=514`, `log_proto=udp`) on the Flint 2 forwards its
    live log stream. BusyBox syslogd isn't strictly RFC-compliant, so
    Alloy doesn't receive it directly — `/root/router-log-receiver.py`
    (plain UDP listener, systemd service `router-log-receiver.service`,
    **runs inside CT 107**, only accepts datagrams from `192.168.9.1`)
    normalizes it to `/root/network-logs/flint2-syslog.log`, which
    Alloy tails (`job="router-syslog"`). Revert: `uci delete
    system.@system[0].log_ip` on the router (+ `log_port`/`log_proto`,
    `uci commit system`, `/etc/init.d/log restart`).
  - **WireGuard tunnel health** — `/root/bin/wg-snapshot.sh` +
    `wg-snapshot.timer` (every 2 min) **run on the host** (pve-01,
    needs the host's existing non-interactive root SSH key to the
    router — that key isn't in CT 107) — polls `wg show wgclient1
    dump` and pushes straight to Loki's HTTP push API
    (`job="wg-snapshot"`). No file, no Alloy involved for this source.
  - **Chromecast/Google TV logcat** — `/root/bin/chromecast-logcat.sh`
    + `chromecast-logcat.service` (**runs on the host**, `adb` paired
    2026-07-19). Streams `logcat` filtered to network/media-relevant
    tags plus all errors, pushes to Loki (`job="chromecast-logcat"`).
    ⚠️ **The Wireless Debugging connect port is not stable** — it can
    change after the device reboots or is re-paired (unlike the
    one-time pairing port, Android doesn't guarantee this one stays
    put either, in practice). If this service starts failing to
    connect, check Settings → System → Developer options → Wireless
    debugging on the TV for the current `IP address & Port` and update
    `CHROMECAST_ADDR=` in `/etc/systemd/system/chromecast-logcat.service`,
    then `systemctl daemon-reload && systemctl restart
    chromecast-logcat.service`.
  - **CT 107's own container logs** (self-observability of this stack)
    — Alloy's `loki.source.docker` component, via the bind-mounted
    Docker socket, labeled by `container` (not `job`).
  - **CT 105's jellyfin/threadfin container logs (2026-07-19, same
    day):** Docker's native Loki logging driver, not Alloy — a
    `logging:` block on each service in CT 105's compose
    (`driver: loki`, `loki-url:
    http://192.168.9.164:3100/loki/api/v1/push`, `loki-retries: "3"`,
    `loki-batch-size: "400"`). Query by **`compose_service`**, not
    `container` — the Docker driver's own label scheme differs from
    Alloy's docker-discovery labels (also sets `compose_project`,
    `container_name`; CT 107's Alloy-collected containers use
    `container` instead). This is the one piece that touched *live*
    production, so it went through the full recording-safety
    discipline: `threadfin_ctl.recording_in_progress()` checked
    immediately before `docker compose up -d`, re-checked again right
    before (agy prepared the plugin install + compose edit in advance
    but was explicitly barred from running the recreate itself — see
    §8). The recreate triggered the **ephemeral-port bug** (documented
    Operations, §6) exactly as it has before; `threadfin_ctl`'s
    verified-restart recovered it on the first attempt without any
    manual intervention — a real-world proof the fix holds up, not
    just the synthetic tests from 2026-07-19 earlier the same day.
- **Retention:** ~30 days via Loki's compactor, filesystem storage
  under `/srv/log-server/loki-data` (bind-mounted, survives container
  recreates).
- **Built primarily by agy** (see §8) in `build` mode — it hit and
  self-corrected two real issues (an unsupported Loki config property
  causing a crash-loop; an Alloy label rule causing scraped lines not
  to land) before reporting success, then Claude Code independently
  re-verified every claim (fresh curl to `/ready` and `/api/health`,
  fresh Loki queries per source, confirmed CT 105 untouched) rather
  than trusting the report as-is.
- **Alerting (added 2026-07-21):** two new Loki job streams feed Grafana
  Alerting rather than any new service. `sync/loki_alert.py` (CT 105) is
  a small fire-and-forget HTTP-push helper, same pattern as
  `wg-snapshot.sh`, that never raises (a Loki outage must never be able
  to break the sync or the health check). `threadfin_ctl._write_alert()`
  pushes to `job="media-core-alerts"` alongside its existing
  `.threadfin_alert` marker file; `xtream-sync.py`'s `build_epg()` pushes
  a structured per-run summary to `job="epg-sync"`
  (`real=`/`synth=`/`ppv=`/`total_channels=` plus each external source's
  `matched=`/`pending=`), and `main()` pushes `event=sync_complete` on
  success or an alert on any unhandled exception. Five alert rules live
  in the Grafana folder **Media-Core**: the original three (provisioned
  via the API) — any `media-core-alerts` line in 15 min; no `epg-sync`
  `sync_complete` in 25h (nightly timer silently dead); real-channel
  coverage under 380 in 25h (regression — baseline 421/996 as of
  2026-07-21) — plus two new Prometheus-based network rules (provisioned
  via file, `provisioning/alerting/network-alerts.yaml` on CT 107,
  added 2026-07-23): NIC carrier-down events on `enp2s0` (any link flap
  within 5 min) and sustained link-down (>30s). The Prometheus
  datasource is likewise file-provisioned
  (`provisioning/datasources/prometheus.yaml`). Notification:
  Grafana → email, sent from a dedicated `kopr.notify@gmail.com` (App
  Password in `/srv/log-server/.env`, 600, `env_file:` in compose — never
  in the compose YAML or committed) to the owner's own address.
  **`smtp.gmail.com:587` is blocked outbound from this network — use
  `:465` (implicit TLS) instead**, confirmed via a direct `/dev/tcp`
  probe (587 timed out, 465 connected instantly); this is a router/ISP
  policy, not a Grafana or Gmail issue, so it'll bite again if anyone
  ever "fixes" the port back to 587. The host's own postfix (for system
  emails — cron failures, security notices) also relays through the same
  `kopr.notify@gmail.com` account via port 465 (configured 2026-07-23;
  SASL credentials in `/etc/postfix/sasl_passwd`, 600); before this fix,
  host postfix tried direct MX delivery on port 25 which was blocked by
  the OpenVPN tunnel, silently dropping all system email for days.
  Deliberately no per-external-source
  zero-match rule — several sources are structurally always near-zero
  for this lineup, so a blanket threshold there would just be noise.
- **Alert-rule format bug fixed (2026-07-22):** the "nightly EPG sync did
  not complete" rule fired with `DatasourceError` / "invalid format of
  evaluation results for the alert definition C: looks like time series
  data, only reduced data can be alerted on" instead of its real
  condition. Root cause, present in **all three** rules built
  2026-07-21, not just the one that paged: query A was a Loki **range**
  query (`intervalMs: 1000` over the 25h window) feeding directly into a
  **threshold** condition C that expects one already-reduced number —
  since the LogQL itself already aggregates the whole window
  (`count_over_time(...[25h])`, `min_over_time(...[25h])`), the range
  query returned thousands of points where C wanted exactly one. Fixed
  by flipping query A to an **instant** query on all three rules (no
  separate Reduce step needed, since the aggregation is already inside
  the LogQL); verified via Grafana's ad-hoc `/api/v1/eval` endpoint
  before touching the live rules, then confirmed all three show
  `health: ok` / `lastError: None` in production. Separately confirmed
  the underlying alarm was a false positive — CT 105's 04:00 sync had
  actually completed cleanly (`sync complete` logged 04:10:25) — so
  nothing on the sync side needed fixing, only the rule. The Grafana
  admin password wasn't recoverable from anywhere on either host (not in
  `.env`, compose files, or shell history — entered once interactively
  and never saved), so it was reset via `grafana-cli admin
  reset-admin-password` inside the `grafana` container to fix the rules
  via the provisioning API; the owner has the new value out-of-band and
  should store it properly (this repo's own secrets rule applies to it
  too — never commit it here).
- **Dashboards + Prometheus/node_exporter/pve-exporter build (2026-07-22):**
  three Grafana dashboards live in the Media-Core folder — "EPG Sync &
  Coverage," "Streaming & Reliability," "Host & Proxmox Resources" — all
  built via the dashboard API with every panel's query tested directly
  against its datasource first (same discipline as the alert-rule fix
  above), not just trusted because the JSON saved cleanly. One real bug
  caught this way: `| logfmt` with no field list promotes *every* parsed
  key to a label, so an unwrap query on `real` was silently split into
  two near-duplicate series whenever an unrelated field like
  `total_channels` ticked between two nightly runs in the same window —
  fixed by scoping logfmt to just the field being unwrapped (`| logfmt
  real | unwrap real`), the same class of bug as the alerting fix, just
  showing up in a panel instead of a condition. New instrumentation to
  feed the dashboards: `xtream-sync.py`'s `main()` now pushes
  `duration_s=` on the `sync_complete` event; `build_vod()`/
  `build_series()` now push `event=vod_summary`/`event=series_summary`
  with write/duplicate/pruned and fetched/failed/written counts —
  additive only, verified end-to-end by calling `loki_alert.push()`
  directly with the real message shapes rather than forcing a live
  4am-only sync run mid-afternoon to prove it (backup:
  `xtream-sync.py.pre-20260722-metrics-instrumentation`). `wg-snapshot.sh`
  (host) now also derives and pushes a clean `event=wg_health
  handshake_age_s=<n>` line per tick from the raw `wg dump` blob it
  already captured, instead of leaving that number buried in an
  unparsed multi-line string (backup:
  `wg-snapshot.sh.pre-20260722-health-metric`). For host/guest resource
  metrics — nothing Loki can produce on its own — added a real
  Prometheus + exporter stack: Prometheus itself (`prom/prometheus:v3.2.1`,
  docker, CT 107, 30-day retention, joins the existing Grafana/Loki
  compose file) scrapes `prometheus-node-exporter` (Debian package,
  native systemd, port 9100) installed on the host and CT 105/107/108 for
  OS-level CPU/memory/load/filesystem/network, plus
  `prometheus-pve-exporter` (installed via `pipx` on the host, port 9221,
  systemd service `pve-exporter`) for Proxmox-API-level per-guest
  CPU/memory/disk/uptime and per-storage usage. The pve-exporter
  authenticates as a **new, purpose-built Proxmox user**
  (`pve-exporter@pve`) holding only the built-in **PVEAuditor** role
  (read-only, cluster-wide — cannot start/stop/reconfigure anything),
  with its own API token (`pve-exporter@pve!metrics`) rather than reusing
  any existing credential; config lives at `/etc/prometheus/pve.yml`
  (600, host-only). All 5 Prometheus scrape targets confirmed `health:
  up` before any dashboard was built on top of them.
- **Router metrics + 4th dashboard "Network: Router & Tunnels"
  (2026-07-22):** extended the same Prometheus stack to the Flint 2
  router itself, not just the media-core boxes. Installed
  `prometheus-node-exporter-lua` (a lightweight Lua rewrite of
  node_exporter, from GL.iNet's own firmware package repo — matches the
  router's actual `mediatek/mt7986`/OpenWrt 21.02 build rather than a
  generic binary) plus the `wifi_stations`, `netstat`, `openwrt`,
  `nat_traffic`, `textfile`, and `uci_dhcp_host` collector modules, and
  `nlbwmon` for its own LuCI-side historical bandwidth accounting. Hit
  one real bug getting it reachable: `listen_interface` set to `lan`
  (the correct logical interface name, confirmed via `ubus call
  network.interface dump`) still left the exporter bound to
  `127.0.0.1` after a config reload — the init script's
  `config_load prometheus-node-exporter-lua.main` doesn't resolve the
  named interface correctly on this build. Fixed by using the
  script's own explicit wildcard case instead (`listen_interface='*'`,
  which the init script special-cases to `bind=0.0.0.0` directly,
  sidestepping the broken interface-name lookup entirely) rather than
  chasing why the named-interface path fails. Added the router as a
  new Prometheus scrape target (job `router`, confirmed `health: up`
  before building anything). One useful discovery that simplified the
  build: the `nat_traffic` collector's `node_nat_traffic{src,dest}`
  metric already carries real per-flow byte counts by source/destination
  IP — a genuine top-talkers signal for free, with no need for the
  custom nlbwmon-to-textfile scripting glue that was the original
  plan (nlbwmon stayed installed anyway, for the router's own
  independent historical view — a second read on the same question
  from a different angle rather than a single point of failure). New
  dashboard covers: VPN tunnel throughput for all three tunnels
  (`wgclient1`, `ovpnclient1`, `ovpnclient2` — confirmed real, non-zero
  rates before calling it done), connected WiFi clients + per-station
  signal/rate detail, conntrack table usage as the connections metric,
  top source/destination IPs by traffic, and router CPU/memory/load/
  uptime. Separately investigated the owner's Chromecast-metrics
  question and found something already broken rather than something
  to build fresh: the existing `chromecast-logcat.service` (§7,
  2026-07-19 — ADB-streams filtered Google TV logcat to Loki) has been
  silently disconnected since **2026-07-20 22:19** (`adb devices`
  empty, stuck in a reconnect loop logging "No route to host"); an
  mDNS scan (`avahi-browse`, installed fresh for this check) found no
  `_adb-tls-connect._tcp` advertisement either, so the wireless-
  debugging IP/port most likely changed or got toggled off on the TV
  itself — not fixable remotely without eyes on the TV's own Settings
  screen. Owner deferred reconnecting it for now; left documented
  rather than silently working around it.

## 8. Claude Code ↔ agy — division of labor

Two AI agents work this box: **Claude Code** (this file's primary
audience) and **agy** (`/root/.local/bin/agy`, a separate CLI on its
own Gemini/Cloud-Code-Assist quota — genuinely separate billing from
Claude's, which is *why* offloading to it saves cost, not just a
manner of speaking). Built 2026-07-19 after a multi-log-source
diagnosis (the channel-117 incident) took a large chunk of a Claude
Code session to investigate manually.

**Split:**
- **agy**: read-heavy investigation (correlating logs across
  Threadfin/Jellyfin/systemd/router), and — as of 2026-07-19,
  owner-approved — well-scoped **build** work, especially anything
  isolated to a new/standalone container where a mistake is cheap to
  undo. The whole log-server stack in §7 was built this way.
- **Claude Code**: synthesis, verification (spot-check agy's claims
  against raw evidence directly — re-run a handful of the exact
  commands it says it ran, don't just re-read its report), and by
  default anything touching the *live* media-core stack's running
  state, secrets, or git/PR history.
- **Trust boundary is the owner's call, revisit as needed** — currently
  "simple tasks, or whatever Claude Code is comfortable with the plan
  for." Isolated/new-container work: agy may apply directly. Anything
  touching CT 105's running state: proposed, not applied, without
  explicit sign-off *unless the owner explicitly asks agy to execute
  it* (first happened 2026-07-22 — see History; the config edit and
  backup applied cleanly, but the change collided numerically with an
  unrelated block from earlier the same night in a way the exact
  procedure handed to agy didn't call out, and the full lineup-change
  cascade — sync + renumber + a 10-16-min guide refresh — comfortably
  exceeds `agy-task.sh`'s print-timeout even at 30-45 min, so the
  wrapper reported `failed` for real completed work twice in a row.
  **Always verify a "failed" agy build task's actual on-disk/live
  state directly before assuming nothing happened** — check for the
  backup file, diff the config, hit the real API — rather than trusting
  either the wrapper's exit code or agy's own last-printed line, and
  budget for a follow-up numbering check whenever a delegated change
  grows a block that something else is positioned immediately after).
- **Overnight maintenance window (2026-07-20): 01:00-05:00 local
  (Europe/Berlin), `sync/maintenance_window.py`.** Either agent may use
  the IPTV provider's single tuner connection for
  troubleshooting/verification/scraper-testing during this window
  *without* asking the owner first — check `maintenance_window.is_open()`
  (script exit 0 = open) before touching the tuner. Auto-tightens around
  any scheduled Jellyfin recording (padding included) and reopens once it
  ends. Outside this window, tuner-touching troubleshooting still needs an
  explicit go-ahead each time. Added after live-testing EPG swaps against
  the tuner during evening viewing hours caused real playback failures for
  the household — this exists so that never has to happen again to learn
  the same lesson.

**Mechanism:** `/root/bin/agy-task.sh` (rewritten 2026-07-23 for
reliability). Subcommands:

```
agy-task.sh run   <slug> <mode> "<prompt>"|@file [options]
agy-task.sh chain <slug> <mode1,mode2,...> "<prompt>"|@file [options]
agy-task.sh status [slug]
agy-task.sh list   [--since <N>d|h]
agy-task.sh read   <slug|report-file>
agy-task.sh cancel <slug>
```

Modes: `diagnose` (read-only, 10m timeout), `plan` (investigate +
propose, 15m), `build` (scoped writes, 20m), `raw` (no guardrails,
20m). Each mode injects appropriate guardrails automatically.
`@/path/to/file` for the prompt avoids heredoc-through-sudo breakage.

Options: `--timeout <seconds>`, `--context <file>` (feed a previous
report as input), `--bg` (background with PID tracking), `--retry <N>`,
`--model <name>`, `--effort <level>`. Default model:
`gemini-3.5-flash-high`.

`chain` pipelines multiple modes sequentially (`diagnose,plan,build`),
feeding each step's report as `--context` to the next — useful for
full diagnose→plan→build workflows in one call.

State tracking lives in `/root/agy-reports/.state/<slug>/`
(status/mode/started/finished/exit_code/summary). Reports write to
`/root/agy-reports/<UTC-timestamp>-<slug>.md`. Backward-compatible:
bare `agy-task.sh <slug> <mode> "<prompt>"` still works (treated as
`run`).

**Dispatch pattern:** launch with `--bg`, then `Monitor` watching the
agy PID for exit (`while kill -0 <pid>; do sleep 5; done`) — **not**
repeated manual polling with scheduled wakeups, which burns turns for
no signal. Use `agy-task.sh status <slug>` to check state without
re-reading the full report.

**Stream detection:** `/root/bin/check-iptv-stream.sh` (added
2026-07-23) detects active IPTV streams and assesses Threadfin restart
safety. Checks four things: (1) Chromecast/TiviMate streaming via
router conntrack byte-counter diffing (SSH to the Flint 2, two
snapshots 2s apart, any connection from `192.168.9.183` growing
>250KB = active stream), (2) Threadfin's own stream state (ffmpeg
processes + stream-tracking directory), (3) Jellyfin recording status
and upcoming timers (2h lookahead via the DVR API), (4) TiviMate
file-based recording detection (same as `threadfin_ctl`). Key design
point: **TiviMate connects directly to the IPTV provider, not through
Threadfin** — so a TiviMate stream does NOT block a Threadfin restart.
Only active Threadfin streams (Jellyfin Live TV) and recordings block
restarts. If TiviMate is streaming AND an upcoming recording is
scheduled, the script warns about the tuner conflict (tuner limit = 1).
Three modes: bare (human-readable status), `--json`, `--restart-ok`
(exit 0/1 for scripting).

## 9. Loose ends

- [x] Create the first Threadfin web-UI user (done by owner, 2026-07-05).
- [x] Change the Jellyfin `root` password (done by owner, 2026-07-05).
- [ ] Jellyfin's scan of the `.strm` movies (~27.7k since the 2026-07-08
      exclude-mode expansion) takes hours and hammers TMDB; artwork fills
      in progressively — let it finish before judging the "Movies"
      library (stale pre-rename entries disappear when the scan's cleanup
      pass runs). Same for the "Series" library (2026-07-06) — genre
      views in both libraries fill in as TMDB metadata converges
      (~240 items/h measured behind the old 10 Mbit tunnel; should be
      much faster since the 2026-07-14 WireGuard migration).
- [x] Many v7 picks are high-bitrate feeds (Cinema TV "4K" loops, Soccer
      PPV "8K EXCLUSIVE" slots) that buffered behind the ~10 Mbit OpenVPN
      tunnel — resolved by the 2026-07-14 WireGuard migration (~102 Mbit
      now). If a client still buffers, remove its OpenVPN-era bitrate cap.
- [ ] EPG match-rate on the new i.mjh.nz feeds is modest (Pluto 24,
      Samsung/Plex/Roku 0 on first run — their display names differ from
      the panel's "GO:"/"PRIME:" naming). `epg_aliases` entries in
      `config.json` can hand-map high-value channels one by one.
- [ ] A client on the LAN polls Jellyfin every ~3 s with a stale/invalid
      token (log spam: `Invalid token`). Sign out and back in on the
      Jellyfin apps (Android TV etc.) — sessions were invalidated by the
      password change.
- [ ] Chromecast clients: install Jellyfin app → Add server manually →
      `http://192.168.9.50:8096`.
- [x] Optional: TiviMate on the Chromecast (`192.168.9.203`) for casual
      channel-surfing — **close it before scheduled recordings**
      (1-connection account) is now backstopped by
      `media-core-guard.timer` for the Threadfin-side case (see
      Operations → DVR recording reliability), but TiviMate itself
      connects straight to the provider and isn't covered — the manual
      habit still matters until the router-block plan below is built.
- [ ] **New-content notification:** provider catalog changes are ingested
      automatically (exclude-based selection + nightly sync + scan
      triggers) and surface in each library's "Latest" row, but nothing
      *notifies* the owner. Follow-up: have the nightly sync diff counts
      night-over-night and emit a "▲ N new movies (X Netflix, …), M new
      shows" summary — destination TBD (email / visible file / push).
- [ ] **Router-level TiviMate block during recordings** (see Operations →
      DVR recording reliability for the diagnosis): extend
      `pre-recording-guard.py` to also block `192.168.9.203` /
      `1c:53:f9:26:34:e9` at the Flint 2 for the guard window, closing the
      one gap the 2026-07-16 fix doesn't cover (TiviMate holding the
      provider's 1-connection cap directly, bypassing Threadfin).
- [ ] Host housekeeping (pre-existing): disable enterprise apt repo, delete
      VM 102's `unused0` disk, consider off-host backups.
- [ ] **Undocumented `metv.py` found in CT 108 (2026-07-21).** A working
      MeTV schedule scraper already exists in `/srv/scrapers/` and is
      wired into `run_scrapers.sh`'s loop, but was never mentioned in any
      changelog entry and isn't wired into CT 105's `external_epg` config
      — looks like unfinished prior work (agy or an earlier session).
      Worth asking the owner about before extending it further.
- [ ] **258 SYNTH-only channels remain** (REAL/SYNTH/PPV: 421/258/317 of
      996 as of 2026-07-21 evening — was 384/295/317 that morning; the
      Bally regional expansion + solid-matches batch closed 37 more the
      same day). Remaining known gaps needing an actual scraper, not an
      alias (confirmed absent from all 3 aggregator sources, not just a
      naming mismatch): RT News, BBC Earth, Hallmark Drama, HGTV, Radio
      Bremen TV, Bally Sports Arizona/Prime Ticket/Great Lakes. The
      ambiguous plain "Bally Sports Ohio"/"Ohio Plus" pair (no city
      qualifier — Cincinnati vs. Cleveland branding unclear) was
      deliberately left unaliased rather than guessed.
- [x] **Front-load the Packers/Badgers/Bucks/Brewers channels — built
      2026-07-21 night, lineup v9, see History for full detail and §5 for
      the current channel map.** Bally/FanDuel Sports Wisconsin, Big Ten
      + overflow, NFL Network/RedZone, NBA TV, MLB Network, and the ESPN
      family now live at ch. 120–134, right after the (deduped) Wisconsin
      locals. Green Bay's duplicate "Alt" feeds and the non-Wisconsin
      locals now form a new "Overflow Locals" block at ch. 480–502, right
      after US Cable.
- [x] **Channel-logo cross-wiring bug fixed lineup-wide — built
      2026-07-22, see History for full detail.** All 996 channels re-fetch
      their logo directly from the provider's own `tvg-logo` URL now
      (Jellyfin's own fetch-once-at-creation mechanism was the root
      cause and has no self-healing path). 15 major networks in the
      Wisconsin Sports block got real sourced logos in place of the
      provider's generic placeholder.
- [ ] **~110 more channels still show a generic/shared placeholder logo**
      (not wrong-network-mislabeled, just no distinct art in the
      provider's own catalog) — down from ~117 after agy's pass found 4
      real matches (ABC News Live, Joel Osteen, TUDN, Universo,
      2026-07-22). The rest are genuinely obscure (shopping channels,
      novelty/filler content, minor regional feeds) and honestly weren't
      findable via Wikipedia/Wikimedia Commons — would need a broader
      source (official channel sites, app store icons) to make further
      progress, not just another pass at the same sources. Also still
      generic: the Bally/FanDuel regional variants beyond Wisconsin
      (~37) — same fix pattern (source a real logo, verify visually,
      install under both `poster.jpg`/`poster.png`) would work on
      request.
- [ ] **1245 movies (of 24,738) have no artwork and Jellyfin's own
      refresh mechanism won't process them — investigated but not
      solved 2026-07-22, see History for the full troubleshooting log.**
      A specific, clearly-real title ("The 7 Grandmasters (1978)")
      never got its `DateLastRefreshed`/`ProviderIds` populated through
      a bulk refresh, a manual single-item refresh, a Jellyfin restart,
      or a full "Scan Media Library" run — looks like these items'
      refresh requests are being silently dropped somewhere in
      Jellyfin's `ProviderManager` queue, not just slow. Cosmetic only
      (titles still play fine). Good candidate for an agy `diagnose`
      pass (check Jellyfin's GitHub issues for this exact symptom)
      rather than more restart-and-hope.
- [x] **EPG failure notification — built 2026-07-21, see History for
      full detail.** Loki alerting + Grafana email now cover the failure
      modes that used to sit silent (the West/Sun and xid-collision bugs
      found earlier the same day could easily have gone unnoticed for
      weeks, the same way the ephemeral-port bug sat undetected for 17h
      on 2026-07-16 before `media-core-healthcheck` existed).
- [x] **OpenVPN → WireGuard on the Flint 2 — done 2026-07-14.** Surfshark
      WG profile loaded, `wgclient1` bound to the Zurich `peer_1501`
      (after the firmware's wrong-peer surprise — see Network), IPTV
      policy + kill switch re-verified leak-proof, 102 Mbit/s measured.
      Old Swiss OpenVPN profile kept disabled as rollback; US tunnel
      untouched. Pre-change config backups:
      `/root/router-backups/*.bak.20260714` on pve-01.
- [x] **Post-WireGuard follow-ups — done 2026-07-18:**
      `LibraryScanFanoutConcurrency` raised 2 → 4; server-side and
      user-policy bitrate limits verified already unlimited — any
      remaining ~8 Mbit caps are **client-app settings on the devices**
      (each app: Settings → Playback quality), owner to check.
- [x] **QSV low-power encode — live 2026-07-18.** Host rebooted (owner
      go-ahead), HuC firmware RUNNING (`ehl_huc_9.0.0.bin`), Jellyfin
      low-power H264/HEVC encoder options enabled, verified with a real
      DVR-recording downscale: `-codec:v:0 h264_qsv` in the transcode
      log at **8.39x realtime** (was 2.69x on libx264). Verification
      gotcha worth keeping: a `.strm` item is useless as a transcode
      test — Jellyfin direct-streams/remuxes it (unknown source
      bitrate), so the log shows no video codec at all; force a real
      encode with a local recording + `VideoBitrate` cap + `MaxWidth`.
      All post-reboot health checks passed (Threadfin bound :34400
      cleanly, VPN egress Switzerland, containers healthy).
- [ ] Deferred by owner (2026-07-18): per-person Jellyfin users (also
      fixes the stale-token client spam) and a recordings retention
      policy (`/media/recordings` grows unbounded; watch `lvs`).
- [ ] **Jellyfin live-TV playback issue (2026-07-14):** owner reports
      live streams play in TiviMate but were not working in Jellyfin;
      deliberately not troubleshot yet (game was on). Recordings/local
      content unaffected. Start with `docker logs threadfin` +
      `docker logs jellyfin` during a tune attempt.
- [ ] **Router hardening (from the 2026-07-14 security review** — fundamentals
      solid: WAN input DROP on both uplinks, no port forwards, no UPnP/DMZ,
      guest/IoT isolated, VPN zones DROP inbound**):**
      1. Disable SSH root **password** login (key auth from pve-01 works;
         web panel remains the fallback).
      2. Disabled **GoodCloud** remote admin (router keeps an outbound
         session to `gslb-eu.goodcloud.xyz`, account-bound since May 2024)
         if it's not actually used — unnecessary external control path.
      3. Panel serves plain HTTP on :80 alongside HTTPS (LAN-only; low
         risk) — consider HTTPS-only.
      4. Informational: VPN creds are plaintext in router config (normal);
         `netifyd` DPI/analytics daemon runs localhost-only (can be turned
         off under privacy settings); firmware 4.9.0 on an aging OpenWrt
         21.02 base — check for updates occasionally.
- [ ] **Saved plan — Immich + Oracle-Cloud free-tier front door** (photo
      backup reachable off-LAN without a VPN app on the phone; approved in
      principle, **not yet executed** — owner will say when):
      [`docs/plans/immich-oci-front-door.md`](docs/plans/immich-oci-front-door.md).
      Blocking user actions when it starts: buy a domain, add an OCI API
      key. Uptime Kuma / Home Assistant / Vaultwarden / Forgejo were also
      discussed as good fits for the box's spare RAM (CPU is the scarce
      resource — nothing that transcodes or runs ML constantly).

## 10. History

| Date | Event |
|---|---|
| ≤2024 | Box built: PVE + KDE desktop, pfSense experiments (Tailscale snapshots), desktop VMs. Old LAN `192.168.8.0/24` behind a GL-AXT1800. |
| 2026-07-04 | Media-Core project adopted (manifest imported). Router side prepared on the Flint 2: static lease `.50` for VM 103's MAC, Swiss OpenVPN tunnel `VM103-Swiss` (kill switch, MAC-bound). VM 103 found unfit (EOL Fedora, no Docker, raw disk). |
| 2026-07-05 | LAN cutover done (`pve-01` → `192.168.9.11`). Owner destroyed VMs 100/101/103. CT 105 built (inherits VM 103's MAC). Stack deployed; brief egress anomaly (whole LAN behind one US exit) fixed on the router; split tunnel verified. Provider activated; `get.php` found disabled → custom Xtream sync written. Threadfin per-playlist buffer quirk found & fixed. End-to-end stream through Jellyfin verified. Threadfin auth enabled, CT sshd disabled. Docs consolidated into this file. |
| 2026-07-05 (later) | Lineup reshaped per owner: less PPV sports, more variety — Wisconsin locals first, then US News, German TV & News, US/German sports, Bundesliga (~480 channels). Sync v2: grouped/ordered selections, channel-name cleaning, logos injected into the EPG for every channel (~90% artwork coverage in Jellyfin), movie titles normalized for TMDB matching (21.8k after dedupe). Jellyfin xmltv cache gotcha documented. |
| 2026-07-05 (v3) | Added UK News (27) + UK Sports (41, Sky/TNT VIP HD) → ~550 channels, 213 with guide data. VOD dedupe made English-first. Fanart plugin installed for extra movie artwork. |
| 2026-07-05 (v4) | EPG + reliability pass. Sync v4: external XMLTV backfill (epgshare01 US/UK/DE) with call-sign/normalized-name matching + `epg_aliases` — guide coverage 192 → 322 of 549 channels (Wisconsin locals 22/23). Provider API retries; VOD prune guard (<70% ⇒ no deletes) fixes fluctuating movie counts. Nightly cascade reordered: 04:00 sync → 04:15 Threadfin → 04:30 guide refresh → 04:45 library scan (fixed daily triggers replace drifting intervals; sync also API-triggers both). Jellyfin automation API key added (CT-only). Diagnosed: movie-count churn = aborted scans + mid-scan pruning, not probing; remote ffprobe only fires on playback. VOD dedupe hardened after owner screenshots showed 4× "7 Days in Entebbe": year-less duplicate prints dropped when a (Year) copy exists, "EN-TOP - NN." compound prefixes stripped, superscript digits normalized (²→" 2") — 21,813 → 20,680 unique movies. Jellyfin gotcha: the web UI's A–Z jump bar "#" filter shows only symbol/digit titles (owner saw "844 movies"). |
| 2026-07-05 (v5) | Lineup v5 + numbering: channel numbers via `tvg-chno` blocks (100s WI locals … 850s Bundesliga), English groups before German; Wisconsin broadened to all WI markets (33), Chicago Local added (14); CSN (dead brand), TUDN EXTRA, exact-duplicate names excluded → 535 channels, EPG coverage 288/461 unique ids. Threadfin switched to XEPG mode (auto-adopts tvg-chno; 04:25 activation timer for new channels). Prune guard caught provider VOD API returning an empty list — zero movies deleted. Measured VPN ceiling ~10 Mbit/s (buffering on high-bitrate remuxes); scan fanout capped at 2; WireGuard upgrade recommended. Jellyfin empty-PresentationUniqueKey bug fixed (users saw ~1k of 20.7k movies). |
| 2026-07-06 (v6) | **Lineup v6 + TV series.** Regional block numbering per owner spec (100s WI/Chicago locals, 150s German public/regional HR-first, 200s US news+cable, 300s premium sports incl. Bally WI + DAZN DE, 400s UK TV/news/Sky/TNT, 500s German cable, 600s German sports, 650s Bundesliga) — 366 channels, strictly US/UK/DE, 100% EPG-mapped, verified live in Threadfin XEPG + Jellyfin. TV-series ingestion added to the sync (`series_categories`, .strm + NFO tree, per-show `last_modified` cache, prune guard); "IPTV Series" library created (NFO readers + TMDB/Fanart, savers off — RO mounts). Fixed: series `episodes` list-vs-dict panel quirk (season 0 = specials); empty-200 API answers now retried; live playlist got the same <70% guard as VOD (an empty `get_live_streams` answer would previously have blanked the lineup). |
| 2026-07-07 | **SMB recordings share.** Samba added inside CT 105: single share `recordings` → `/srv/media-core/media/recordings` (rw, user `tivimate` only, SMB2+, no netbios) so TiviMate records directly onto the server; same folder is Jellyfin's DVR path, so recordings surface in Jellyfin. Verified working from the client. |
| 2026-07-08 | **Exclude-mode VOD/series + library renames.** Owner supplied panel-wide exclude lists (782 live / 24 VOD / 26 series categories, all verified against the panel). Sync switched to exclude-based selection for VOD (66 cats, ~27.7k movies) and series (52 cats, ~7.5k shows); old include keys now only order the dedupe (HD before 4K/Dolby — VPN can't play high-bitrate remuxes — then EN-first). Jellyfin libraries renamed "IPTV Cinema"→**Movies**, "IPTV Series"→**Series** (owner naming). Series backfill confirmed complete (6,124 shows); the pending empty-`PresentationUniqueKey` stamp applied to 28,225 `/media/shows` rows (jellyfin stopped → UPDATE → started). Live lineup unchanged pending owner's channel-level selection from the review artifact (v6 keeps working; 4 blocks flagged as source-less under the new list). |
| 2026-07-09 | **Lineup v7 — owner's hand-picked 1,856-channel guide.** Built from the review-artifact CSV (1,599 explicit stream ids + whole Soccer PPV group; ESPN dropped, German general TV kept but moved to 2500s per owner). Compact-hundreds priority order: Locals → News → Cable → Sports → 24/7 → German. Sync gained explicit-`ids` selections, stable slot display names (`slot`), PPV event parsing (`epg_mode: "ppv"`, ~340 slots incl. times parsed as UTC from names), synthesized looping guide for ~1,143 24/7 channels, i.mjh.nz external feeds, and an hourly `media-core-ppv.timer` refresh (change-detected; Threadfin `update.xmltv` via its now-enabled API + Jellyfin guide refresh). Threadfin quirks found: keys channels by name and truncates at apostrophes (names now apostrophe-stripped — VEVO '70S/'80S had collapsed into one channel); tvg-chno only adopted for new channels (renumber-xepg.py handles renumbering). Panel carries every NBA slot twice — deduped to 16. The old "<500 channels" rule was owner-superseded; practical scale limits are guide-refresh time and EPG size, tuner stays 1. |
| 2026-07-10 | **Lineup v8 — per-city locals.** The locals blocks reorganized from state/region buckets into per-city groups, network order ABC → NBC → FOX → CBS → PBS within each city: Madison 100, Green Bay 105 (incl. DirecTV CITY backup feeds), Milwaukee 115, New York 120, Chicago 125, Denver 130, Los Angeles 135. Everything from US News (200) onward unchanged from v7 — still 1,856 channels, verified 1,856/1,856 active in Threadfin XEPG. Config-only change (`live_selections` reordered); rollback copies `*.v7` sit next to the files in `sync/`. |
| 2026-07-11 | **Threadfin ephemeral-port bug fix.** Diagnosed an issue where Threadfin failed to bind to port 34400 and gracefully fell back to an ephemeral port (causing Jellyfin guide refreshes to fail with Connection Reset). Confirmed this was triggered by a CT 105 system reboot at 13:48 local time (11:48 UTC) which left `settings.json` intact (v8 scripts do not touch it; Threadfin correctly uses a string `"port": "34400"`). A clean `docker stop` and `docker start` of Threadfin cleared the socket state and fully restored the 34400 listener and Jellyfin guide sync. |
| 2026-07-13 | **Threadfin ephemeral-port permanent fix.** The 04:25 `media-core-xepg.timer` (`activate-xepg.py`) triggered the exact same Threadfin ephemeral-port bug (fallback to random port) because it ran `docker stop` followed immediately by `docker start`, hitting a socket race condition. Added a `time.sleep(5)` before the start command in `sync/activate-xepg.py` to give Docker enough breathing room to fully release the `34400` port. Triggered Jellyfin's guide refresh manually via API to re-populate the empty guide. |
| 2026-07-14 | **Threadfin ephemeral-port bug, third occurrence — `renumber-xepg.py` patched.** Guide dead again this morning: Threadfin up but refusing 34400 (empty port in the `Web Interface:` log line, listener on a random ephemeral port). Root cause: the 07-13 "permanent fix" only patched `activate-xepg.py`, but `media-core-xepg.service` runs a **second** `ExecStart` — `sync/renumber-xepg.py` — which did its own un-throttled `docker stop`/`start` at 04:26 and hit the same socket race. Added the identical `time.sleep(5)` before `docker start` there; recovered with a clean stop → 5 s → start, then the 07:07 PPV run re-triggered Threadfin `update.xmltv` + Jellyfin Refresh Guide. Also documented the failure signature + recovery in Operations → troubleshooting. |
| 2026-07-14 | **Local channels lost names + guide data — 07-13 naming config was corrupt.** Owner reported locals showing raw stream ids ("429409") with blank guide rows. The 07-13 explicit-naming edit's *code* works (`ids` dict value → display name verbatim), but the committed `config.json` mapped every id **to itself** (`"430234": "430234"`), so the 04:00 sync wrote numeric `tvg-name`s, the call-sign EPG matcher had nothing to match (locals fell to `mc…` ids, external coverage dropped to 124), and Threadfin treated all 36 "renamed" locals as new channels (the 04:25 activation of 36). Fixed: rebuilt all 36 names from the panel's raw names per the documented `City: NETWORK ## (CALLSIGN)` convention (Green Bay CITY backup feeds suffixed ` Alt` — identical display names in one group are dropped as duplicates by the sync), broken config saved as `config.json.bad-naming-20260714`. Re-ran sync (external coverage 124 → 153, every local now carries real programmes), then update.m3u → renumber → activate → update.xmltv, cleared Jellyfin's `/cache/xmltv`, refreshed the guide. Verified lineup 100–139 shows proper names, 0 numeric ghosts. Lesson: after editing `config.json`, verify the *written playlist* (`grep tvg-chno=\"10 playlist.m3u`), not just the changelog. |
| 2026-07-13 | **Local Channel explicit naming.** Modified `sync/xtream-sync.py` to support dictionary mapping in the `ids` array of `config.json`, allowing explicit display names per stream ID. Updated `config.json` to map all 36 local network channels to beautiful, UI-friendly names (e.g. `Madison: ABC 27 (WKOW)`) so the city name is always visible on-screen. Preserved the W/K call signs in parentheses so the external EPG matcher automatically finds the correct guide data without manual `epg_aliases`. |
| 2026-07-15 | **VPN-dashboard card desync fixed.** Owner spotted the `media-core(ch)` card showing "Please select a configuration" after the WG migration; tunnel verified fully working (Zurich egress, fresh handshakes, kill-switch mark rules intact) — display-only desync because the manual `uci` peer re-bind didn't set the panel's `group_id`/`client_id` bookkeeping fields. Added `group_id='5308'` + `client_id='1501'` to the rule (no service restart; runtime untouched, egress re-verified). Backups: `/tmp/route_policy.pre-cardfix` on the router, `/root/router-backups/route_policy.pre-cardfix.20260715` on pve-01. |
| 2026-07-14 (evening) | **Swiss tunnel OpenVPN → WireGuard (10 → 102 Mbit/s).** SSH key access from pve-01 to the Flint 2 established (tmux was breaking the interactive password prompt; owner ran `ssh-copy-id` from a plain shell). CPU-bottleneck theory ruled out (router idle during transfers) — the ceiling was OpenVPN-over-TCP itself. Surfshark WG profile loaded; firmware bound `wgclient1` to a **Chicago** peer (`peer_7124`) so IPTV briefly egressed via the US — re-bound to Zurich `peer_1501` via uci; **lesson: always verify egress country after router VPN changes**. Verified: CT 105 egress Switzerland, 102.3 Mbit/s at 22% router CPU (steady with a live TiviMate stream), kill switch leak-proof both ways (tunnel down ⇒ curl times out, no US-tunnel or raw-WAN fallback). Chromecast (192.168.9.203) confirmed on the same Swiss rule. Router security review: fundamentals solid; hardening items filed in Loose ends. Pre-change config backups: `/root/router-backups/` on pve-01. Also filed: Immich + OCI free-tier front-door plan saved (not executed) at `docs/plans/immich-oci-front-door.md`; open issue — Jellyfin live TV not playing (TiviMate fine), deferred by owner. |
| 2026-07-16 | **DVR recording reliability diagnosed + fixed.** Owner reported flaky sports recordings + flaky TiviMate; investigation (Claude Code, granted permanent root via `/etc/sudoers.d/nate-claude` for this and future Proxmox-wide work) found two confirmed, distinct root causes. (1) The ephemeral-port bug recurred a 4th time overnight and sat undetected for **17+ hours** (04:25–21:51) — every hourly PPV run logged `update.xmltv failed: Connection reset by peer` with nobody watching; restored live with the documented manual recovery, then replaced the fixed-`sleep(5)` band-aid with `sync/threadfin_ctl.py`'s port-verified retry loop in `activate-xepg.py`/`renumber-xepg.py`, plus a new `media-core-healthcheck.timer` (5 min) that auto-recovers and leaves `.threadfin_alert` if recovery ever fails. (2) The two failed recordings (07-14, 07-15, both ~20 KB stub files) were confirmed via `docker logs threadfin` to be Threadfin's single-tuner slot stuck busy from an earlier stream that ended abnormally (a zombie session, not proven to be TiviMate — it was Jellyfin's own prior connection both times) — refused with `No new connections available. Tuner = 1` at the exact moment the DVR tried to start. Fixed with a new `sync/pre-recording-guard.py` + `media-core-guard.timer` (1 min): force-clears the Threadfin tuner a few minutes before every scheduled recording, and (via `threadfin_ctl.recording_in_progress()`) any Threadfin restart from any source — nightly cascade included — now skips if a recording is already active rather than risking killing it. Not yet covered: TiviMate connects straight to the provider bypassing Threadfin, so it can still hold the account's 1-connection cap outside any of the above — router-level block on `192.168.9.203` filed as a future plan in Loose ends. All new scripts deployed to CT 105, syntax-checked, and live-tested (the `renumber-xepg.py` run during testing exercised a real verified restart successfully on the first attempt). |
| 2026-07-17 | **DVR fix verified with a real recording.** Owner scheduled a 1-hour recording ("Surviving Earth") via the Jellyfin web UI (the Android app has no Record option on the guide — web UI is the reliable path) to test the 07-16 fix. `media-core-guard.timer` fired every minute through the window without issue; the recording completed cleanly as a full 1.7 GB `.ts` file (vs. the ~20 KB stubs before the fix); no `.threadfin_alert` since. |
| 2026-07-19 | **Live World Cup recording dropped mid-game — root-caused and fixed.** Owner watching + recording (Bronze Final, France v England) reported it "stopped working" partway through; owner explicitly confirmed no channel change and TiviMate untouched, correcting an initial wrong guess. Investigation found `docker logs threadfin`: FFMPEG EC 1204 at the 95-minute mark, everything local ruled out (container never restarted, no OOM, our own automation correctly no-opped, no client transcode running). Root cause: `ffmpeg.options` had no `-reconnect` flags, so the provider dropping the stream once killed the recording for good instead of it reconnecting. Recovered live: hard-linked the 95-min partial (3.65 GB) to `_recovery/` for safety, cleared Threadfin's zombie tuner state, started a fresh timer on the same still-live program with extra post-padding — new segment picked back up within ~8 minutes and ran cleanly to the owner's "match is done" signal (982 MB). Fix applied immediately after (nothing recording): added the reconnect flags to `settings.json`, verified via a direct pull from the real per-channel URL (`/lineup.json`, not a guessed path). See Operations for the full writeup and the recovery procedure for next time. Recording is now two files (a pre-drop and a post-recovery segment), not one continuous one — known limitation of this recovery path. |
| 2026-07-19 (later) | **Fixed the Android app pausing at every recording start/stop.** Owner reported a consistent multi-second stream pause/drop right around recording boundaries, correctly pushing back on an initial wrong guess (not a channel change, not TiviMate). Two distinct causes, one fixed: `media-core-guard.service`'s original preemptive design (07-16) restarted Threadfin ~4 min before *every* recording unconditionally, dropping any live viewer's stream along with everything else — confirmed by exact log-timestamp correlation with two prior playback complaints. Rewrote `pre-recording-guard.py` from preemptive to detect-then-fix: watch a new recording for real growth after it starts, only intervene (mirroring the prior incident's manual recovery) if it's actually stuck — zero disruption in the common healthy case. Verified with 4 synthetic scenarios plus a live no-op confirming Threadfin doesn't restart when nothing's due. The stop-side pause is a separate, unrelated cause — Jellyfin's own live-stream-sharing teardown when a recording's consumer count drops to 0 — confirmed via a clean natural-end log (`consumer count is now 0` → `Closing live stream`) and documented as an accepted side effect rather than fixed (disabling stream sharing would make live-viewing-while-recording refuse outright instead). See Operations for the full writeup. |
| 2026-07-19 (agy + observability) | **Channel-117 diagnosis delegated to agy; log-server (CT 107) built, mostly by agy.** Owner asked to save Claude tokens by delegating troubleshooting to agy where it fits — first real test was diagnosing the morning's channel-117 drop: agy pinpointed the exact failure (`EC: 1204` at 09:45:38, a reconnect attempt reaching "buffering" before dying again, ambiguous between TiviMate contention and a VPN blip — the same two candidates as Saturday's incident), Claude independently re-verified every timestamped claim against raw logs before reporting it (all confirmed). That worked well enough to formalize: `/root/bin/agy-task.sh` wrapper + a documented division of labor (§8). Same day, owner asked whether a proper log server made sense given the "VPN blip, unresolvable" gap that keeps recurring — researched current best practice (Loki over ELK for homelab resource constraints; Alloy over Promtail, which is EOL 2026-03-02), then had agy build the actual stack (§7) in a new isolated container (CT 107) end-to-end: docker-compose, Loki/Grafana/Alloy configs, image-tag pinning. agy hit and fixed two real bugs itself (Loki config crash-loop, an Alloy relabel issue) before reporting done; Claude Code re-verified independently (fresh curl to every health endpoint, fresh Loki queries per source, confirmed CT 105 untouched) rather than trusting the report. Also wired up two sources Claude built directly: WireGuard tunnel snapshots (`wg show` has no history — this closes that gap) and Chromecast/Google TV `logcat` via ADB (owner enabled Wireless Debugging + paired live during the session). Router reconfigured to forward syslog (its own buffer is ~64 KB, minutes of history). All four sources verified flowing with real data before calling it done. |
| 2026-07-19 (observability complete) | **CT 105 logs wired into Loki — observability project done.** Split the work at the production boundary: agy prepared everything short of touching the running containers (installed the `grafana/loki-docker-driver` plugin, edited `docker-compose.yml` to add `logging:` blocks to jellyfin+threadfin, validated with `docker compose config`, explicitly barred from running `up -d`) — Claude Code independently re-verified the diff, plugin state, and container uptime before proceeding, then did the actual production step itself: fresh `recording_in_progress()` check immediately before `docker compose up -d`, recreated both services. The recreate triggered the ephemeral-port bug exactly as documented (§6) — `threadfin_ctl`'s verified-restart recovered it automatically on the first attempt, a real production proof of that fix beyond the synthetic tests done earlier the same day. Confirmed real jellyfin/threadfin log lines landing in Loki (queryable by `compose_service`, a different label scheme than Alloy's docker-discovery `container` label — worth knowing before querying). All four log sources plus the media stack itself now flow into one place. |
| 2026-07-18 (reboot) | **QSV encode verified end-to-end.** Host rebooted on owner go-ahead (session self-terminated — Claude Code runs on pve-01; post-reboot checklist pre-staged in agent memory + README). After boot: HuC RUNNING, all services healthy on first try (incl. Threadfin on :34400 — no ephemeral-port relapse), low-power encoders enabled, `h264_qsv` proven on a real World Cup recording downscale at 8.39x realtime (3.1× faster than libx264, at far lower CPU). Interrupted library scan retriggered. First `.strm`-based verification attempt was a false test (direct-stream remux, no encode) — documented in Loose ends. |
| 2026-07-18 (hardening) | **DVR padding, scan fanout, config backups, QSV.** Owner-approved improvement batch: (1) global DVR padding 2 min pre / 60 min post (sports overtime). (2) `LibraryScanFanoutConcurrency` 2→4; server/user bitrate limits verified already unlimited (remaining caps are client-app side). (3) Nightly host-side config backup (`media-core-config-backup.timer`, 03:30 → `/mnt/pve/SSD/media-core-backups/`, keep 7) closing the mp0-not-in-vzdump gap — first design **livelocked**: `sqlite3 .backup` restarts whenever another process writes the db, and the running library scan writes constantly (99% CPU for 89 min, snapshot never converged); rewritten as `VACUUM INTO` on a read-only WAL connection, which snapshots consistently under load. (4) QSV: `renderD128` passed into CT 105 (`dev0`, gid 992) + jellyfin compose `devices`/`group_add`, CT restarted in a safe window, iHD driver verified via vainfo, Jellyfin accel=qsv (hw decode h264/hevc10/vp9/mpeg2). Encode still libx264 until the staged `i915 enable_guc=2` host reboot (Jasper Lake = low-power-only encode, needs HuC; enabling Jellyfin's LP options before HuC would hard-fail sessions). Items 5 (per-user accounts) and 6 (recordings retention) deferred by owner. |
| 2026-07-18 (wave 2) | **12 more branded libraries + icons everywhere.** Owner asked about HBO and other premium studios: no English HBO VOD/series category exists on this panel (live-only), but Paramount+/Peacock/Showtime/Sky/Discovery+/Crunchyroll/Nickelodeon (series), Discovery+ (movies) and the film studios Paramount Pictures/Universal/Marvel/DreamWorks/James Bond do — all split into their own libraries (14 new; 25 total). Custom flat brand-inspired tile icons generated + uploaded for all 22 service/studio libraries (clapperboard = Movies, episode-card stack = Series, per-brand palette + flourish: Peacock feather fan, Paramount mountain, 007 gun-barrel rings, DreamWorks crescent, Universal ringed planet, Discovery+ globe, …). Found: panel gives each VOD stream one primary category, so the James Bond collection library got 1 title (rest live in general EN categories). See Operations → per-service libraries. |
| 2026-07-18 (later) | **Per-service VOD libraries + TiviMate guard.** (1) VOD/series split by streaming service per owner spec (top-level service granularity; cross-service titles appear in every library that carries them, best print per library): `xtream-sync.py` gained `SERVICE_PATTERNS`-based bucketing with per-library dedupe/prune, 8 new read-only mounts added to `docker-compose.yml` (jellyfin container recreated in a verified no-recording window), 8 new Jellyfin libraries created via API with LibraryOptions cloned from the existing Movies/Series (savers off, NFO readers, TMDB/Fanart). First run: 24.5k movies / 7.3k shows across 10 libraries, logic pre-verified against synthetic fixtures before deploy. (2) `threadfin_ctl.recording_in_progress()` extended to detect in-progress TiviMate recordings by watching for actively-growing `.ts` files in its SMB write folders — no Threadfin restart can interrupt one anymore. See Operations for both. |
| 2026-07-18 | **Live TV tile image + guide-size trim.** (1) Built and set a custom image for the Live TV home-screen tile via the Jellyfin API directly (`/Items/{id}/Images/Primary`) — it's a `UserView`, not a normal library folder, so it doesn't appear in the Dashboard's library-image manager. (2) Guide-speed investigation + fix: see Operations → "Guide size trim" for the full writeup — lineup cut from 1,856 to 996 channels (Cinema TV removed, Prime 24/7 and DirecTV Stream trimmed to sports + genuinely-unique content only), `epg.xml` 20.7 MB → 13.1 MB. Also researched (no server-side fix available): Chromecast focus-highlight visibility (no Jellyfin Android TV setting exists; alternative clients Streamyfin/Wholphin suggested) and scoped a VOD-category-by-streaming-service feature (Netflix/Amazon/Apple TV+/Disney+ etc. as separate libraries — the provider categories already support this granularity; design questions on granularity/dedupe scope pending owner decision, not yet built). |
| 2026-07-19/20 (World Cup final night) | **Panel "ban" was a UA filter; 18 EPG source swaps; World Cup final DVR conflict; ffmpeg resilience tweaks.** Investigating what looked like the documented anti-abuse panel ban (§6, HTTP 403 to everything) turned out to be something new: the panel 403s the **default Python user-agent** specifically, not a real ban — `xtream-sync.py`'s own `MediaCoreSync/1.0` UA works fine throughout. Cost ~2 hours of unnecessary waiting; noted here so it isn't relitigated. Separately, two rounds of agy duplicate/EPG-gap analysis (7 candidate categories, then a full 996-channel sweep) found 18 channels pinned to no-EPG copies when a same-name duplicate elsewhere on the panel had real guide data — all 18 swapped in `config.json` (NFL Network, Fox Business, Sat.1 Gold, Universal TV HEVC, Boomerang, Cartoon Network, Disney XD, Baby First, Nickelodeon, MeTV Toons, Start TV, Discovery, all 6 Starz Encores), each dry-run validated via a live `build_playlist()` import before writing. ZDF/RTL Crime alternates were skipped on owner instruction (quality downgrade or negligible gain not worth the risk). A separate agy audit proposed 187 `epg_aliases` entries against external XMLTV sources; verified against the real `merge_external_epg()` code path rather than trusted — only **31 held up** (concentrated in FanDuel-rebranded Bally Sports regionals + CSN networks), the other 156 were unverifiable guesses and were dropped; added `epg_ripper_FANDUEL1.xml.gz` as a new source. Net effect: EPG coverage 277→307/957 channels. Separately, during the actual World Cup final: a live DVR/TiviMate conflict test (temporary router block on the Chromecast's non-LAN traffic, `claude-tuner-lock` iptables rule, removed same night) coincided with a genuine ~14-minute provider-side outage (confirmed via the CT 107 Loki stack: WireGuard handshakes kept renewing and router syslog showed zero WAN events throughout — the tunnel never dropped, the provider just served bad data at stream-open); the recording only captured the first ~65 minutes before the owner switched to TiviMate for the rest (not recorded — owner didn't want it re-fetched). That outage motivated a round of ffmpeg resilience tweaks aimed at matching TiviMate's better real-world reliability: `-user_agent "TiviMate/5.1.6"`, `-reconnect_on_http_error 4xx,5xx`, `-reconnect_at_eof 1`, `-rw_timeout 10000000`, `-err_detect ignore_err`, `+discardcorrupt`, probe/analyze 1MB→3MB — applied via the standard verified-restart path (clean, no ephemeral-port relapse), live tune-in tested clean. Marked **under observation**: real validation is the next provider flake, not this test. Config backups: `config.json.pre-20260720-{epg-swaps,bucket1-swaps,alias-merge}`, `settings.json.pre-20260720-ffmpeg-resilience`. |
| 2026-07-20 (morning follow-up) | **Nick Jr language-verified swap + second alias pass — 57 channels gained EPG overnight/morning.** Resolved the 3 ambiguous ES| ORANGE duplicates via ffmpeg stream-metadata probing (audio track language tag) instead of the originally planned record+multimodal-analysis approach — cheaper and definitive: Nick Jr (1677908) tagged `eng` despite the Spanish-bundle category (likely an English international kids feed bundled by the Orange telecom brand alongside its Spanish channels) — swapped in, real EPG (244 programmes). Disney Junior and Nat Geo Wild both confirmed genuinely `spa` audio — left alone. Separately ran a second, targeted alias-verification pass on the alias audits 109-channel true-residue list (call-sign matching for US locals, parent-channel aliasing for German regional opt-outs) — 7 more verified real matches (CW Atlanta/WUPA, CW Seattle/KONG, New Jersey 12 News, and 4 NDR regional variants aliased to the main NDR feed). Total tonight: 19 channels moved to real provider EPG via stream swaps, 38 more gained real programme data via verified alias/source additions — coverage 195→315/957. Config backups: `config.json.pre-20260720-{nickjr-swap,residue-pass2}`. |
| 2026-07-20 (later) | **2 more German regional aliases (BR/MDR), Chromecast ADB reconnect, transient real provider 403 diagnosed.** Third alias-verification pass found BR Fernsehen Süd and MDR Sachsen-Anhalt could alias to their parent network feeds (BR, MDR) — applied, 140/155 programmes respectively. Chromecast wireless-debugging port changed again on reboot (42467→43119, expected Android 14+ behavior — the pairing port is ephemeral by design, not a bug on our end; reconnected, updated `chromecast-logcat.service`). Separately diagnosed a real (not the UA false-alarm) transient panel 403 window 20:26–20:35 affecting arbitrary channels in Jellyfin (TiviMate unaffected — likely masked by its own retry behavior) — traced to this evenings own verification scripts making repeated `get_live_categories`/`get_live_streams` calls in a short window; confirmed cleared on retest. Lesson: cache/reuse category+stream data across scripts within a session instead of re-fetching each time.
| 2026-07-20 (evening, correction) | **403 issue was NOT resolved — still recurring; adb-auto-enable installed; scraper build dispatched to agy; maintenance-window policy added.** The prior entry's "confirmed cleared on retest" was wrong — the household hit the same `LiveTvConflictException`/zero-bytes-copied symptom again at 20:55-20:59, over 30 minutes after that claim, across more unrelated channels (AMC, Nat Geo Wild, AWE). Root cause still assessed as this evenings cumulative API+restart activity (roughly 20+ panel requests and 3 Threadfin restarts across a few hours) tripping a volume-based throttle distinct from the documented hard-ban, not a single clean event — corrected rather than left standing. All further provider-facing verification halted for the night once this was found; a passive Loki/docker-logs watch was set up instead of more active probing. Installed `adb-auto-enable` (github.com/mouldybread/adb-auto-enable) on the Chromecast via the existing ADB connection + `pm grant WRITE_SECURE_SETTINGS` — makes wireless debugging survive reboots permanently once the owner completes the one required manual step (pairing code entry via the on-device Developer Options flow, `http://<device-ip>:9093`). Dispatched agy (build mode) to write bespoke XMLTV scrapers for an owner-prioritized channel list (ESPN family, Disney Channel, Investigation Discovery, Discovery Science, Science, Bally/FanDuel regionals) into a new `sync/scrapers/` directory — explicitly barred from touching config.json/xtream-sync.py/production, one channel at a time with proof-of-output required per channel, not just claimed. Built `sync/maintenance_window.py` (see §8) — the concrete fix for tonights root problem: a 01:00-06:00 local window, auto-tightening around scheduled recordings, so future tuner-touching troubleshooting/testing doesnt collide with live viewing again. Also: accidentally broke the `AGENTS.md -> CLAUDE.md` symlink by `sed -i`-ing it directly (sed replaces symlinks with regular files, silently duplicating content) — caught via a diff check, symlink restored. |
| 2026-07-20 (night) | **Chromecast ADB pairing completed; scraper build verified independently (4 real, 5 honestly-failed); ESPN scrapers blocked by CDN WAF on the Swiss VPN egress.** Owner completed the on-device pairing step; app's own port-5555 switch hit an internal race-condition exception but the underlying OS-level switch succeeded anyway — verified independently via a real `adb shell` command over the new port rather than trusting the app's status field. Confirmed `BootReceiver` registered for `BOOT_COMPLETED`/`LOCKED_BOOT_COMPLETED` with permission granted (a first dumpsys check wrongly suggested no receiver existed — caused by an ambiguous two-device adb state, caught by retrying against the specific device rather than accepting the empty result). `chromecast-logcat.service` repointed from the ephemeral port to the new permanent 5555. Separately, independently verified agy's scraper-build report rather than trusting it: found the deliverable had been written to a coincidentally-matching but entirely wrong path on the pve-01 **host** (CT 105's `/srv/media-core` is a dedicated LVM volume, `mp0`, not host-visible) — fixed by pushing all 9 files into the real CT 105 path. Ran every scraper myself: the 4 ESPN-family scripts are genuinely solid (real HTTP fetch + JSON extraction, correct per-channel filtering, verified with fresh distinct output per channel); the 5 declined channels (Disney Channel, Investigation Discovery, Discovery Science, Science Channel, Bally/FanDuel) were spot-checked and agy's "not scrapeable" calls were accurate, not a cop-out (FanDuel's schedule URL really 404s; Investigation Discovery really is an empty JS shell with New Relic telemetry and no real content in the raw HTML). New finding from testing inside CT 105 itself rather than just the host: the ESPN scrapers fail completely over the Swiss VPN egress — `x-amzn-waf-action: challenge` from ESPN's CloudFront/WAF, meaning that IP is bot-flagged. Fix identified but not yet built: run these scrapers from the host (unflagged) on a timer and push output into CT 105, rather than running them from inside the VPN-bound container. Nothing integrated into production config tonight. |
| 2026-07-20 (late night) | **Owner proved the 403 was not a blanket provider ban; UA spoof reverted out of caution.** Owner tuned TiviMate to the exact failing channel and it worked instantly — confirmed independently via router `conntrack` (a real ESTABLISHED connection, ~10 MB transferred) rather than taking it on faith. That connection went to `185.202.100.91` (Stockholm, AS214785), not either of `cf.teltv.xyz`'s known Cloudflare IPs (`104.21.69.199`/`172.67.212.205` — confirmed stable via repeated lookups, and the Chromecast's own system DNS resolves the same two Cloudflare IPs, ruling out a resolver difference). Tested the obvious theory — hit that IP directly with `Host: cf.teltv.xyz` to bypass Cloudflare — and it disproved cleanly: 200 OK from a bare Apache default vhost, zero-byte body, not the stream. Whatever TiviMate does to reach that origin isn't a simple direct connection; parked as a task needing real packet capture, not guesswork. Separately, owner directly questioned whether the ffmpeg resilience tweaks (applied 08:18 that morning, see the World Cup final night entry) caused the evenings 403s — a fair challenge, checked rather than dismissed: found zero live-stream attempts logged between 08:18 and 20:26, meaning the first real use of the new settings was the one that failed, so the calm 12-hour gap proved nothing either way. A live A/B test (same failing stream, both UAs, back to back) succeeded cleanly on both, but given the failures have been intermittent all night, one clean test cant fully clear it. Reverted just the `-user_agent "TiviMate/5.1.6"` spoof out of caution — kept the other flags (`reconnect_on_http_error`, `reconnect_at_eof`, `rw_timeout`, `err_detect`, larger probe size), which are standard client-side reliability options that could not plausibly change how the *server* treats a request, unlike a UA string. Backup: `settings.json.pre-20260720-ua-revert`. Applied via the standard verified-restart path. |
| 2026-07-20 (close) | **New scraper container (CT 108) built; third VPN tunnel scoped; maintenance window tightened to 05:00.** Owner confirmed the mornings ffmpeg UA revert was the right precaution and the household's own testing looked stable. To fix the ESPN-scraper-vs-Swiss-VPN-WAF problem found earlier: owner pointed out the router already carries a second tunnel for the hosts own traffic (`ovpnclient1`, OpenVPN, Surfshark `us-ltm` — Latham NY — separate from CT 105s `wgclient1` WireGuard/Zurich) and asked for a **third**, dedicated tunnel + a new isolated container for all current and future scrapers, rather than running them from the host or CT 105. Investigated the routers policy layer (GL.iNets `route_policy` UCI config, which auto-generates the underlying network/firewall/ipset entries for `wgclient1`/`ovpnclient1`) — deliberately did **not** hand-edit it, since this project already has a documented precedent (the 2026-07-15 "media-core(ch)" panel desync) of direct uci edits to this exact system breaking the GL UIs own bookkeeping. Split the work instead: built CT 108 ("scraper", Debian 13, unprivileged, MAC `BC:24:11:28:55:77`, `192.168.9.115`) myself; owner to add the router-side policy (Surfshark OpenVPN, profile group `19169`, `us-lax` UDP, bound to that MAC, killswitch on) via the routers own web UI. Filed as task #8, picking up once the policy is in place: verify `us-lax` egress, deploy the 4 verified ESPN scrapers, build a sparse/jittered timer (owner specifically wants irregular timing to avoid looking like bot traffic — the same WG-ban concern that motivated the third tunnel), and wire the output into `xtream-sync.py`s EPG merge. Also: maintenance window (see §8, `sync/maintenance_window.py`) tightened from 01:00-06:00 to **01:00-05:00** at owner request — one-line constant change (`MAINT_END_HOUR`), verified at both new boundary edges before committing. |
| 2026-07-21 (morning) | **CT 108 scraper container completed end-to-end — real ESPN family EPG now live.** Owner confirmed the router-side VPN policy; verified independently (three simultaneous am.i.mullvad.net checks) that CT 108, CT 105, and the host each egress through genuinely distinct tunnels/locations (LA, Zurich, Latham NY). Live-tested a scraper immediately after: real current MLB schedule data pulled clean through the new tunnel, zero WAF challenge — the exact problem this container exists to fix, confirmed fixed. Found and fixed a real bug before wiring anything in: the scrapers only printed bare `<programme>` fragments, not valid XML — `_merge_one_source()` needs a full document with `<channel><display-name>` elements to match against. Rewrote all 4 to emit a proper `<tv>` document with display-names exactly matching our current channel names (`GO: ESPN`, etc.), so the existing generic alias-matching in `merge_external_epg()` picks them up with **zero changes to xtream-sync.py**. Built a safe wrapper script (only replaces served output on scraper success; keeps last-known-good file on failure) behind a small local HTTP server, on a sparse/jittered systemd timer (~4h ± 30 min, staggered per-channel — deliberately irregular, per the owners bot-detection concern). Hit and fixed a real permissions bug (timer runs as root, HTTP server as `nobody` — first run wrote files the server couldnt read, silently 404ing); caught by testing the actual URLs, not just trusting the timer ran. Wired all 4 as new `external_epg` sources, verified via the real `merge_external_epg()` path (4/4 matched, zero cross-contamination) before writing config.json, applied via the standard safe cascade — hit the same transient recording-check timeout as the night before and correctly retried after re-confirming nothing was actually recording. Confirmed live in the real generated `epg.xml`: ESPN 51, ESPN2 63, ESPNEWS 80, ESPNU 33 programmes (plus an unplanned bonus — ESPN8/The Ocho picked up 12 via incidental cross-match with an existing source). Backup: `config.json.pre-20260721-ct108-scrapers`. |
| 2026-07-21 (afternoon) | **All 5 "Declined" channels solved without new scrapers; xid-collision bug found + fixed; 25 channels moved SYNTH→REAL.** Owner asked for another pass at the channels agy's build-mode run had marked genuinely unscrapable (Disney Channel, Investigation Discovery, Discovery Science, Science Channel, Bally/FanDuel Sports Wisconsin), prioritizing Bally/FanDuel and offering a candidate TVInsider URL. Dispatched agy (raw mode) in parallel to dig deeper on the Bally/FanDuel regionals while doing the owner's other ask (alias-fixable gaps + genuine new-scraper candidates) directly. Owner's TVInsider lead confirmed dead: 403 even from CT 108's real US VPN egress — actively bot-blocked, not a geo/VPN problem. agy independently built a working MLB Stats API scraper for Bally Sports Wisconsin (`bally_sports.py` in CT 108, kept as a low-risk fallback) — but a direct check of the existing `epg_ripper_FANDUEL1` aggregator found it already carries this channel under "FanDuel Sports Network Wisconsin" (94 real programmes), so no new scraper was actually needed. Same story for the other 4 declined channels plus all 4 "genuine new scraper candidate" channels (MLB Network + 2 variants, Hallmark Mystery, AMC+, Cheddar) — all resolved via `epg_aliases` pointing at names already present in the existing `US2`/`FANDUEL1` aggregators, zero scraping required. While auditing aliases, found and fixed a real production bug: the provider assigns the **same** `epg_channel_id` to multiple distinct regional opt-out streams (all 10 WDR regional variants shared `WDR.de`; RBB Brandenburg shared `RBB.de` with RBB Berlin; SWR Rheinland-Pfalz/Baden-Württemberg shared `SWR.de`) — `_merge_one_source()`'s XML writer silently dropped every sibling but the first-processed one under its own display name. Added `resolve_xid()` to `xtream-sync.py` (falls back to a unique `mc{stream_id}` id when a claimed `epg_channel_id` collides) — a systemic fix, not specific to these channels. Also fixed a callsign-regex false-positive in `our_match_keys()`: short alias values like `"WDR"` matched the US-callsign pattern `[WK][A-Z]{2,4}` and got routed into a callsign-only lookup instead of a name lookup, silently breaking the WDR alias (RBB/SWR happened not to match the regex, which is why only WDR was affected) — now always emits both key types. Applied 13 new German regional aliases (10 WDR cities, RBB Brandenburg, SWR ×2) plus 12 US aliases — config backup `config.json.pre-20260721-epg-alias-xidfix`, code backup `xtream-sync.py.pre-20260721-xidfix`. Deliberately left `US: CW SAN FRANCISCO HD` un-aliased: the provider's own `epg_channel_id` for it (`KMAX.us`) is factually wrong (Sacramento's callsign, not San Francisco's KBCW) and no real external source exists — aliasing it would have shown Sacramento's schedule mislabeled as San Francisco's, worse than the existing dummy filler. Owner explicitly opened the maintenance window early ("no one is watching tv right now") to allow same-day production verification instead of waiting for the 01:00-05:00 window. Verified end-to-end in the real household-facing guide, not just `epg.xml`: found Jellyfin's `ListingsProviders` config points directly at `/epg/epg.xml` (Threadfin's own generated `threadfin.xml` is channel-list-only, zero `<programme>` elements — a red herring, not what Jellyfin actually reads); a first "Refresh Guide" run raced against the tail end of the epg.xml rewrite and left 2 of ~27 fixed channels at 0 programmes, resolved cleanly by a second refresh once the file was stable (confirmed via direct `/LiveTv/Programs` API checks by channel GUID, not just task-completion status). Corrected an earlier undercount: a naive "≥1 programme" gap scan can't tell real data from the `synth_programmes()` 24h dummy-block fallback; a proper REAL/SYNTH/PPV classifier (keyed on the synth/PPV `<desc>` marker text) put the true picture at 384 REAL / 295 SYNTH / 317 PPV of 996 total before this pass — a much larger remaining gap than the original "39 channels" estimate suggested. PR #27 merged to `main`. |
| 2026-07-21 (evening) | **Bally/FanDuel regionals expanded (13→28/39 real); 2 pre-existing wrong aliases fixed; 17 more solid-match channels aliased; 37 SYNTH→REAL total today (384→421/996).** Owner asked to wire up the remaining Bally/FanDuel regionals next, merge the morning's work to `main`, then move to the "solid individual matches" list, while thinking about EPG failure notification for the future. Auditing the existing (pre-session) Bally aliases before adding more turned up two real bugs already live in production: `US: BALLY SPORTS WEST HD` was aliased to `Fanduel Sports Sun HD` — confirmed by content, it was showing Miami Heat games under the West channel's name — corrected to `FanDuel Sports Network West` (verified after fix: golf/regional content, no more Heat). `US: BALLY SPORTS NORTH (MINNESOTA) HD` was aliased to the ambiguous bare `FanDuel Sports Network` entry; switched to the specific `FanDuel Sports Network North` (verified: Wolves/Wild content). Mapped 15 more regions individually against `FANDUEL1`'s actual channel list rather than guessing (Detroit Extra, Kansas City Plus, Midwest St. Louis, Oklahoma's `Southwest- OK2` secondary feed, San Diego, SoCal, Southeast Georgia/NC, South NC/NC2/Tennessee, Southwest Dallas/San Antonio, Sun/Miami) — left 11 regions genuinely unmappable rather than guess wrong (Arizona, Great Lakes, Prime Ticket, the ambiguous plain "Ohio" pair with no city qualifier to disambiguate Cincinnati vs. Cleveland branding, and true overflow-only "Plus" feeds with no distinct external source). Then aliased 27 more US channels + 1 DE channel from the previously-reported "solid matches" list (Yes Network incl. its "Yankees Entertainment & Sports Network" alt-name, 3 NBA TV variants, Golf Channel, Tennis Channel, 3 MeTV variants, OWN HD, 2 Bounce TV variants, Fuse, Nat Geo Wild, Big Ten + 1 overflow, ABC News Live + alt-name, Euronews, TRT World HD, SNY, Willow Sports, 4 MSG variants, Kabel 1 Classics). Hit and fixed a real bug in this batch: Euronews and TRT World kept showing dummy filler despite correct alias values — root cause was that `merge_external_epg()` scopes external sources by each channel's own region bucket, and these are "US:"-prefixed/bucketed channels while their only real source (`epg_ripper_UK1.xml.gz`) was configured under the "UK" region only, so it was never even consulted. Fixed by adding UK1 to the US source list too; verified safe by checking the matched-count came back as exactly 2 (Euronews + TRT World, no other US channel accidentally cross-matched against UK1's ~290 other entries). Reconfirmed the "first refresh after a channel's first-ever real alias often isn't enough" pattern from the morning session — twice more this round, a `Refresh Guide` cycle completed clean but newly-real channels still showed dummy content until a second cycle; channels that already had *some* data (even wrong data, like the West/North fixes) updated correctly on the first cycle every time. Net result: Bally Sports group 13→28 real of 39; overall lineup 384→421 real of 996. Config backups: `config.json.pre-20260721-bally-regional`, `config.json.pre-20260721-solid-matches`. Owner separately asked to identify and front-load the channels covering the Packers/Badgers/Bucks/Brewers — identified (Bally/FanDuel Wisconsin for Bucks+Brewers, Big Ten Network+overflow for Badgers, NFL Network/RedZone + NBA TV + MLB Network for national coverage, locals already front-loaded) but the actual renumbering (needs the `renumber-xepg.py`/`activate-xepg.py` cascade, not the EPG-only cascade used above) deferred to Loose ends rather than interleaved with the alias work above. |
| 2026-07-21 (night) | **EPG failure notification built: Loki alerting + Grafana email, no new services.** Owner picked email as the notification channel and, after some back-and-forth on which Gmail account to send *from* (settled on a dedicated `kopr.notify@gmail.com` sender, separate from the owner's own inbox — delivery still goes to the owner's address), provided a Gmail App Password. Built on top of what already existed rather than adding anything new: CT 107's Loki/Grafana stack (§7) already ingested logs and already had a proven push pattern (`wg-snapshot.sh`); `threadfin_ctl._write_alert()` already wrote a marker file on unrecovered failures, just nothing read it. Added `sync/loki_alert.py` (fire-and-forget HTTP push to Loki, logfmt-style lines, never raises — a Loki outage must not be able to break the sync or the health check) and wired it into two places: `threadfin_ctl._write_alert()` now also pushes to `job="media-core-alerts"`, and `xtream-sync.py`'s `build_epg()` now logs a structured per-run summary (`real=`/`synth=`/`ppv=`/`total_channels=` counts, plus each external source's `matched=`/`pending=`) to `job="epg-sync"` — this formalizes this session's ad hoc `full_gap_scan.py` into a permanent, queryable part of the sync itself, and doubles as a free coverage-over-time dashboard, not just point-in-time snapshots; `main()` also now pushes a `sync_complete` marker on success and an alert on any unhandled exception. Pinned the Loki datasource's UID (`uid: loki`) so alert rules can reference it deterministically. Built 3 Grafana alert rules via the provisioning API (folder "Media-Core"): any `media-core-alerts` line in the last 15 min; no `epg-sync` `event=sync_complete` line in the last 25h (catches the nightly timer silently not running/failing outright); real-channel coverage dropping below 380 in the last 25h (catches a regression like this morning's xid-collision bug before it sits for weeks — current baseline 421/996). Deliberately did **not** add a per-external-source zero-match rule — several sources (e.g. `US_SPORTS1`) are structurally always near-zero for this lineup, so a blanket threshold there would false-positive constantly; noted as a gap rather than shipping something noisy. SMTP: `smtp.gmail.com:587` was blocked outbound from CT 107 (confirmed via a direct `/dev/tcp` probe — `465` open, `587` timed out — a router/ISP policy, not a Grafana problem); switched to `465` (implicit TLS) and it went straight through. Password delivered via a `!`-prefixed command per the project's secret-handling convention (`/srv/log-server/.env`, 600, `env_file:` in compose — never in the compose YAML itself or committed); the owner's paste of that command got line-wrapped by their terminal and the trailing `chmod 600` failed with "missing operand" — turned out to be harmless, since the file already had 600 permissions from when the placeholder was first created and only the content changed. Verified end-to-end with Grafana's real receiver-test API (not just "container started cleanly") — `status: "ok"` on the first attempt after the port switch, and a real `epg-sync` Loki push confirmed by querying it back after an actual `xtream-sync.py` dry run. Backups: `docker-compose.yml.pre-20260721-smtp` (CT 107), `xtream-sync.py.pre-20260721-loki-alerts` + `threadfin_ctl.py.pre-20260721-loki-alerts` (CT 105). |
| 2026-07-21 (later night) | **Lineup v9 — Wisconsin sports front-loaded, Green Bay deduped, overflow locals relocated.** Owner asked to front-load the channels carrying Packers/Badgers/Bucks/Brewers games right after the Wisconsin locals, dedupe Green Bay's duplicate "Alt" backup feeds down to one per network, and push the non-Wisconsin locals ("overflow locals") to right after the US Cable block — with an explicit invitation to ask if anything was ambiguous. One genuine ambiguity surfaced and got clarified before building anything: "after the cable type channels" could have meant just the `US Cable` group, all national non-locals/non-PPV/non-sports groups, or nearly everything except PPV/international — owner picked the narrowest reading (just `US Cable`), which kept the rest of the lineup's relative order completely undisturbed. Did the actual broadcast-rights deep-dive before touching anything: no national ABC/CBS/NBC/FOX feed exists separately from the local affiliates in this lineup (only reachable via the already-front-loaded Green Bay/Milwaukee/Madison locals), so the real front-load candidates were Bally/FanDuel Sports Wisconsin (Bucks + Brewers regional home — owner independently confirmed the Bucks' regional-vs-national split from memory, matching), Big Ten Network + 3 overflow feeds (Badgers), NFL Network + RedZone, NBA TV, MLB Network ×2, and the ESPN family (Monday Night Football + national NBA/MLB windows) per the owner's explicit "front-load ESPN-type channels too" instruction. Deliberately left TNT/TBS out of the front-loaded block — genuine uncertainty about their current-year sports-rights standing given how much national broadcast realignment has happened recently, flagged rather than guessed. Clarified for the owner: MLB Network is a national league-owned channel like ESPN, not a per-team channel — it doesn't carry every Brewers game, just occasional national windows plus highlights/studio shows; the regional Bally/FanDuel channel is the actual full-season home for both Brewers and Bucks. Built a new `live_selections` block "Wisconsin Sports" (120–134, 15 channels) and pulled those exact channels out of their old blocks (Bally Sports, NFL, MLB, NBA, DirecTV Stream) rather than duplicating their ids — cross-referenced against `sync/threadfin/conf/playlist.m3u`'s real provider stream ids to identify the exact channels precisely rather than guessing from display names alone. Built a new "Overflow Locals" block (480–502, 23 channels: Green Bay's 3 demoted Alt feeds + New York/Chicago/Denver/Los Angeles locals, 5 each) right after US Cable; shifted HBO Max (510) and BBC & Discovery (530) down to make room without disturbing anything after them. Verified id-conservation before touching production: 741 explicit channel ids before and after the edit, exact match, nothing lost or duplicated. Owner explicitly authorized applying it immediately despite being outside the maintenance window (7:46 PM, likely prime viewing time) rather than waiting until 01:00–05:00, since this needed a Threadfin restart as part of the renumber/activate cascade (`renumber-xepg.py` → `activate-xepg.py` → `update.xmltv` → Jellyfin Refresh Guide, the documented lineup-change path, not the EPG-only cascade used earlier the same day) — checked `recording_in_progress()` first regardless, `renumber-xepg.py` came up clean on the first attempt (no ephemeral-port recurrence). Hit the same "Jellyfin's Live TV channel list itself lags behind a completed Refresh Guide" pattern seen with programme data earlier the same day, this time for channel *numbers* rather than programme content — a second full refresh cycle resolved it; confirmed via the real `/LiveTv/Channels` API (not just Threadfin's own `lineup.json`, which updated immediately — the gap was specifically Jellyfin's cached channel list) that every moved channel landed at its correct new number, and spot-checked 3 moved channels' programme data (Bally Sports Wisconsin, ESPN, MLB Network) to confirm the reorg didn't disturb their EPG. Config backup: `config.json.pre-20260721-lineup-reorg`. Channel-map table in §5 updated to v9 in the same commit. |
| 2026-07-22 (early) | **Channel-logo cross-wiring bug found and fixed lineup-wide (996 channels); 15 major networks given real sourced logos.** Owner installed Wholphin (a third-party Jellyfin client, chosen after a properly-researched recommendation — Findroid doesn't support Android TV, Streamyfin is phone/tablet-first, Wholphin is purpose-built for TV remote navigation and published on the Play Store) and immediately spotted the guide showing wrong channel icons — e.g. Big Ten Network displaying FOX's logo, ESPN displaying NBC's. Confirmed via byte-for-byte comparison this was real, not a rendering quirk: multiple unrelated channels were serving byte-identical cached image files. Root-caused properly rather than guessing: the cached file for Big Ten Network was dated 2026-07-16 (owner confirmed this has been broken "for a while") — predates this session entirely, not something introduced by the day's lineup work. Scoped the true extent before fixing anything: 42-58 distinct images were each shared across multiple channels (321-791 channels involved depending on measurement pass) and, in an earlier flawed check, appeared to show 516 channels missing an image outright (a glob-pattern bug on my end — some cached files are `poster.png` not `poster.jpg`; once fixed, all 996 channels turned out to already have *some* cached file, just often the wrong one). Traced the actual mechanism: Jellyfin's `GuideManager.PreCacheImages` only fetches a channel's logo once, at initial channel-creation time, and never revisits it on routine guide refreshes (confirmed via `/Items/{id}/RemoteImages` returning empty for a LiveTV channel — this item type doesn't go through the normal metadata-provider pipeline other item types use) — so any image that came out wrong or missing at creation time stays wrong forever without external intervention. Built `fix_channel_logos.py`: reads every channel's own `tvg-logo` URL straight from `sync/threadfin/conf/playlist.m3u` (the actual provider-supplied source of truth) and re-downloads it directly, bypassing Jellyfin's own fetch path entirely. Hit and fixed two real bugs in this script before trusting its output: (1) `photo-tmdb.com` returns "Error processing request." as a 200-OK plaintext body for Python's default `urllib` User-Agent on some URLs while a browser UA succeeds — added magic-byte validation (reject anything that isn't a real JPEG/PNG) plus a proper Chrome UA string, since the first pass had silently written that error text into a channel's image cache as if it had succeeded; (2) after the fix, Jellyfin itself started throwing `Could not find file '.../poster.jpg'` for channels whose *correct* logo extension (`.png`) differed from whatever extension was wrong-cached originally — Jellyfin's DB stores the exact filename it expects per channel, not just "any poster.* file," so changing the extension orphaned that reference. Fixed by writing every channel's corrected image under **both** `poster.jpg` and `poster.png` (identical bytes, dual-named) rather than trying to reverse-engineer which exact filename each channel's DB record expects. Result: 0 failures across all 996 channels; the remaining ~42 groups of channels still sharing an identical image are now legitimate (all Madison/Green Bay/Milwaukee/NY/Chicago/Denver/LA ABC affiliates correctly sharing one generic "ABC" mark, all ~140 obscure `GO:`-prefixed 24/7 channels and all Bally/FanDuel regional variants sharing the provider's own generic placeholder because no per-channel art exists in their catalog, all PPV numbered event slots sharing a placeholder) — not the wrong-network-mislabeling bug, just an honest neutral placeholder where the provider genuinely has no distinct art. Owner separately asked me to source real logos for anything still generic, "based on the network" — did this for the 15 channels in the Wisconsin Sports block specifically (Big Ten Network ×4, ESPN/ESPN2/ESPNU/ESPNEWS, NFL Network, NFL RedZone, NBA TV, MLB Network ×2, Bally/FanDuel Sports Wisconsin ×2): sourced official-looking logos from Wikipedia/Wikimedia Commons (rendering SVGs to PNG via Wikipedia's own thumbnail pipeline, no new tooling installed on CT 105), visually verified each one before installing, none reused the ambiguous "Bally Sports" mark since the provider's own EPG data already confirmed the current live brand is "FanDuel Sports Network." A full Jellyfin restart was needed to force served bytes to reflect the new files (owner explicitly authorized this at 9:42 PM, "freely restart the server," since it interrupts any active playback/recording — confirmed nothing was recording first) — even so, the `ImageTags` metadata hash Jellyfin reports via its API never updated to reflect the new content (the hash is a static DB field, not recomputed from the file), so any client with an already-cached copy of the old wrong image (including the owner's freshly-installed Wholphin) may need its own local cache cleared/app restarted to show the fix, even though the server is now confirmed serving the correct bytes. Separately investigated the owner's "movies missing artwork" report: an initial API query suggested nearly the entire 24.7k-movie library was missing images, which turned out to be a broken filter parameter on my end, not real data — a direct sample confirmed 19/20 movies already have proper artwork, matching the documented progressive-TMDB-fill behavior for a library this size, not a systemic problem. No specific title was reported as missing, so left as a known small/normal backlog rather than something to chase further without a concrete example. |
| 2026-07-22 (later) | **CBS/PBS icons fixed lineup-wide; first attempt at delegating live-production work to agy revealed a real reliability gap; found + fixed a channel-number collision agy's own change introduced; Big Brother + regional news channels relocated; movie artwork backfill kicked off (1251 queued).** Owner asked to (1) extend the logo fix to the ~117 remaining obscure channels via agy, (2) replace CBS's icon (too dark to see against Wholphin's dark theme) and PBS's (wanted something different) everywhere, and (3) move the 5 "Big Brother" 24/7 channels to the end of `US Cable` and 7 hyper-local news channels (NECN, New Jersey/Brooklyn/Connecticut/Long Island/Bronx/Westchester "12 News") there too — this last one explicitly delegated to agy at the owner's request ("have agy do it since you have the procedure refined already"), a first for this session: every prior CT 105 production change had been done directly. Fixed 3 more channels directly first: Green Bay's ABC/NBC-Alt/FOX-Alt locals were themselves still stuck on the generic placeholder (a residual instance of the same bug fixed earlier) despite their sibling cities being correct — copied the already-correct logos over rather than re-fetching. Sourced CBS (colorful multi-color wordmark, visible against dark UI) and PBS (2019 circular mark) from Wikimedia Commons, installed on all affected channels (14 total). Dispatched agy twice in parallel: one `build`-mode task to source real logos for the 117 remaining placeholder channels (scoped to file-sourcing only, explicitly barred from touching CT 105), one to execute the Big Brother/news-channel move using an exact, pre-validated procedure (not an open-ended task, given the live-production risk) — both calls to `agy-task.sh` reported `failed` (exit 1) due to the wrapper's print-timeout, not because the underlying work didn't happen: independently verified via direct inspection (not by trusting agy's own report) that the logo-sourcing task genuinely completed its full pass over all 117 channels (honestly reporting only 4 real matches — ABC News Live, Joel Osteen, TUDN, Universo — and correctly flagging false-positive candidates like a Jordan highway-sign image it declined to call a match for "I24 NEWS" rather than papering over the low hit rate), and that the cable-reorder task's config edit and backup **did** complete successfully. But the reorder task's automated cascade did not finish cleanly: it left `US Cable` (grown from 180 to 187 ids by the added news channels) and `Overflow Locals` (still declared at its old `start_chno: 480`) with overlapping ranges — confirmed via Threadfin's real lineup, 7 channel numbers (480–486) were each serving **two different channels** simultaneously (e.g. `480` = both NECN and "Green Bay: NBC 26 (WGBA) Alt"). Fixed directly (not re-delegated, given the live-production stakes): recalculated clean, non-overlapping ranges (`Overflow Locals` 480→490, `HBO Max` 510→520, `BBC & Discovery` 530→545) and re-ran the full cascade myself; verified via both Threadfin's `lineup.json` and Jellyfin's real `/LiveTv/Channels` API that zero duplicate numbers remain. Takeaway applied going forward: agy is workable for well-scoped, independently-verifiable production changes when given an exact procedure rather than an open brief, but its task wrapper isn't reliable for anything spanning a long cascade (sync + renumber + a 10-16-minute guide refresh comfortably exceeds a 30-45 minute print-timeout in practice) — and a change that adds channels to one block needs to account for any block positioned immediately after it, which the procedural instructions handed to agy didn't call out explicitly enough to prevent. Installed the 4 genuine agy-sourced logos after independently re-verifying each one visually. Separately, owner authorized working through the night ("no one will be watching anymore") and asked to force-complete movie artwork: found the true gap was 1254 of 24,738 movies (5%, not the ~100% an earlier broken filter had suggested) — mostly genuinely obscure content (concert films, wrestling shows, some garbled/malformed titles) — queued a paced `Items/{id}/Refresh` (`ImageRefreshMode=FullRefresh`, 0.3s between requests to avoid hammering TMDB per the documented concern in Operations) for all of them. First pass (1251/1254 queued) hit real `SQLite Error 5: database is locked` errors — it collided with the concurrently-running 35-minute Refresh Guide task, both hammering `jellyfin.db` at once; likely silently dropped most of the batch, since Jellyfin doesn't appear to auto-retry a refresh that failed on a transient lock. Re-queued cleanly once the system was idle (1254/1254 queued, 0 failures this time) — but two 30-minute waits afterward showed **zero net progress** (missing count never moved from 1254, despite queueing returning HTTP 204 "accepted" every time, TMDB itself confirmed reachable from CT 105, and no further lock errors). A Jellyfin restart did surface 9 fixed items (likely flushed from an in-flight queue at shutdown), then stalled again at 1245 for another 30 minutes. Explicitly triggering "Scan Media Library" (completed cleanly, ~8.5 min) made no difference either. Picked one specific, clearly-identifiable title ("The 7 Grandmasters (1978)," a real film that should have an unambiguous TMDB match) and confirmed directly via the API that its `DateLastRefreshed` stayed `None` and `ProviderIds` stayed empty through *all four* of a bulk refresh, a manual single-item refresh, a container restart, and a full library scan — i.e. this class of item's refresh request appears to be silently dropped somewhere in Jellyfin's `ProviderManager`/`StartProcessingRefreshQueue` pipeline, not just slow. Left unresolved rather than continuing to guess at 1 AM: this is cosmetic (every affected title still plays fine, just shows no poster), and a good candidate for a focused agy `diagnose` pass in a future session (check Jellyfin's GitHub issues for this exact symptom — a refresh request for items with zero prior provider match apparently never actually running — rather than more trial-and-error restarts). Config backups: `config.json.pre-20260721-cable-reorder`, `config.json.pre-20260721-collision-fix`. |
| 2026-07-22 (afternoon) | **Grafana alert-rule format bug found and fixed on all 3 Media-Core rules; confirmed the underlying "nightly sync failed" alarm was a false positive.** Owner got paged with a `DatasourceError` on the "nightly EPG sync did not complete" rule (built the day before, §7 Alerting) whose annotation said "no successful nightly EPG sync seen in 25 hours," but the actual Grafana error was a plumbing failure, not the real condition: "invalid format of evaluation results for the alert definition C: looks like time series data, only reduced data can be alerted on." Checked the real thing the alert claims to monitor before touching Grafana at all: `journalctl -u media-core-sync.service` on CT 105 showed the 04:00 run had completed cleanly at 04:10:25 with `sync complete` logged — the sync itself was never broken. Traced the actual Grafana bug to CT 107 (`docker exec grafana`, port 3000, `/srv/log-server` compose stack): all three alert rules built 2026-07-21 have the same structural flaw, not just the one that fired — query A was a Loki **range** query (`intervalMs: 1000` over a 25h window) piped directly into a **threshold** condition C that expects a single already-reduced number, and since the LogQL itself already aggregates the whole window (`count_over_time(...[25h])`, `min_over_time(...[25h])`), the range query was returning thousands of points where C wanted exactly one. Fixed by flipping query A to Grafana's **instant** query type on all three rules (no separate Reduce step needed — the LogQL already does the reduction); verified the fix against Grafana's ad-hoc `/api/v1/eval` endpoint before touching anything live, then applied it via `PUT /api/v1/provisioning/alert-rules/{uid}` and confirmed all three show `health: ok` / `lastError: None` in production. Hit one real obstacle along the way: the Grafana admin password wasn't recoverable from anywhere on either host — not in `docker-compose.yml`, `.env`, or shell history on the pve-01 host or CT 107, and no provisioning-as-code file for the alert rules existed either (they were built via one-off API calls the night before, confirmed by `git show` on the commit that built them) — so, with the owner's explicit go-ahead, reset it via `grafana-cli admin reset-admin-password` inside the container rather than guessing at it or trying to bypass auth another way; owner has the new value out-of-band. Separately confirmed lineup/channel-count questions the owner raised mid-session: current lineup is **v9** (built 2026-07-21 night — Wisconsin Sports front-load + Green Bay dedupe + Overflow Locals relocation), not v8; the ~996-channel count (v8's 1,856 was cut in the 2026-07-18 guide-speed trim) is correct and expected, not a regression. |
| 2026-07-22 (later afternoon) | **Built 3 Grafana dashboards + a real Prometheus/node_exporter/pve-exporter metrics stack; instrumented sync duration and VOD/series counts; upgraded WireGuard health logging.** Owner asked for "good looking and informative dashboards" plus recommended metrics worth tracking, and to install/configure whatever was needed rather than working around gaps. Built three dashboards in the Media-Core Grafana folder — EPG Sync & Coverage, Streaming & Reliability, Host & Proxmox Resources — with every panel query tested directly against Loki or Prometheus before being put in a panel, not just trusted because the dashboard JSON saved without error (same verification discipline used on the alert-rule fix earlier the same day). That discipline caught a real bug before it shipped: an `unwrap` query with a bare `| logfmt` (no field list) promotes every parsed key to a label, so the real/synth/ppv coverage-trend queries were silently returning two near-duplicate series whenever `total_channels` ticked between two nightly runs inside the same 1-day window — fixed by scoping logfmt to only the field being unwrapped, the same underlying mistake as the morning's alerting bug, just surfacing in a graph instead of a condition. Added new instrumentation to feed the dashboards: `xtream-sync.py`'s `main()` now pushes `duration_s=` alongside the existing `sync_complete` event; `build_vod()`/`build_series()` now push `event=vod_summary`/`event=series_summary` Loki lines with write/duplicate/pruned and fetched/failed/written counts. Verified this end-to-end without waiting for or forcing a live sync run — the project's own maintenance-window discipline says a full `main()` run mid-afternoon would restart Threadfin and trigger a Jellyfin guide refresh outside the 01:00–05:00 window for zero reason — by calling `loki_alert.push()` directly with the exact new message shapes and confirming the lines landed in Loki; real values populate naturally at tonight's 04:00 run. `wg-snapshot.sh` (host, pushes `wg show wgclient1 dump` to Loki every ~1s via its existing timer) now also derives and pushes a clean `event=wg_health handshake_age_s=<n>` line each tick, parsed from the same dump it was already capturing, rather than leaving tunnel staleness buried in an unparsed multi-line blob nothing could graph — verified live within seconds of deploying. For host/guest resource metrics, which Loki structurally can't produce, built a real Prometheus stack: Prometheus itself joins the existing Grafana/Loki docker-compose file in CT 107 (30-day retention); `prometheus-node-exporter` (Debian package, native systemd) installed on the host and all 3 CTs (105/107/108) for OS-level CPU/memory/load/filesystem/network; `prometheus-pve-exporter` (installed via `pipx` on the host) for Proxmox-API-level per-guest and per-storage stats. Created a dedicated, purpose-built Proxmox user (`pve-exporter@pve`) holding only the built-in read-only **PVEAuditor** role — deliberately not reusing any existing credential or granting anything beyond read access — with its own API token for the exporter to authenticate with. Confirmed all 5 Prometheus scrape targets reported `health: up` before building any panel on top of them. Backups: `xtream-sync.py.pre-20260722-metrics-instrumentation`, `wg-snapshot.sh.pre-20260722-health-metric` (host), `docker-compose.yml.pre-20260722-prometheus` (CT 107). |
| 2026-07-22 (evening) | **Router metrics + 4th Grafana dashboard ("Network: Router & Tunnels"); found the Chromecast log pipeline silently dead.** Owner asked whether there were good router metrics worth tracking (three VPN tunnels, throughput, clients, connections, top talkers) and whether Chromecast metrics were worth adding too, having just built the Proxmox/host metrics stack. Confirmed the Flint 2 actually runs three tunnels — `wgclient1` (WireGuard), `ovpnclient1`, `ovpnclient2` (two separate OpenVPN clients) — via `ubus call network.interface dump`, not just the two documented in the router's own memory notes. Installed `prometheus-node-exporter-lua` from GL.iNet's own firmware repo (matches the actual `mediatek/mt7986` build) with `wifi_stations`/`netstat`/`openwrt`/`nat_traffic`/`textfile`/`uci_dhcp_host` collectors, plus `nlbwmon` for the router's own historical accounting view. Hit and fixed a real bug getting it network-reachable: setting `listen_interface` to the correct logical name (`lan`, confirmed via ubus) still left the exporter bound to `127.0.0.1` after a uci commit + service restart — the init script's `config_load prometheus-node-exporter-lua.main` doesn't resolve a named interface correctly on this build; used the script's explicit `listen_interface='*'` wildcard case instead, which the script handles as a direct `bind=0.0.0.0` and sidesteps the broken lookup entirely, rather than spending more time root-causing an OpenWrt init-script quirk on someone else's package. Added the router as a new Prometheus scrape target, confirmed `health: up`. Discovered the `nat_traffic` collector's `node_nat_traffic{src,dest}` metric already carries real per-flow byte counts by IP — a genuine top-talkers signal for free, so the originally-planned custom nlbwmon-to-textfile-exporter glue turned out to be unnecessary; nlbwmon stayed installed regardless; as its own independent local view, not as a dependency of the dashboard. Built and verified the new dashboard: tunnel throughput for all three tunnels (confirmed real non-zero rates, not just configured-but-idle — WireGuard alone was moving ~715 KB/s at verification time), WiFi client count + per-station signal/rate table (2 clients on the `Big-GL` SSID), conntrack table usage as the connections metric, top source/destination IPs by traffic, and router CPU/memory/load/uptime. Separately investigated the Chromecast question and found something already broken rather than something to build fresh: `chromecast-logcat.service` (built 2026-07-19, ADB-streams filtered Google TV logcat to Loki) has been silently disconnected since 2026-07-20 22:19 — `adb devices` empty, stuck in a reconnect loop against a stale address. An mDNS scan for the device's wireless-debugging advertisement (installed `avahi-utils` fresh to check) found nothing either, meaning the TV's IP/port most likely changed or wireless debugging got toggled off — not something fixable without physically checking the TV's own Settings screen. Owner deferred reconnecting it for this session; documented as a known-broken item rather than silently left for someone to eventually notice the gap in Loki. |
| 2026-07-23 | **Router reboot root-caused as 0630 network dropout; daily reboot cron disabled; Threadfin stuck-tuner fix; network alert rules added; host postfix email relay fixed; agy wrapper rewritten; stream detection built.** Owner reported network dropout at ~0630, broken Jellyfin streaming, and missing email alerts. **(1) Network dropout:** kernel logs showed two NIC link-down flaps on `enp2s0` (the `vmbr0` bridge port) at 06:30:01 and 06:30:21; router syslog in Loki revealed the root cause — the Flint 2 rebooted at ~06:30 (kernel uptime `[70]` seconds at 06:31:31 post-boot, previous uptime `[86291]` = ~24 hours — a scheduled daily reboot via `gl_timer.reboot`, enabled every day at 06:30). Disabled the daily reboot: `uci set gl_timer.reboot.enable='0' && uci commit gl_timer`. **(2) Jellyfin streaming:** Threadfin's internal tuner counter was stuck at 1/1 ("No new connections available") with zero active ffmpeg processes and an empty stream-tracking directory — stale state from a previous unclean stream disconnect. Fixed by restarting Threadfin (no recording in progress, verified via `threadfin_ctl.recording_in_progress()`). **(3) No email alert:** two issues — no Grafana alert rule existed for network health (all 3 existing rules cover EPG sync only), and the host's postfix was completely broken (empty `relayhost`, trying direct SMTP on port 25 which the OpenVPN tunnel blocks — a 4-day-old email stuck in the queue since Jul 19). Fixed both: added 2 Prometheus-based alert rules via file provisioning (`provisioning/alerting/network-alerts.yaml` on CT 107) — NIC carrier-down flap detection (`increase(node_network_carrier_down_changes_total[5m]) > 0`) and sustained link-down (`node_network_carrier < 1` for 30s); also provisioned the Prometheus datasource via file (`provisioning/datasources/prometheus.yaml`). All 5 rules confirmed evaluating. Tested Grafana email delivery end-to-end via the receiver-test API — `status: ok`, owner confirmed email received. Fixed host postfix: configured relay through `[smtp.gmail.com]:465` using the same `kopr.notify@gmail.com` App Password as Grafana (SASL auth via `/etc/postfix/sasl_passwd`, 600); rebuilt `/etc/aliases.db`; test email delivered successfully (`dsn=2.0.0, status=sent`). **(4) Agy wrapper rewrite:** replaced `agy-task.sh` with a comprehensive orchestration tool: subcommands (`run`, `chain`, `status`, `list`, `read`, `cancel`), per-mode timeouts (10m diagnose / 15m plan / 20m build), background execution with PID tracking, retry logic, context chaining (feed previous report into next task), state tracking per slug (status/mode/started/finished/exit\_code/summary), and backward compatibility with the original 3-argument invocation. Default model updated to `gemini-3.5-flash-high`. Old wrapper backed up as `agy-task.sh.pre-20260723-rewrite`. **(5) Stream detection:** built `/root/bin/check-iptv-stream.sh` — detects active IPTV streams via router conntrack byte-counter diffing (SSH to Flint 2, two snapshots 2s apart from the Chromecast at `192.168.9.183`), checks Threadfin stream state, Jellyfin recording/timer status, and TiviMate file-based recordings. Key insight confirmed via router policy routing: TiviMate on the Chromecast connects directly to the IPTV provider (excluded from all VPN tunnel rules, not routed through Threadfin), so TiviMate streams are safe across Threadfin restarts — only Threadfin's own streams and recordings block restarts. Warns about tuner conflicts when TiviMate is streaming and a recording is upcoming (tuner limit = 1). Three output modes: human-readable, `--json`, `--restart-ok` (exit code for scripting). |
Historical deep-dives preserved in [`docs/archive/`](docs/archive/):
the original Media-Core manifest (imported verbatim) and the network
cutover runbook (step-by-step with rollback, now fully executed).
| 2026-07-23 (late night) | **Threadfin tuner lockup root cause fixed & watchdog cron scheduled.** Investigated recurring Threadfin tuner lockups. Verified Swiss WireGuard tunnel performance inside CT 105 (101.3 Mbps throughput, ruling out network/VPN bandwidth bottlenecks). Root cause traced to Threadfin's ffmpeg.options setting -reconnect_at_eof 1 in /srv/media-core/threadfin/conf/settings.json. When a provider stream drops, ends, or serves corrupt H.264 frames, -reconnect_at_eof 1 causes ffmpeg to enter an infinite reconnect loop inside the container instead of exiting. Threadfin's internal tuner count stayed trapped at 1/1 ("No new connections available"). Fixed by: (1) removing -reconnect_at_eof 1 from ffmpeg.options in CT 105 and restarting Threadfin (XEPG 996 channels mapped, Ready to use), and (2) deploying /etc/cron.d/threadfin-watchdog on pve-01 to run /root/bin/threadfin-tuner-watchdog.sh every 2 minutes for automated recovery. |
| 2026-07-24 | **`agy-task.sh` print-timeout alignment fixed.** Investigated false-failure incident on task `channel-lineup-fix-build-20260724` where `agy-task.sh` reported failure with exit code 1 (`Error: timeout waiting for response`) despite the substantive work completing successfully. Root cause: internal `--print-timeout` in `cmd_run()` was hardcoded to default 8m (`${AGY_PRINT_TIMEOUT:-8m}`), firing before the outer task `--timeout` (20m for build mode). Fixed by updating `--print-timeout` to default to `${AGY_PRINT_TIMEOUT:-$timeout_val}` so internal timeout matches the task budget. Updated script header comments to document the change and added a warning comment near the retry loop against blindly retrying stateful build-mode tasks. |

| 2026-07-25 | **Router factory-reset rebuild; VPN leak found and fixed; backup/restore capability built.** Owner factory-reset the Flint 2 after Tunnel 1 (SurfShark OpenVPN, scraper's tunnel) sat in a TLS-handshake reconnect loop across multiple provider regions — the saved `.ovpn` profiles dated 2024-09-15 were the leading suspect (stale credentials/static key), and the reset resolved it. Rebuild: restored pve-01's SSH key to dropbear, re-created the three tunnels through the **web UI** (owner drove tunnel creation — CLI-created tunnels aren't recognised by the UI, which keeps its own backing state), then renamed/reassigned them to match the original topology: `Primary Tunnel` (ovpnclient1, exclude-mode: FireStick + media-core + scraper), `media-core(ch)` (wgclient1, Swiss — media-core + Chromecast WiFi), `Tunnel 1` (**now wgclient2/WireGuard**, previously ovpnclient2/OpenVPN — scraper). Re-added the 5 static DHCP reservations, restored router syslog forwarding to CT 107 (verified end-to-end into Loki, not just configured), reinstalled `prometheus-node-exporter-lua` + 6 collectors and `nlbwmon` (config backups don't carry packages), and set 4 client display names. **Found a real VPN leak while spot-checking egress IPs before a speed test:** scraper was egressing on the bare WAN despite the Clients page *and* the `vpn-client.get_vpn_using_status` RPC both reporting it as VPN-protected. Root cause was self-inflicted and instructive — assigning Tunnel 1's single MAC with `uci set` stored a scalar `option from_mac` instead of `list from_mac`, which (a) crashed the Lua backend on `ipairs()`, producing the intermittent *"Unknown error occurred"* popup the owner had been seeing, and (b) left firewall ipset `src_mac8166` **empty**, so scraper's traffic was never marked for the tunnel and fell through to the main routing table. Key lesson: **an empty ipset fails OPEN, it does not blackhole** — the killswitch never engaged because the traffic was never claimed. Also learned `/etc/init.d/network reload` and `ifup`/`ifdown` do *not* repopulate ipsets (config and runtime diverge silently); the correct CLI apply path is `/usr/bin/rtp2.sh apply`, which is also what boot runs via `/etc/rc.d/S95vpn-client` — confirming the fix persists. Separately: client display names persist in `/etc/config/gl-client` (`alias`), **not** the `name` column of `/etc/oui-tertf/client.db`, which is recomputed from DHCP hostnames on every refresh (direct SQL edits get wiped on service restart — tested); LXC guests don't broadcast hostnames, hence the "Unknown" entries. Fixed a stale dashboard: "Network: Router & Tunnels" hardcoded `wgclient1|ovpnclient1|ovpnclient2`, so post-rebuild it charted a retired interface while scraper's new `wgclient2` was invisible — now `wgclient.*|ovpnclient.*`. Recovered the pre-reset WireGuard private key from Loki (`wg show <iface> dump` includes it, and `wg-snapshot.sh` had been pushing that to `job="wg-snapshot"`) but did **not** use it — owner opted to let the provider issue fresh keys via the UI, the supported path. **Backups built** so this is never re-done by hand: `/root/bin/router-backup.sh` + weekly `router-backup.timer`, capturing a `sysupgrade -b` archive (verified to contain WireGuard private keys, all OpenVPN profiles/auth, route_policy, gl-client aliases, DHCP reservations, wireless, and dropbear keys — so a restore needs no VPN-provider re-login), the opkg delta vs the factory ROM, and a human-readable summary; gzip+tar integrity-checked on capture, since a corrupt archive would otherwise only surface mid-restore. Full runbook at `docs/router-rebuild-runbook.md`, including how to regain initial access (a reset returns the router to `192.168.8.1` — confirmed from `/rom/etc/board.d/03_gl_network` — where pve-01 at `192.168.9.11` cannot reach it; three documented ways round that) and why verification must compare real egress IPs rather than trust the UI. Throughput after rebuild, against a ~104 Mbps direct-WAN ceiling (the ISP/upstream link, not the router — its WAN port negotiated gigabit): media-core(ch) **96.7 Mbps / 33 ms** (±1%, and 95% of the 101.3 Mbps documented 2026-07-23 — no regression; also the tunnel carrying IPTV, with ~12x headroom over an 8 Mbps stream), Primary **84.0 Mbps / 92 ms** (±5%, jitter ±5.3 ms — OpenVPN overhead), Tunnel 1 **83.5 Mbps / 102 ms** (±4%). All three verified leak-free by egress-IP comparison. Noted gap: `wg-snapshot.sh` polls only `wgclient1`, so `wgclient2` has no health history — the exact blind spot that made the 2026-07-24 outage unreconstructable. |
| 2026-07-25 (later) | **WiFi tweaks backed up; monitoring gaps closed; router security audit; WG keys pulled out of Loki.** Owner renamed both bands to a single `Big-GL` SSID (band steering, WPA2, HE40/HE80) — captured in a fresh snapshot and verified inside the archive before any other change. **Monitoring:** `wg-snapshot.sh` now covers *all* WireGuard interfaces, discovered from `wg show all dump` rather than hardcoded, so future tunnels are picked up with no code change — this closed the `wgclient2` blind spot that made the 2026-07-24 scraper-tunnel outage unreconstructable; both tunnels confirmed reporting to Loki. Added Tailscale health as Prometheus metrics via the already-installed node-exporter **textfile** collector (`/etc/tailscale-metrics.sh` → `/var/prometheus/tailscale.prom`, cron every 2 min) — no new services. `/var` is a symlink to tmpfs here, so the script re-creates its own output dir and cron self-heals it within a minute of boot; verified end-to-end (deleted the .prom, watched cron regenerate it in 40s, then confirmed the series queryable in Prometheus). `tailscale_exit_node_in_use` is the security-relevant metric: it must stay 0, since selecting an exit node would route traffic outside the VPN tunnels. Both new files plus `/etc/crontabs/root` added to `/etc/sysupgrade.conf` (which is itself in that list, so the additions survive a restore). **Security fix:** the old `wg-snapshot.sh` shipped raw `wg show` dumps to Loki, and the interface line of a dump contains the tunnel's **private key** — it had been logging live key material every 2 minutes. Now redacted before the push (verified: all key-bearing entries predate the deploy). Historical entries still hold the current keys until Loki's ~30-day retention ages them out; flagged as a MEDIUM finding with options (accept retention / purge via delete API / rotate) rather than silently left. **Audit** (`docs/router-security-audit-20260725.md`): verified-good — WAN input `DROP` with the *active* repeater path (`apclix0` = logical `wwan`) confirmed inside the `wan` zone, zero port forwards, no GL.iNet cloud/DDNS exposure, guest+IoT properly isolated, no exit node in use, all three tunnels leak-free. Top finding (**MEDIUM-HIGH**): the tailnet is a fully trusted zone — `firewall.tailscale0.input=ACCEPT` plus a `tailscale0 -> lan` forwarding rule and `AdvertiseRoutes=192.168.9.0/24` mean any tailnet peer reaches the router admin UI, SSH, the exporter and every LAN host — while **16 of 21 peers are offline**, several 500–972 days (`ipad-gen-6` 972d, `plex` 924d, `win11-pve` 564d, three of them still advertising themselves as exit nodes). Each retains valid credentials, so any sold/lost/reimaged device is a standing key to the LAN; recommended pruning stale nodes and adding tailnet ACLs (owner action in the Tailscale console). Also noted: SSH password auth is on (brute-force surface, but the rebuild runbook depends on it post-reset — a real tradeoff, documented rather than changed unilaterally), WPA2 rather than WPA3 (left as-is deliberately given the FireStick/Chromecast/IoT client mix), exporter readable from the tailnet, and `192.168.2.0/24` bypassing VPN policy because tailscale's routing rule sits at priority 5270 ahead of the VPN rules at 6000 (RFC1918 to another of the owner's own networks, encrypted — a VPN bypass, not a public leak; `192.168.1.0/25` is guarded by a priority-0 rule, which is also what stops it hijacking the WAN gateway). |
| 2026-07-25 (reboot test) | **Rebuilt router reboot-tested end to end; "Unknown error" popup confirmed fixed.** Deliberately rebooted the Flint 2 to prove the rebuild survives a power cycle rather than assuming it. Verified post-reboot: all three policy-routing **ipsets repopulated from config** (3/2/1, identical to pre-reboot — this is the one that matters, since it proves the VPN-leak fix lives in committed config and not just runtime state), zero leaks (all three endpoint egress IPs differ from the bare-WAN reference), both WireGuard tunnels re-handshaked, OpenVPN up, syslog forwarding traced through to CT 107, exporter 200 with the Prometheus target `up`, client aliases and the new `Big-GL` SSID intact, and — the piece most at risk — the Tailscale metrics file **recreated by cron** after `/var` (tmpfs) was wiped, which is exactly why the collector script mkdirs its own output directory. Owner confirmed the Clients-page *"Unknown error occurred"* popup is gone, closing out the `from_mac` scalar-vs-list bug. Two verification traps worth remembering, now in the runbook: `/var/log/nginx/error.log` **survives** reboots and nginx stamps it in **UTC** while `date` is local, so a raw error count looks alarming until timestamps are compared against boot time (all 165 entries here predated the fix, last at 08:33 UTC vs 08:45 boot); and `grep -c ipairs` wildly overcounts because Lua stack-trace continuation lines contain the word too — match `'lua entry thread aborted'`, which appears once per real error. Added a reboot-survival checklist to `docs/router-rebuild-runbook.md` so this is repeatable rather than a one-off. |

| 2026-08-11 | **`sports-dvr-auto` permanent automation event log (`dvr-automation-events.jsonl`) & `--history` CLI.** Root cause: working state files (`STALL_STATE_FILE`, `RESTORE_STATE_FILE`) are pruned when timers finish, leaving no historical record of extend/trim/stall/restore/stitch decisions without grepping systemd journal logs by hand. Fix: added `EVENT_LOG_FILE` (`/var/lib/dvr-dashboard/dvr-automation-events.jsonl`) and append-only `log_event()` function (logging extend, trim, stall, restore, stitch events). Added `--history N` CLI option (`print_event_history()`). Updated restore timer creation payload to copy categorization fields (`IsSports`, `IsMovie`, `IsKids`, `IsNews`, `IsSeries`, `Genres`, `Priority`) from original timer so restored recordings land in "Sports" rather than "Other". Verified via `sports-dvr-auto --history` and checking event logging during restore/extend operations. |
| 2026-08-12 | **`sports-dvr-auto` multi-segment restore chain stitching.** Root cause: in a real incident on Aug 12 (Brewers @ Padres), a recording stalled and auto-restored 4 times in a row, producing 5 separate segment files. The old stitching logic only evaluated independent pairs of (original, restore), which could only stitch the first pair into a 2-segment file and left subsequent restores unstitched. Fix: rewrote `check_restore_stitching()` and added `build_restore_chain()` to trace restore chains of arbitrary length (N segments), resolve paths for all segments via `resolve_segment()` and on-disk `timers.json`, check for timeline overlaps between consecutive segments, and concatenate all N segments using an expanded `perform_ffmpeg_concat(segment_paths, stitched_path)`. Verified against the 5-segment Brewers @ Padres incident state. |
| 2026-08-12 | **`sports-dvr-auto` restore timer ID fallback disambiguation.** Root cause: when `POST /emby/LiveTv/Timers` doesn't return the new timer ID, `trigger_auto_restore()` falls back to searching `get_existing_timers()`. Previously, it took the `break` on the FIRST match by name + channel ID. During rapid restore cycles (e.g. Aug 12 Brewers @ Padres), multiple timers shared the same name+channel (an orphaned timer awaiting cleanup and the new timer). The fallback picked an old timer ID (`ec204d87...` instead of `bcc584d6...`), causing subsequent timer extension/trim calls to hit the wrong timer ID, leaving the active recording to run for 52 minutes of post-game dead air. Fix: updated the fallback loop in `trigger_auto_restore()` to collect all matching candidates by name and channel ID, sort by `StartDate` descending, and pick the newest (most recently created) timer ID. Verified against historical timer payload logs and the Aug 12 Padres@Brewers incident state. |
| 2026-08-12 | **`sports-dvr-auto` raw segment auto-archiving (`/media/segments-archive`).** Root cause: after stitching multi-segment recordings, raw segment files remained in `/media/recordings`, cluttering the Jellyfin DVR UI with N+1 duplicate entries for a single game. Fix: added `SEGMENT_ARCHIVE_DIR` (`/media/segments-archive`) and `archive_segments()` helper function. Moves raw segment files and matching `.nfo` files out of `/media/recordings` into `/media/segments-archive` (outside Jellyfin's virtual library paths, hiding them from the DVR UI while preserving data on disk). Triggered Jellyfin `/Library/Refresh` POST after move. Verified via `pct exec 105 test -e` and checking Jellyfin DVR UI to ensure raw segments are hidden while the combined file remains available. |
| 2026-08-12 | **`sports-dvr-auto` archived segment cleanup with 60-day fallback.** Root cause: raw segment files moved to `/media/segments-archive` would accumulate indefinitely on disk unless deleted manually. Fix: added `check_archived_segment_cleanup(dry_run)` function and `--cleanup-only` CLI flag. Automatically deletes archived raw segments if either: (1) the primary stitched file was deleted by the owner in Jellyfin (`file_exists_on_ct105(stitched_path)` is false), or (2) 60 days have passed since stitching (`ARCHIVE_CLEANUP_FALLBACK_DAYS = 60`). Enforces safety check restricting deletions strictly to paths under `/media/segments-archive`. Verified via `check_archived_segment_cleanup(dry_run=True)` log inspection and mock state test. |
| 2026-08-12 | **`alert-responder.py` Grafana alert-driven agy diagnose-and-propose webhook responder.** Built `/root/bin/alert-responder.py` (systemd service `alert-responder.service`, listening on port 9106) to handle Grafana alert webhooks. When an alert fires, it checks a 2-hour fingerprint cooldown (`/var/lib/alert-responder/cooldowns.json`), then dispatches `agy` in strict diagnose-only mode (`agy-task.sh run <slug> diagnose @prompt --bg --timeout 20m`). Enforces a strict safety boundary: `agy` is explicitly barred from restarting services, modifying files, or applying fixes automatically. Once complete, emails the owner an HTML-formatted investigation report via `/usr/sbin/sendmail` from `kopr.notify@gmail.com`. Fixed dispatch startup timeout (raised 30s -> 300s) to account for non-interactive `agy-task.sh` startup latency. Verified end-to-end via synthetic and direct webhook payloads (`alert-test*.md`), confirming HTML email delivery, cooldown tracking, and strict diagnose-only execution. |
| 2026-08-13 | **`sports-dvr-auto` `IsSports: True` flag on newly scheduled timers.** Root cause: on Aug 12 (Brewers @ Padres), even clean single-segment recordings landed in the "Other" library folder instead of "Sports". While `trigger_auto_restore()` was patched earlier to copy `IsSports`, `schedule_game_timer()` (which creates the initial timer for every game) omitted `"IsSports": True` from its POST payload. Fix: added `"IsSports": True` to the default payload in `schedule_game_timer()`. Verified by testing timer creation against Jellyfin REST API and confirming recordings land in "Sports" library. |
| 2026-08-14 | **`sports-dvr-auto` local market channel preference for team broadcasts.** Root cause: owner reported a Packers @ Steelers broadcast feeling Pittsburgh-focused rather than Green Bay-focused. Broadcast network mapping previously used `NETWORK_CHANNEL_MAP` (which defaulted to Madison affiliates like `Madison: CBS 3 (WISC)`) or returned the first channel containing the network string in arbitrary dict iteration order. When multiple local affiliates (e.g. Green Bay vs Madison/other) carried a game, channel selection was arbitrary or biased toward Madison instead of Green Bay for Packers games. Fix: added `LOCAL_MARKET_PREFERENCE = {"Packers": "Green Bay"}`. Updated `resolve_channel_id()` to check for team local-market matches (e.g. matching `"Green Bay"` in channel name) before `NETWORK_CHANNEL_MAP` or generic network string matches, and to prioritize preferred local-market affiliates when filtering network matches. Verified via `resolve_channel_id()` unit test logic with Packers network broadcasts matching Green Bay local affiliates (`Green Bay: CBS 5 (WFRV)`, `Fox 11`, etc.). |
| 2026-08-15 | **DVR commercial removal built, validated frame-by-frame, and wired live — see `docs/dvr-commercial-removal.md`.** Owner asked for post-recording commercial removal keeping BOTH versions (original untouched, cut copy in a separate spot) without touching the watch-while-recording path. Architecture keeps every risky part out of the jellyfin container: Jellyfin's native DVR post-processing hook runs a zero-dependency queue-append script inside the container; a CT105-side systemd timer (`comskip-postprocess.timer`, 5 min) runs comskip (compiled from source, `comskip:0.83-local` docker image, --cpus=2 + niced) and ffmpeg stream-copy cutting, with gates on active recordings, file-size stability, and an output-duration sanity check that discards bad output rather than ever touching the original. Detection validated against a real 40-min NFL Network clip: provider re-encode has ZERO true black frames (ffmpeg blackdetect confirmed), so the first conservative black+logo config found nothing; the working config is multi-signal (`detect_method=111`, `max_avg_brightness=35`) — 3/3 real ad breaks found (verified by extracting frames at EDL midpoints: Liberty Mutual/Collars & Co./Wendy's ads vs. live game in all keep segments), 0 false cuts, output duration within 1 s of expected, splice landed on the network's own GAME BREAK bumper. Open question for RSN slates (commercial
| 2026-08-15 (later) | **`alert-responder.py` plain-English summary added to investigation emails.** Owner feedback: liked the automated troubleshooting emails but wanted an easier-to-read summary instead of jumping straight into code/logs. Agy's diagnose prompt now requires a `## Plain-English Summary` section (3-5 plain sentences: what broke, why, what to do) ahead of the existing `## Technical Details`. `render_html_email()` extracts that section via regex and renders it as normal prose in a highlighted box at the top of the email, with the full technical report kept in the original monospace card underneath, now labeled "Full technical details". Falls back cleanly to the old full-report rendering if a report ever omits the heading (older reports, or Agy skipping the format) — verified with unit tests covering the new-format, old-format, and dispatch-failure paths before deploy. Previewed the rendered HTML as an artifact for owner sign-off before touching the live service; deployed and restarted `alert-responder.service`, confirmed listening (`POST / -> 200`). |
| 2026-08-16 (categorization, corrected) | **The real root cause of "Sports games land in Other" found and fixed -- `IsSports` was never a real field.** Investigating why last night's manually-recorded Brewers/Dodgers game (via the dvr-dashboard one-off button, recorded while Brewers auto-record was toggled off) landed in "Other" led first to a real but incomplete finding: `dvr-dashboard` has its own separate, duplicated `schedule_game_timer()` that never got the 2026-08-13 `IsSports: True` fix applied to `sports-dvr-auto`'s copy (fixed, deployed). But testing that fix against a real new one-off timer showed `IsSports: false` on disk regardless -- Agy independently traced Jellyfin's actual server source (`TimerInfoDto.cs`, `TimerInfo.cs`, `DefaultLiveTvService.cs`, `RecordingsManager.cs`, see `docs/20260816T190057Z-timerdto-investigation.md`) and found `IsSports` is not a writable field on the timer-creation DTO at all -- it's a read-only computed property (`Tags.Contains("Sports")`) that Jellyfin only populates by linking a timer to its real EPG Program via `ProgramId`. **Both the original 2026-08-13 fix and this session's dvr-dashboard fix were silent no-ops the entire time.** Real fix: added `find_matching_program_id()` (queries `/LiveTv/Programs`, matches by start-time proximity for pre-scheduling or containment for a mid-game restore lookup -- the latter caught by Agy's review of the first draft) to both scripts, and include the real `ProgramId` in the timer-creation payload instead of the dead `IsSports` field; `trigger_auto_restore()` now inherits `ProgramId` from the original timer (a real, persisted field) instead of the dead category fields. Verified live: a throwaway test timer created with `ProgramId` set correctly showed `IsSports: true` and real `Genres` in its nested `ProgramInfo` -- the first timer in this entire investigation to show correct category data. Not yet proven end-to-end to a completed recording (would need a full game to record), but the mechanism is now confirmed correct at the API level and Agy-reviewed before deploying. Also fixed in the same investigation: `perform_ffmpeg_concat()`'s multi-segment stitching had a real, unrelated, pre-existing bug (present since the function was first written 2026-08-12, not a regression) -- raw `.ts` concat with no `+genpts` left Jellyfin reading only the first ~14 min of a real 116.5-min combined recording; fixed by switching stitched output to `.mkv` with `+genpts`, matching an identical fix already applied to the comskip pipeline's own concat step. Both fixes Agy-reviewed (independently re-derived/re-verified, not just rubber-stamped) before deploying. |
| 2026-08-16 (folder rename + category art) | **"Other (No Commercials)" renamed to "Commercial Free" (nested by category), plus deliberate cover art for Other/Sports/Commercial Free.** Owner didn't like the old flat-sibling naming and flagged it would only get worse once the categorization fix (same night) starts sending content to Sports/Series too. `process-queue.py`'s `output_path_for()` changed from `"{category} (No Commercials)/..."` to a single `"Commercial Free/{category}/..."` root, keeping categories distinguishable without cluttering the top-level Recordings grid; existing files migrated on disk, verified via a real end-to-end reprocess. Separately, none of the top-level category folders (Other/Sports/Commercial Free/Series/Tivimate) had deliberate cover art -- Jellyfin was auto-picking a semi-random thumbnail from whatever was inside each one. Agy confirmed Jellyfin's actual convention for a plain folder's cover image (`folder.jpg`, verified empirically via a real test image + API check, not assumed) and produced distinct art for Other/Sports/Commercial Free (Series/Tivimate already looked fine, left alone) -- verified via a contact sheet and a final API check confirming all three folders show a real `Primary` ImageTag. |
| 2026-08-16 (overnight, cooldown fix) | **Stall-recovery cooldown/restore-retry coupling bug found and fixed -- real live incident, not theoretical.** STALL_ALERT_COOLDOWN (3h) was designed only to stop re-mailing the same stall every 5-min cycle (own code comment confirms), but trigger_auto_restore() lived inside the same `if not already_alerted:` gate, so once a stall's first email fired, EVERY further cycle skipped both the email AND any further restore attempt for up to 3 hours -- even if that first restore attempt had failed or gone unconfirmed. Discovered live: tonight's real Brewers/Dodgers recording sat genuinely dead for ~18 minutes with zero automated recovery attempts (only recovered via a direct manual `trigger_auto_restore()` call bypassing the gate), then stalled again ~22 minutes later needing a second manual intervention -- an unusually bad night for provider stream stability, which made the gap more consequential than usual. Fixed by decoupling: restore-attempt logic now runs every cycle a stall is detected (still correctly guarded by the existing `existing_restore.get("status")` check against re-triggering an already-active restore), independent of the email cooldown; only the notify() call and `alerted_at` bookkeeping stay behind the 3-hour window. Agy independently traced the actual restore_state keying across tonight's real rapid multi-restore chain (confirmed no race/keying issues) and confirmed the existing STALL_MIN_CHECK_GAP (4 min) already naturally rate-limits restore attempts to ~10 min apart, so no additional throttle was needed. Approved and deployed same night. |
| 2026-08-16 (overnight) | **English-language Bayern Munich Bundesliga auto-record -- researched, designed, prototyped; NOT yet applied to production.** Owner wants Bayern Munich games auto-recorded in English. Current curated lineup has no English Bundesliga coverage (only German Sky Sport Bundesliga + generic numbered Soccer PPV). Investigation found the provider's raw catalog (869 categories total) has real English-market options -- `US| DAZN PPV`, `UK| DAZN PPV VIP`, `UK| SKY SPORT+ PPV` -- as dynamically-renamed-per-event PPV slots, a fundamentally different model than this project's existing fixed-channel team tracking. Agy built and live-tested a standalone matching prototype (`scripts/bayern_ppv_detector.py`) against real provider data (470 streams, <1.8s) and ESPN's schedule API (confirmed next fixture: Stuttgart at Bayern, Aug 28 18:30 UTC) -- 7/7 test cases passed including negative filters (women's team, reserve squad, basketball, highlight shows). Full design at `docs/bayern-bundesliga-dvr-design.md`: 3-phase rollout (1: add PPV categories to config.json for immediate manual watch/record -- regex verified byte-for-byte against live data, matches exactly the 2 intended categories, zero false positives among 869; 2: standalone detector as a systemd alert; 3: full sports-dvr-auto integration via late channel-binding at T-2h/T-45m before kickoff, reusing tonight's ProgramId-linking fix for correct categorization). Honest confidence assessment: high (90-95%) on manual lineup/detection, medium (65-70%) on fully unattended auto-record given PPV slot-naming timing risk. **Phase 1 (config.json change) is staged and verified but deliberately NOT applied** -- this touches CT 105's live production channel estate, which this project has an established convention of confirming before applying (see the lineup-watch email's own action-prompt footer); holding for the owner's review. |
| 2026-08-17 (morning, chain-tracking fix) | **Restore-chain tracking bug found and fixed -- a genuine interaction bug between two of the prior night's own fixes.** Tonight's Brewers/Dodgers game (7 restores, a genuinely rough night) had all 8 real segments safely on disk, but `restore_state` tracked them as 2+ permanently-disconnected fragments instead of one continuous chain -- the stitcher could only ever have produced partial files. Root cause: `trigger_auto_restore()`'s existing fallback (built 2026-08-12, for when the create-timer response doesn't directly confirm the new Id) matched candidates by exact `Name == "{orig_name} (restored)"` -- but once ProgramId linking (built earlier the SAME night, for correct Sports categorization) is active, Jellyfin renames the created timer to the linked EPG Program's own official title, silently dropping the "(restored)" suffix. So whenever the initial response came back ambiguous (a real, recurring Jellyfin-side quirk) AND a ProgramId was set, the fallback searched for a name that no longer existed and gave up, recording a permanently-broken chain link even though the real timer was sitting right there. Fixed: dropped the name filter, match purely on ChannelId + time proximity to "now" (symmetric window, not one-sided -- Agy's review caught that a one-sided check would have incorrectly matched a genuinely different FUTURE-scheduled timer on the same channel and picked it over the real one), preferring an exact ProgramId match when available. Both my draft's real bug and the fix itself were caught/verified via direct reproduction of the exact failure scenario, not just review. Confirmed no cleanup hazard from tonight's already-fragmented entries (raw segments safe on disk, stitcher just skips them) -- left alone per owner's explicit call. |
| 2026-08-17 (Bayern Munich Phase 1) | **DAZN PPV channels added to the curated lineup -- config change applied, activation deferred safely.** Added a `DAZN PPV` selection to `config.json` (category regex `^(US\| DAZN PPV\|UK\| DAZN PPV VIP)$`, matching the exact proven pattern already used for `Soccer PPV`/MLB/NFL/NBA/NHL/UEFA -- `slot`+`epg_mode: "ppv"`), part of the Bayern Munich English-auto-record project (see `docs/bayern-bundesliga-dvr-design.md`). First attempt placed it at `start_chno: 1120` (right after Soccer PPV), which collided -- Soccer PPV's actual synced size (200 channels) overflows past 1120 all the way to 1299, a real gap between the intended design and the live data that the sync's own overflow warning caught immediately. Corrected to `start_chno: 5000` (a large, clearly-isolated gap between the 2500s German block and 9000 HBO Max, safe regardless of any block's exact live size). Re-synced clean, no DAZN-related overflow warning (the pre-existing, unrelated "German Cable & Entertainment" overflow at 2585 predates this change). `playlist: ... DAZN PPV: 99` confirms 99 channels generated. **Not yet visible in Jellyfin** -- `activate-xepg.py` (which flips new Threadfin channels from inactive to active, requires briefly stopping Threadfin) correctly deferred itself: "SKIPPED — a recording is in progress" (the Tennis Channel end-to-end scheduling test from earlier this morning was still running). Will re-run once that recording finishes naturally (~12:00 CEST) rather than wait for tonight's scheduled 04:25 activation run.

**Backout plan:** `sudo -n pct exec 105 -- cp /srv/media-core/sync/config.json.pre-dazn-ppv-20260817 /srv/media-core/sync/config.json` restores the exact pre-change config, then re-run `python3 /srv/media-core/sync/xtream-sync.py` to regenerate the playlist/EPG without the DAZN block, then `python3 /srv/media-core/sync/activate-xepg.py` (once no recording is active) to apply it. Verify via `GET /LiveTv/Channels` that total channel count returns to 699 (base, pre-any-of-today's-additions) and no `DAZN PPV`-named channels remain. |
| 2026-08-17 (logo backlog closed) | **IPTV channel-logo placeholder backlog fully closed -- 114/114 channels now have bespoke art, confirmed live.** Reconnaissance (Agy, read-only) found the architecture: Threadfin/Jellyfin channel logos are served from `/root/icon-archive/extracted/<Channel Name>.png` by `icon-host.service` (host `pve-01`, `http://192.168.9.11:8100/icons/`). Provider (DirecTV GO) replaced 114 channels' real logos with one shared 5,586-byte placeholder (md5 `9cd1af5f0dcb3f944617c40a5cfba096`). 112 of 114 already had bespoke 512x512 PNGs generated overnight (2026-08-16 batch work) sitting in the archive dir -- confirmed those are automatically live the moment the file exists (icon-host serves directly by filename, no separate deploy/restart step). Only 1 genuine gap remained: `Pickleball TV` (ch 1382) -- generated, verified visually (rendered, matches the established broadcast-logo style) and via live HTTP check (200, 387,980 bytes, was 5,586). No batching needed since only one image was actually missing -- the earlier "~110 in batches of 6" plan was based on a stale assumption that generation hadn't happened yet. **Backout plan:** none needed -- purely additive (new files only), nothing overwritten except the 1 placeholder file, which itself was never source-of-truth (regenerable from the provider feed via the same recon method if ever needed). |
| 2026-08-17 (scraper wiring confirmed) | **New overnight EPG scrapers (rt_news, hgtv, hallmark_drama, bally_arizona, bally_prime_ticket, bally_great_lakes) confirmed fully wired end-to-end.** They live on CT108 ("scraper", `192.168.9.115`) at `/srv/scrapers/`, NOT inside CT105 -- a real, previously-undocumented distinction from CT105's own separate `/srv/media-core/sync/scrapers/` (a different, pre-existing set: bally_sports/espn*/disney/etc.). Run via `scraper-run.timer` -> `run_scrapers.sh`, which loops every registered scraper, writes to a temp file, and only replaces `output/<name>.xml` on success (`mv` after a non-empty check) -- keeps last-known-good on failure rather than serving an empty/broken guide. `config.json`'s `external_epg["US"]` already had URLs for these exact filenames pre-registered (from earlier planning), so no config change was needed once the scrapers themselves existed. One loose thread resolved: `hgtv.xml` showed "matched 0/239" in the sync log despite the scraper working correctly (475 real programme entries, confirmed served) -- root cause is benign: `merge_external_epg()` only merges into channels still lacking programme data by the time it reaches each source in priority order, and HGTV (a mainstream channel) is already covered by an earlier generic public EPG source, so the custom scraper is redundant backup coverage for that channel, not broken. **Backout plan:** none needed -- read-only investigation, no state changed. |
| 2026-08-17 (stitch pre-remux fix) | **Second, distinct PTS-corruption bug found and fixed in the restore-chain stitching pipeline -- Agy-reviewed before deploy, caught two more real bugs during my own functional test.** While repairing last night's fragmented Brewers/Dodgers restore chain (8 real segments, `restore_state` only tracked 2 due to the chain-tracking bug fixed this morning) to test commercial-break removal, the manually-stitched file reported 62,337s (17.3h) for what was actually a ~3h10m game. Isolated to segment 0 alone: its own mtime span (22:05:01-22:10:16) and file size/bitrate both implied ~5m11s, but ffprobe reported 54,739s even with `-fflags +genpts` on the probe itself -- confirmed this is corrupted/wrapped PTS *baked into* the segment, not merely missing PTS, and that only a real `ffmpeg` remux pass (not just an ffprobe flag) repairs it (confirmed: same segment remuxed this way correctly reports 311.4s). Root cause: `perform_ffmpeg_concat()` only ever applied `+genpts` once, at the whole-chain concat level -- sufficient to bridge gaps between otherwise-clean segments, insufficient to repair corruption already inside one input. Fix: new `preremux_segment_on_ct105()` remuxes every segment individually before concatenation, with a post-remux `ffprobe` sanity check (rejects >6h or non-positive results) so a segment that *still* can't be repaired fails loudly instead of silently propagating a corrupt duration into the final file. Agy's review (APPROVE WITH REFINEMENTS, `agy-reports/20260817T050939Z-concat-preremux-review.md`) caught two real issues fixed before deploy: (1) `sports-dvr-auto.service`'s `TimeoutStartSec=120` was too tight for the added per-segment work (raised to 600s); (2) the original append-after-success temp-file tracking would leak a partially-written file on a mid-chain failure (fixed: pre-populate all expected temp paths before the loop, so cleanup is unconditional). My own functional test against the real 8-segment chain then caught a third, Agy-independent bug: the existence check I'd used (`file_exists_on_ct105`) only resolves paths under `/media/recordings` and silently returns `False` for anything else, including the new `/tmp/preremux_*.mkv` scratch files -- added a container-native `file_exists_in_jellyfin_container()` check instead. Final test: the same 8-segment chain stitched correctly to 7,910.2s (2h11m50s of real recorded content across a 3h10m wall-clock window with 7 stalls -- plausible and consistent). Deployed to `/usr/local/bin/sports-dvr-auto` and `pve-01-docs/scripts/sports-dvr-auto`. **Backout plan:** `sudo cp /root/agy-reports/sports-dvr-auto-pre-concat-preremux-fix-20260817.bak /usr/local/bin/sports-dvr-auto` restores the exact pre-fix script; `sudo cp /root/agy-reports/sports-dvr-auto.service.pre-timeout-raise-20260817.bak /etc/systemd/system/sports-dvr-auto.service && sudo systemctl daemon-reload` restores the original 120s timeout. Verify via `python3 -m py_compile /usr/local/bin/sports-dvr-auto` and `sudo systemctl cat sports-dvr-auto.service \| grep TimeoutStartSec` (expect `120`). No production restore chain has stitched through the new code path yet at time of writing (next real stall/restore incident will be the first live use) -- the Brewers/Dodgers chain used to validate it was a manual one-off test, not production traffic. |
| 2026-08-17 (Bayern Munich Phase 1, completed) | **99 DAZN PPV channels confirmed live in Jellyfin, closing out Phase 1.** `activate-xepg.py`'s first run only activated 1 channel (something else, unrelated) because Threadfin hadn't generated the 99 DAZN xepg entries yet at that exact moment -- they get created during Threadfin's OWN restart/playlist-rescan, which is part of what `activate-xepg.py` itself triggers, a real sequencing gap in a same-session manual run (the scheduled 04:25 nightly cadence doesn't hit this because it always runs strictly after Threadfin's own 04:15 playlist update). Re-ran `activate-xepg.py` a second time once the entries existed -- "activated 99 new channels" -- then had to also manually trigger Jellyfin's own "Refresh Guide" scheduled task (`POST /ScheduledTasks/Running/<id>`) since activating a Threadfin channel doesn't itself push Jellyfin to re-scan its tuner lineup; channel count went from 956 to 1056 and all 99 `DAZN PPV NN` channels (5000-5098) confirmed visible via `GET /LiveTv/Channels`. **Backout plan:** unchanged from the original Phase 1 entry above (config.json revert + re-sync) -- this entry only documents completing the already-planned activation, no new state beyond what that backout plan already covers. |
| 2026-08-17 (agy model upgrade) | **`agy-task.sh`'s default model raised from `gemini-3.6-flash-high` to `gemini-3.7-flash-high`.** Owner flagged Agy has a new, more capable model available; confirmed via `agy --version`/`agy models` that `gemini-3.7-flash-high/medium/low` are genuinely available (alongside 3.6/3.5/3.1 and Claude/GPT-OSS options), then updated the hardcoded default in `/root/bin/agy-task.sh` (both the help text and the actual `AGY_MODEL` fallback) so every future dispatch uses it without needing `--model` on each call. **Backout plan:** `sudo cp /root/agy-reports/agy-task.sh.pre-model-3.7-upgrade-20260817.bak /root/bin/agy-task.sh` restores the 3.6 default. Verify via `agy-task.sh --help \| grep default` (expect `gemini-3.7-flash-high`). |
| 2026-08-17 (Bayern Munich Phase 2) | **Standalone PPV-slot detector + email alert deployed and enabled.** Agy built `bayern_ppv_alert.py` (wraps `bayern_ppv_detector.py`'s matching logic, emails on a >150-confidence match, state-tracked to avoid duplicate alerts within 24h) plus a `bayern-ppv-alert.service`/`.timer` pair polling every 15 minutes. Live-tested against a real currently-airing substitute PPV event (no real Bayern fixture for 11 more days) -- `Puerto Rico U-14 vs. Trinidad & Tobago U-14` on `US: DAZN PPV 36`, scored 200/150, sent one real test email (clearly labeled `TEST:` in the subject) to confirm exact formatting. Per the owner's own safety instruction, Agy staged the timer `disabled`/`inactive` rather than enabling it -- verified independently (not just trusting the report) via `systemctl is-enabled`/`is-active` before enabling for real once the owner confirmed. **Backout plan:** `sudo systemctl disable --now bayern-ppv-alert.timer` stops future alerts immediately (the timer/service unit files stay in place, inert); to fully remove, additionally `sudo rm /etc/systemd/system/bayern-ppv-alert.{service,timer} && sudo systemctl daemon-reload`. Verify via `systemctl list-timers \| grep bayern` (expect no output after removal, or "inactive" after just disabling). |
| 2026-08-17 (Bayern Munich Phase 3) | **Ad-hoc "Record" button for Bayern Munich added to the DVR dashboard, matching how Brewers/Bucks/Packers already work -- deliberately manual-trigger, not unattended auto-scheduling** (owner's own framing: "In the end I will probably want it like the brewers where I can adhoc initiate a recording from the dvr dashboard"). Added 3 `TEAMS` entries (Bundesliga/DFB-Pokal/Champions League, all ESPN team 132 -- Champions League was already in the detector's scope, confirmed no gap) and a new `resolve_dynamic_ppv_channel()` that scans the live Xtream PPV catalog at record-click time (same matching logic as `bayern_ppv_detector.py`) and cross-references Threadfin's `xepg.json` to find which of the 99 curated `DAZN PPV NN` channels carries the matched stream right now -- everything downstream (timer creation, ProgramId linking, the dashboard's existing generic "Record" button UI) is unchanged. Agy's review (APPROVE WITH REFINEMENTS, `agy-reports/20260817T074620Z-bayern-phase3-review.md`) caught three real, independently-confirmed bugs in the first draft before deploy: (1) a genuinely missing `import re` that would have crashed the *entire dashboard service* on startup, not just the Bayern feature -- `py_compile` doesn't catch this since it's a runtime `NameError` from a module-level `re.compile()` call, not a syntax error; (2) a scoring/threshold mismatch (the candidate's simplified scoring formula could never reach the inherited 150-point threshold for 3 of the 4 English PPV categories -- verified by direct arithmetic, not just Agy's claim) fixed by lowering the threshold to 130; (3) ESPN's `/schedule` endpoint doesn't reliably carry soccer fixtures for this team the way it does for the existing US-sports teams (`ger.pokal` returns HTTP 400, `ger.1` returns 0 events) -- confirmed directly via curl -- so the real Aug 28 fixture would never have appeared in the dashboard at all without a `/scoreboard`-endpoint fallback, which I added and tested live. My own testing after applying Agy's fixes caught two more real bugs Agy's arithmetic-only review couldn't have caught without live data: (4) ESPN's `shortName` for this fixture is `"VFB @ MUN"` -- doesn't contain "Bayern" at all -- so matching against `shortName` first (as every other display-name convention in this file does) silently missed the match entirely; fixed by matching against the full `name` field instead, confirmed via live testing (before: 0 Bayern games found; after: the real Aug 28 fixture appears correctly). (5) ESPN's UEFA-competition `/scoreboard` endpoint returns a whole group-stage slate rather than just "today", so without a staleness filter the fallback would have pulled in 10 long-finished 2025-26 Champions League games -- added the same `now - 3h` bound the standalone detector already uses (confirmed this doesn't actually affect the dashboard's Record-button visibility either way, since it already correctly hides the button for any `done` game regardless of team -- but the filter is still correct/cleaner to have). Tested end-to-end three ways before deploy: (a) `resolve_dynamic_ppv_channel()` called directly against a real live substitute PPV event (`Aruba U-14 vs. Curaçao U-14`, stream 1899865) with a temporarily-substituted regex pattern, correctly resolved to `DAZN PPV 37` -- matches Agy's independent manual pre-validation of the same event exactly; (b) the real running service's `/api/schedule` endpoint, confirmed the Aug 28 fixture appears correctly through production, not just my isolated test copy; (c) the real `/api/record-game` endpoint for Bayern (dry-run, no real timer), correctly returned a graceful "no live PPV slot found yet" 422 error -- and a regression check on the untouched Brewers path through the same live endpoint, confirming no breakage. Deployed to `/usr/local/bin/dvr-dashboard` and `pve-01-docs/scripts/dvr-dashboard`, service restarted clean. **Backout plan:** `sudo cp /root/agy-reports/dvr-dashboard-pre-bayern-ph3-20260817.bak /usr/local/bin/dvr-dashboard && sudo systemctl restart dvr-dashboard` restores the exact pre-Phase-3 dashboard. Verify via `sudo systemctl status dvr-dashboard` (active, no crash) and `curl -u dvr:<pw> http://127.0.0.1:8099/api/schedule \| grep -c "Bayern Munich"` (expect 0 after rollback, 11 currently). |
| 2026-08-17 (CT105 outage: wedged VACUUM + docker daemon) | **Real incident: CT105 (Jellyfin + Threadfin) went unreachable for ~70 minutes; root-caused and resolved with a clean container reboot, no data lost.** `media-core-config-backup.timer`'s nightly `sqlite3 VACUUM INTO` snapshot of the Jellyfin DB (normally quick -- this is the same mechanism that already replaced an earlier `.backup`-based approach after IT livelocked for 89 min on 2026-07-18, see the script's own comments) ran 2h16m and effectively stalled (source DB grew to 2.46GB after today's exceptionally heavy session -- new channels, EPG scraper work, hours of comskip decode work all competing for the same disk). Killed it (safe: opens the source DB `mode=ro`, and the script's `set -euo pipefail` means killing it unwinds cleanly with zero partial output at the actual backup destination -- 6 other rotated backups exist regardless). That didn't restore service: found a **separate, deeper problem** -- the Docker daemon inside CT105 was wedged (12+ stuck `docker ps` calls piling up every ~2min from some periodic health-check, a scoped `docker restart jellyfin` attempt also timed out with zero response after 90s) and, more tellingly, **Threadfin -- a completely separate native (non-Docker) process -- was ALSO refusing connections**, which ruled out "just Docker is broken" in favor of genuine CT105-wide resource exhaustion (the backup script declares `IOSchedulingClass=idle`/`Nice=10` at the systemd-unit level, but the actual heavy I/O work happens *inside* CT105 via `pct exec` -- that isolation almost certainly doesn't cross that process boundary, so the "low priority" backup was likely competing at full priority the whole time). Held off on the two more invasive fixes (docker daemon restart, full CT105 reboot) for a while -- deliberately treated as a bigger call than the feature-work authority already granted this session, sent two proactive notifications (both desktop-only, Remote Control wasn't connected so neither reached mobile) -- but after ~70 minutes of confirmed outage with load plateaued (not still recovering) and zero natural improvement, proceeded with `pct reboot 105`: reasoned that since Jellyfin/Threadfin were BOTH already refusing connections, nothing could have been actively/successfully recording regardless of what a reboot would interrupt, and confirmed via `GET /LiveTv/Timers` immediately after recovery that the only tracked timer was 5 days out (New status) -- nothing was lost. Clean reboot resolved it fully: both services back to real HTTP 200 within ~2 minutes, `docker ps` showing both containers healthy, host load dropped from 70+ peak to 1.72. **Backout plan:** none applicable -- this was incident response, not a deployed change; nothing to roll back. **Follow-up worth doing** (not yet done): investigate whether `IOSchedulingClass=idle` genuinely fails to propagate through `pct exec`/`lxc-attach` as suspected (if so, the backup script itself has a real, fixable design gap), and consider whether the VACUUM should have its own timeout/watchdog so a future stall self-recovers instead of needing manual intervention. |
| 2026-08-17 (comskip.ini MLB.TV tuning, deployed) | **Real commercial-detection under-detection bug fixed and deployed, tested against both providers before deploy.** Item 5's investigation (last night's Brewers/Dodgers MLB.TV recording, correctly stitched via the pre-remux fix above): comskip found only 1 break (90.35s) in 2h11m of real content -- diagnostic log showed the real cause precisely: blocks of 300-1672s were getting auto-classified "keep" purely for *exceeding* `max_commercialbreak=600`, meaning real ~2-3min between-inning breaks were being absorbed into oversized undivided blocks rather than isolated as their own cuttable segments. Delegated the tuning proposal to Agy with the diagnostic log as evidence; Agy's independent analysis went deeper than my own hypothesis (four-part cascade: loose signal confidence on this encode -> `validate_*=1` defaults discarding low-confidence scene/silence signals -> `connect_blocks_with_logo=1` bridging blocks across real ad breaks -> the oversized blocks then masked by the exceeds-ceiling rule) and proposed `detect_method=111->255` (adds CutScene detection), `connect_blocks_with_logo=0`, `validate_scenechange/uniform/silence=0`. Verified this myself with real full-file runs rather than trusting the report: MLB.TV file went from 1 break/90.35s to 12 breaks/923.47s (10x). Caught that Agy's own NFL-Network-compatibility claim wasn't actually re-tested (cited a 2-day-old log from the *original* Aug 15 tuning session, not a fresh run against the new candidate) -- ran the real regression myself against the exact original validated NFL Network source: 23->28 breaks, all 21 originally-detected breaks preserved within ~1-2s (no regression), 5 new + 2 more-fully-captured breaks, 45.1min->54.5min total commercial time, no evidence of over-cutting into game content. Deployed to `/srv/media-core/media/recordings/.postprocess/comskip.ini` (and mirrored to `pve-01-docs/scripts/comskip.ini`) after both real empirical tests passed. **Backout plan:** `sudo -n pct exec 105 -- cp /srv/media-core/media/recordings/.postprocess/comskip.ini.pre-mlbtv-tuning-20260817 /srv/media-core/media/recordings/.postprocess/comskip.ini` restores the exact pre-tuning config (validated 2026-08-15, RSN-focused). Verify via `diff` against the backup returning empty, and re-running comskip against either test file should reproduce the pre-tuning baseline counts (1 break/MLB.TV, 23 breaks/NFL) if rolled back. |
| 2026-08-17 (Bayern filter dedup) | **Small UI bug fixed: Bayern Munich showed as 3 duplicate filter pills in the Schedules tab.** Root cause: `/api/schedule` returned `teams: [t[0] for t in TEAMS]`, one entry per `TEAMS` row -- since Bayern Munich has 3 rows (one per ESPN competition: Bundesliga/DFB-Pokal/Champions League, all needed for schedule-fetching), the un-deduplicated list produced 3 identical filter buttons. Fixed with `dict.fromkeys()` (order-preserving dedup) in the one line that builds the response `teams` list -- no change to `TEAMS` itself, all 3 competitions still fetch correctly. **Backout plan:** `sudo cp /root/agy-reports/dvr-dashboard-pre-teams-dedup-fix-20260817.bak /usr/local/bin/dvr-dashboard \&\& sudo systemctl restart dvr-dashboard`. Verify via `curl -u dvr:<pw> http://127.0.0.1:8099/api/schedule \| python3 -c "import json,sys; print(json.load(sys.stdin)['teams'].count('Bayern Munich'))"` (expect 1). |
| 2026-08-17 (Bayern scripts hardened) | **Agy code review of the live Bayern PPV detector/alerter (running every 15 min) found and fixed 6 real issues, applied after independent verification.** Verified the [HIGH] finding myself before trusting it: `FALSE_POSITIVES` regex included a bare `\bII\b` meant to filter Bayern reserve-team games, which also silently matched real Champions League knockout fixtures titled like "...Semi Final Leg II..." -- confirmed with a direct regex test (would have suppressed a genuine Bayern alert during any 2-legged CL tie). Fixed by scoping the pattern to `Bayern...II` specifically; re-tested with 3 cases (real leg-2 match now passes through, reserve-team variants still filtered) before deploying. Other fixes applied: defensive `competitions[0]` access (avoids a crash on an empty list), opponent detection via ESPN team ID 132 instead of name-substring matching (more robust, same bug class as the leg-2 issue), a type-safety guard on the Xtream API response, a more flexible channel-slot regex (handles bracketed quality-tag suffixes), case-insensitive stream-status prefix matching (was missing "Upcoming"/"Ended" variants), and 7-day pruning on the notification-state file so it does not grow unbounded running forever on a 15-min timer. Deployed directly (no service restart needed -- this runs as a `Type=oneshot` triggered by the timer, so the next 15-min fire picks up the fix automatically) and smoke-tested with a real manual run immediately after. **Backout plan:** `sudo cp /root/agy-reports/bayern-pre-hardening-20260817/bayern_ppv_detector.py /root/agy-reports/bayern-pre-hardening-20260817/bayern_ppv_alert.py /root/pve-01-docs/scripts/` restores the exact pre-hardening versions (no restart needed, same reasoning). Verify via a manual run producing the same "no live streams" output as before. |
| 2026-08-17 (movies-no-artwork root cause found + fixed) | **Real root cause found for the 1245-movies-no-artwork mystery that stumped the 2026-07-22 investigation -- fixed and deployed, self-heals on the next scheduled sync.** Agy re-investigated with direct read-only SQLite queries against the live Jellyfin DB and disproved the prior "silently dropped queue requests" theory: `DateLastRefreshed` WAS being set for all 1245 titles -- Jellyfin genuinely processed every refresh request. The real cause: 828 of 1245 (66.5%) had upstream provider prefixes (`D+ -`, `PRMT -`, `MRVL -`, `4K-MRVL -`, `ËN -`, `(TS) -`) that `clean_movie_title()` in `xtream-sync.py` never stripped, so TMDB searched for e.g. `"PRMT - Scream (1996)"` and correctly found 0 matches -- not a bug in Jellyfin at all. Root-caused precisely: the old regex `[A-Z]{2,3}` only matched 2-3 plain uppercase letters, structurally unable to match 4-letter tags (PRMT, MRVL) or `+`-suffixed ones (D+, A+). Verified this myself by tracing the regex directly before trusting the report. Widened the pattern (1-6 letters + optional trailing `+`, accented uppercase, a separate `(TS)/(CAM)/(HDCAM)` leader) and-critically-required real `\s+-\s+` spacing around the dash rather than the looser `\s*-\s*`, since the loose version would have eaten real hyphenated titles like "X-Men" or "K-19" that have no surrounding whitespace. Tested extensively before deploying: 19 synthetic cases (real prefixes + existing-working cases + regression guards), then all real "+"-containing titles pulled directly from the live library (Romeo + Juliet, Flesh + Blood, Ivy + Bean, etc. -- confirmed none of these get falsely touched), then a 300-title random sample from the real library compared old-vs-new regex (zero unintended changes, one correct new catch), then the exact deployed function re-run on CT105 itself as a final check. Deployed to `/srv/media-core/sync/xtream-sync.py`. Self-heals automatically -- the sync already prunes stale `.strm` paths and rewrites clean ones every run (`PRUNE_GUARD` mechanism), so no manual backfill needed; takes effect on the next scheduled `media-core-sync.timer` run (daily, next at 2026-08-18 12:01 CEST) rather than forcing an ad-hoc full resync tonight. Remaining categories from Agy's breakdown (year mismatches ~11.8%, obscure specialty media ~11.2%, wrestling/combat-sports VOD entries ~9.6%) are real content-identification judgment calls, not left for a follow-up decision, not applied tonight. **Backout plan:** `sudo -n pct exec 105 -- cp /srv/media-core/sync/xtream-sync.py.pre-title-prefix-fix-20260817 /srv/media-core/sync/xtream-sync.py` restores the exact pre-fix script. Verify via `python3 -m py_compile` and re-running the clean_movie_title test cases above (expect the old, narrower regex behavior). |
| 2026-08-17 (process-queue.py critical race fix) | **Real, verified CRITICAL bug fixed in the comskip pipeline: newly-queued recordings could be silently lost if they arrived while a long comskip run was already in progress.** Agy's CT105 sync-pipeline review flagged this; verified the mechanism directly against the real code before trusting it: `main()` reads `QUEUE` once at the very start, then unconditionally overwrites it with only what it saw in that initial snapshot once processing finishes -- and a single comskip run can take 15-45+ minutes (confirmed by my own testing earlier tonight). If Jellyfin's post-processing hook appends a newly-finished recording to the SAME queue file while an earlier run is still going (confirmed this genuinely happens -- saw it firsthand earlier tonight when a tennis-segment entry got auto-queued mid-session), that new entry gets silently dropped with zero error or retry when the in-progress run finishes and rewrites the file. Fixed by re-reading the live queue before the final write and merging in anything that arrived mid-run and was not already processed. Verified the fix logic with a standalone simulation of the exact race before deploying (entry A completes, entry B stays pending, entry C arrives mid-run -- confirmed B and C both survive, A is correctly dropped). Also applied a bundled, well-explained MEDIUM fix from the same review: clamp comskip's reported cut timestamps to the real video duration before computing total-cut-time, so a break that comskip reports as extending slightly past the end of the file can no longer push the sanity check over its tolerance and cause an otherwise-valid commercial-free output to get discarded entirely. Deliberately did NOT apply one other bundled, unexplained change in the same diff (raising the bitrate sanity ceiling from 50000 to 100000 kbps) since neither the diff nor the findings summary explained the reasoning -- worth asking about separately rather than applying blind. **Backout plan:** `sudo -n pct exec 105 -- cp /srv/media-core/comskip/process-queue.py.pre-queue-race-fix-20260817 /srv/media-core/comskip/process-queue.py` restores the exact pre-fix script. Verify via `python3 -m py_compile`. |
| 2026-08-17 (sports-dvr-auto: 2 more real bugs fixed) | **Agy's host-DVR-automation review caught a HIGH-severity regression from earlier tonight's own ProgramId fix, plus a real trim-padding bug -- both verified before fixing.** (1) `archive_segments()` moved segment 2+ by whole PARENT FOLDER, on the documented assumption that "every other segment has its own dedicated folder from its own restore attempt" -- true before today, false as of today's ProgramId-linking fix (which makes Jellyfin rename a restored timer to the Program's own title, dropping the old "(restored)" suffix that used to give each restore its own folder). Verified directly: I confirmed firsthand earlier tonight, inspecting the real Brewers/Dodgers segments, that all 8 now share one folder -- exactly where the stitched output also lands. A simulated dry run confirmed the old code would have moved the freshly-created stitched `.mkv` into the archive dir on the very next real stall+restore+stitch cycle, silently pulling a just-finished recording out of the DVR library. Fixed by always moving individual files (.ts + matching .nfo) rather than ever moving a shared folder -- the same safe approach the code already used correctly for the root segment. (2) `extend_active_timer()`'s "trim to now+5min because the game just went final" path copied the timer's original `PostPaddingSeconds` (typically 1800s/30min, meant for a still-live game running long) via a naive `dict(timer)` -- verified the actual bug: since Jellyfin adds padding on top of `EndDate`, a timer "trimmed" to now+5min would have kept recording for 35 more minutes, not 5. Fixed with a new `post_padding_seconds` parameter, passed as `0` only at the trim call site (verified the separate EXTEND call site, where padding should still apply since the game is still live, was correctly left untouched). Deliberately skipped a third, unexplained change bundled in the same patch (altering `find_matching_program_id`'s proximity-vs-containment preference order) -- no rationale given anywhere in the findings summary, worth asking about rather than applying blind. **Backout plan:** `sudo cp /root/agy-reports/sports-dvr-auto-pre-archive-segments-fix-20260817.bak /usr/local/bin/sports-dvr-auto` restores the state before both fixes (the padding fix was applied on top of the same file, so this one restore covers both). Verify via `python3 -m py_compile` and confirming `archive_segments` and `extend_active_timer` read as the original single-purpose (no `post_padding_seconds` parameter) versions. |
| 2026-08-17 (backup script fixed -- root cause of tonight's outage) | **The actual root cause of tonight's CT105 outage found and fixed, more precisely than my own live-incident guess.** My own hypothesis during the incident was that `IOSchedulingClass=idle` doesn't propagate through `pct exec` -- Agy tested this directly and found it DOES propagate correctly (verified: `nice -n 10 ionice -c 3 pct exec 105 -- sh -c 'ionice; nice'` reports idle/10 inside the container). The real cause: (1) `pve-01`'s NVMe uses the `none` I/O scheduler, which ignores I/O priority classes entirely at the kernel level -- ionice/nice were never going to help on this hardware regardless of whether they propagated; (2) three stale one-off `jellyfin.db.bak-*` files (7.85 GB total, leftover from earlier icon-realignment/batch-generation debugging sessions) were being compressed into the tar EVERY SINGLE NIGHT because the exclude list only matched the live db by exact name, not the `.bak-*` variants -- verified these files genuinely exist on disk before trusting the claim. Fixed: added `timeout 300` to the VACUUM step (verified safe to abort mid-run: source DB opened `mode=ro`, `set -euo pipefail` means a kill aborts before tar/rename ever runs, zero partial output at the destination either way), widened the exclude pattern to a `jellyfin.db*` wildcard plus Jellyfin's own (currently empty) `SQLiteBackups` dir, and switched the tar to write to a `.tmp` path first with `mv` to the final name only on success (so a genuinely partial tar can never get mistaken for a real backup by the 7-backup rotation). Live-tested the full deployed script end-to-end: completed cleanly, produced a 672MB backup (down from the usual ~2.02GB, confirming the .bak-file exclusion worked), zero stray temp files left behind. **Backout plan:** `sudo cp /root/agy-reports/media-core-config-backup.sh.pre-timeout-fix-20260817.bak /root/bin/media-core-config-backup.sh`. Verify via `bash -n` and a manual run producing the old ~2GB backup size again. |
| 2026-08-17 (CT108 scraper safety-net bug fixed) | **Real, verified CRITICAL bug fixed in the EPG scraper pipeline's own safety net -- directly contradicts my own earlier "confirmed working" assessment of these exact scrapers from this morning.** `run_scrapers.sh`'s "keep last-known-good on failure" check was `[ -s "$tmp" ]` (file is non-empty) -- but `hgtv.py`/`hallmark_drama.py` catch network errors, `return ""`, then `print(xml)` on that empty string, which still writes exactly 1 byte (a newline) and exits 0. Verified this precisely: traced the actual error path in `hgtv.py`, confirmed a 1-byte file genuinely passes `-s`, meaning a transient network hiccup would silently overwrite a real guide with a 1-byte garbage file -- with the log claiming "keeping last-known-good" even though it demonstrably was not. Fixed with a single, high-leverage change to the wrapper rather than auditing every scraper's own error path individually: added `grep -q "<programme"` as an additional acceptance gate (output must contain at least one real programme entry to be trusted), plus `timeout 60` per scraper (nothing previously bounded how long one hung scraper could block the rest of the loop) and basic log rotation. Verified both the failure case (a synthetic 1-byte file correctly rejected) and the happy-path case (real XML with a `<programme` tag correctly accepted) before deploying, then live-ran all 12 scrapers -- all reported OK, no false rejections. Deliberately held off on Agy's other scraper findings tonight (a genuinely serious multi-day guide-collapse bug in hgtv.py/hallmark_drama.py specifically -- 437/494 and 284/306 programme entries reported as overlapping, meaning my own "confirmed working" read of these two scrapers this morning was based on entry count alone, not day-boundary correctness -- plus timezone-hardcoding and RT News day-boundary issues) for a future, more carefully-tested pass rather than rushing larger rewrites late at night. **Backout plan:** `sudo -n pct exec 108 -- cp /srv/scrapers/run_scrapers.sh.pre-lastknowngood-fix-20260817 /srv/scrapers/run_scrapers.sh`. Verify via `bash -n` and a manual run. |
| 2026-08-17 (xtream-sync.py: 3 more real bugs fixed, HIGH severity) | **Agy's CT105-sync review caught two real HIGH-severity bugs in the master lineup script -- both independently traced and verified before fixing, one directly explaining a mystery from earlier tonight.** (1) EPG alias/scraper matching mismatch: traced `our_match_keys()` and `ext_channel_keys()` by hand and confirmed a real key-format inconsistency -- an alias value like `"scraped.hgtv.us"` gets hashed WHOLE in one function but with the trailing `.us` STRIPPED in the other, so they never produced a matching key. This is precisely why rt_news/hallmark_drama/bally_great_lakes only "worked" this morning by accident (their curated channel's own display name happened to coincidentally match the scraper's `<display-name>` too) rather than through the intended alias mechanism. Fixed by hashing both the full and suffix-stripped forms on both sides (purely additive, verified with a standalone reproduction of the exact HGTV case: 1 accidental overlap before, 3 independent overlaps after). (2) One bad external EPG source (of 19+, on any given night) used to throw an uncaught exception that crashed the ENTIRE nightly sync -- playlist, EPG, VOD, series, and the Jellyfin refresh trigger never ran, not just that one source's data going missing. Isolated each source's failure with a try/except + Loki alert instead. (3) Series-folder cleanup deleted ANY file not tracked as `.strm`/`.nfo` -- including Jellyfin's own fetched poster/fanart artwork and external subtitles -- every time a show's info genuinely changed upstream; restricted deletion to just the two extensions the sync actually manages. Live-tested all three together with a real, full sync run (not just isolated tests): completed cleanly end-to-end, 1056 channels, 12238 provider programmes, VOD (24765 movies) and series (7254 shows) processed with prune-guard safety correctly triggering where expected, Jellyfin refresh triggered, zero errors. **Backout plan:** `sudo -n pct exec 105 -- cp /srv/media-core/sync/xtream-sync.py.pre-epg-alias-fix-20260817 /srv/media-core/sync/xtream-sync.py` restores the state before all three fixes (each was applied on top of the same already-deployed file from earlier tonight's title-prefix fix, so this one restore reverts only these three, not that one). Verify via `python3 -m py_compile` and a manual sync run producing the same channel/EPG counts. |
| 2026-08-17 (activate-xepg.py sequencing gap actually fixed) | **The exact manual-workaround-needing gap from earlier today (Bayern Munich Phase 1's DAZN rollout, where I had to run this script twice by hand) now has a real fix instead of needing a manual retry.** Verified the proposed Threadfin API call live before wiring it in: `POST {"cmd":"update.m3u"} /api/` against the real running Threadfin instance returned `{"status": true}` and Threadfin stayed fully responsive afterward. Added as the first thing `main()` does -- asks Threadfin to reload its playlist immediately instead of waiting for its own 04:15 daily poll, so a manually-triggered run (e.g. right after `xtream-sync.py` adds new channels) doesn't have to be run twice anymore. Also applied two smaller, well-explained fixes from the same review: atomic `xepg.json` writes (`.tmp` + `os.replace`, so a crash mid-write can't leave Threadfin's own config file empty/corrupt instead of merely stale) and checking `start_threadfin_verified()`'s return value on exit instead of ignoring it. Live-tested the deployed script: ran cleanly end-to-end, correctly reported "all channels active, nothing to do" (accurate -- nothing was pending after tonight's sync already activated everything). **Backout plan:** `sudo -n pct exec 105 -- cp /srv/media-core/sync/activate-xepg.py.pre-m3u-trigger-fix-20260817 /srv/media-core/sync/activate-xepg.py`. Verify via `python3 -m py_compile` and a manual run. |
| 2026-08-17 (channel_naming.py market-prefix bug fixed) | **Real MEDIUM bug fixed in channel display-name formatting -- verified directly, and caught that a second bundled "fix" in the same review was actually ineffective for the real-world case.** `modernise()` checked `KEEP_PREFIX` (city names like "Milwaukee:") against the UNSTRIPPED input before removing `DROP_PREFIX` (country/provider tags like "US:") -- for an input like `"US: Milwaukee: WTMJ HD"`, the city name isn't at the start of the string yet (it's preceded by "US:"), so `KEEP_PREFIX` never matched and the market tag was silently discarded entirely. Verified directly with a real Python reproduction: old logic produced `market=""`, new logic (strip `DROP_PREFIX` first, then check `KEEP_PREFIX` against the already-stripped remainder) produces `market="Milwaukee: "` correctly. Confirmed via the real `modernise()` function that `"US: Milwaukee: WTMJ HD"` and `"Milwaukee: WTMJ HD"` now produce identical output, with no regression on prefix-less inputs. Separately checked -- and skipped -- an apostrophe-contraction capitalization fix bundled in the same proposed patch (meant to fix `"WOMEN'S NETWORK"` incorrectly staying `"Women'S Network"`): traced it directly and found it doesn't actually solve the problem for real all-caps provider input, since excluding the post-apostrophe letter from re-capitalization just leaves it exactly as capitalized as the ALL-CAPS source already was -- same wrong output either way. Also skipped a bundled, purely-cosmetic dedup of literal duplicate entries in the initialism set (no functional difference, just noise in the diff). **Backout plan:** `sudo -n pct exec 105 -- cp /srv/media-core/sync/channel_naming.py.pre-prefix-order-fix-20260817 /srv/media-core/sync/channel_naming.py`. Verify via `python3 -m py_compile` and re-running `modernise("US: Milwaukee: WTMJ HD")` (expect the market tag to be lost again if rolled back). |
| 2026-08-17 (threadfin_ctl.py: 2 more real bugs fixed) | **Two real robustness bugs fixed in the shared Threadfin-safety module (used by sports-dvr-auto, dvr-dashboard, xtream-sync.py, activate-xepg.py, process-queue.py) -- both verified before fixing.** (1) `_tivimate_recording_active()`'s `try/except OSError` wrapped the ENTIRE inner file-scanning loop, not just the individual `stat()` call -- a single file racing (deleted/renamed between `rglob()` yielding it and `stat()` running on it, plausible since TiviMate actively rewrites files in these exact directories) aborted scanning the REST of that directory too, not just that one file. Since this function directly gates whether it's safe to restart Threadfin, a false negative here could restart Threadfin mid-recording -- exactly what this whole module exists to prevent. Narrowed the try/except to just the `stat()` call. (2) `start_threadfin_verified()`'s retry loop called `docker stop` with `check=True`, uncaught -- a failed stop mid-retry would raise straight out of the function, skipping both the remaining retry attempts AND the `_write_alert()` call that's supposed to leave a marker for the health check on ultimate failure. Changed to `check=False` so a stop failure doesn't defeat the very alerting mechanism this function exists to guarantee. Live-tested the deployed module directly (`recording_in_progress()` correctly returned `False`, matching reality -- nothing scheduled right now). **Backout plan:** `sudo -n pct exec 105 -- cp /srv/media-core/sync/threadfin_ctl.py.pre-exception-scope-fix-20260817 /srv/media-core/sync/threadfin_ctl.py`. Verify via `python3 -m py_compile` and a manual `recording_in_progress()` call. |
| 2026-08-17 (dvr-dashboard + sports-dvr-auto: consistency + market-preference fixes) | **Applied the same `find_matching_program_id` fix to BOTH duplicate copies (dvr-dashboard and sports-dvr-auto) after independently reasoning through and verifying the underlying bug -- I'd deliberately skipped this earlier tonight as "unexplained," but traced it myself this time.** Confirmed with a concrete reproduction: the old logic returned on the FIRST containment hit while scanning the API's program list, so a loosely-scheduled placeholder block that happens to temporally contain `game_start` (but appear earlier in the response than the real, much tighter proximity-matched program) would win purely by iteration-order luck. Fixed by tracking both and only falling back to containment when no proximity match exists at all -- confirmed with a direct reproduction (old logic picked the wrong "WRONG-containment-block" program; new logic correctly picked "RIGHT-proximity-match"). Also added to dvr-dashboard specifically: (1) `LOCAL_MARKET_PREFERENCE = {"Packers": "Green Bay"}` -- the curated lineup has separate Madison/Green Bay/Milwaukee local-market blocks, and the old fuzzy substring fallback for a nationally-broadcast Packers game (FOX/NBC/etc, not in the explicit `NETWORK_CHANNEL_MAP`) could pick whichever same-network channel happened to iterate first, not necessarily Green Bay's -- worst case records the SAME game from a different market's feed, not a wrong game, but worth getting right; (2) `channel_names()` simplified from 2 separate `pct exec` subprocess forks per call to the file's existing cached-token `jf_request()` helper -- this runs on every dashboard poll, so the old version was needlessly expensive. Deployed, restarted the service clean, and regression-tested the untouched Brewers path through the real live endpoint (identical result to before -- same channel, same ProgramId). **Backout plan:** `sudo cp /root/agy-reports/dvr-dashboard-pre-review-fixes-20260817.bak /usr/local/bin/dvr-dashboard \&\& sudo systemctl restart dvr-dashboard` and `sudo cp /root/agy-reports/sports-dvr-auto-pre-containment-preference-fix-20260817.bak /usr/local/bin/sports-dvr-auto` restore the pre-fix state of each. Verify via `python3 -m py_compile` on both and `sudo systemctl status dvr-dashboard` (active, no crash). |
| 2026-08-17 (process-queue.py bitrate ceiling raised) | **Investigated and resolved the one remaining unexplained finding from tonight's reviews -- an undocumented bitrate-ceiling change I'd deliberately skipped earlier.** No rationale was ever given in Agy's original report, but this same session's own Bayern Munich PPV investigation found the provider tags some DAZN PPV streams "8K EXCLUSIVE" -- genuine 8K content commonly needs 40-100+ Mbps depending on codec/motion, which the old 50Mbps sanity ceiling could incorrectly reject as "corrupted" the first time any such stream actually gets recorded and comskip-processed (plausible now that Bayern Munich Phase 3's ad-hoc Record button exists). Raised to 100Mbps -- a low-risk change either way, since widening an upper sanity bound can only prevent a false rejection, it can't newly accept something that was already broken. **Backout plan:** `sudo -n pct exec 105 -- cp /srv/media-core/comskip/process-queue.py.pre-bitrate-ceiling-20260817 /srv/media-core/comskip/process-queue.py`. Verify via `python3 -m py_compile`. |
