# pve-01 / Project Media-Core

One document for the whole project: a single-node Proxmox homelab (`pve-01`)
whose main job is **Media-Core** — a self-hosted sports DVR + VOD stack
(Jellyfin + Threadfin + a custom Xtream-API sync) running in an LXC behind a
per-device Swiss VPN tunnel.

> ⚠️ This repo documents a private home network. Keep it **private** on
> GitHub. It contains internal addressing but **no secrets** — provider
> credentials and passwords live only on the server (see [Secrets](#secrets)).

**Status (2026-07-10): fully deployed and verified end-to-end — lineup v8
(per-city locals, 1,856 channels) + Movies/Series libraries live.**
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
  the WireGuard migration; used for all `uci` work).
- **Router syslog forwarding (2026-07-19):** `log_ip`/`log_port`/
  `log_proto` in `uci show system` point at CT 107 (`192.168.9.164:514`
  udp) — the router's own local log buffer is tiny (~64 KB), this gives
  it durable history. See §7 Observability for the full setup and how
  to revert.
- The Flint 2 also runs an IoT subnet (`192.168.10.1/24`) and a guest
  network; the server belongs on the main LAN only.
- The old AXT1800 router (`192.168.8.1` LAN, migrated away 2026-07-05) is
  retired; optionally usable as another AP.

## 4. Guests

| ID | Name | Type | Resources | Notes |
|---|---|---|---|---|
| **105** | **media-core** | LXC, Debian 13, unprivileged | 2 vCPU / 8 GB / 512 M swap | The whole point of the box. `onboot=1`, `nesting=1,keyctl=1` (Docker inside). Rootfs 32 G + `mp0` 1 TB at `/srv/media-core` with **`backup=0`**, both on `local-lvm`. No SSH (use `pct exec`). |
| **107** | **log-server** | LXC, Debian 13, unprivileged | 2 vCPU / 4 GB / 512 M swap | Loki + Grafana + Alloy (2026-07-19) — see §9. `onboot=1`, `nesting=1,keyctl=1`. Rootfs 32 G on `local-lvm`, DHCP networking (no special MAC binding needed, unlike 105). No SSH (use `pct exec`). |
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
   ├─→ threadfin/conf/playlist.m3u   366 channels, regional number blocks
   │       (tvg-chno) — see "Channel map v6" below; strictly US/UK/DE
   │       content (Telemundo/ES dropped); dead brands, event channels and
   │       duplicate-quality prints excluded; per-block start_chno in
   │       config.json
   ├─→ epg/epg.xml                   a <channel> entry with LOGO for every channel
   │                                 (display-name == tuner name → Jellyfin auto-maps
   │                                 artwork) + provider programmes, backfilled from
   │                                 external XMLTV (epgshare01 US/UK/DE dumps) →
   │                                 under v6: 366/366 channels mapped in Threadfin,
   │                                 253 of 343 unique guide ids carry programmes
   │                                 (the rest are PPV/event channels no EPG covers)
   ├─→ media/movies/**.strm          ~20,700 VOD movies, titles normalized to
   │                                 "Title (Year)" (provider prefixes incl. "EN-TOP -
   │                                 NN.", superscript digits ²→" 2", quality tags
   │                                 stripped; 4K/HD copies, year-less duplicate
   │                                 prints, and non-EN twins collapsed) → reliable
   │                                 TMDB artwork matching
   └─→ media/shows/**.strm           TV series (added 2026-07-06): one folder per
                                     show "Title (Year)", Season NN/ subfolders,
                                     "Title SxxEyy.strm" + .nfo per episode and a
                                     tvshow.nfo per show; EN categories first
                                     (same dedupe rule as movies), per-show
                                     episode cleanup + library prune guard
   ▼
Threadfin 1.2.37 (Docker, :34400) — HDHomeRun emulation, ffmpeg buffer,
   │                                 Tuner = 1  ← hard 1-stream brake
   ▼
Jellyfin 10.11.9 (Docker, host network, :8096)
   ├─ Live TV tuner:  HDHomeRun @ http://127.0.0.1:34400
   ├─ Guide:          XMLTV @ /epg/epg.xml
   ├─ Library:        "Movies" → /media/movies (.strm; named "IPTV Cinema" until 2026-07-08)
   ├─ Library:        "Series" → /media/shows (.strm, added 2026-07-06; named "IPTV Series" until 2026-07-08)
   └─ DVR:            records to /media/recordings (remux, no transcode)
   ▼
Clients (Chromecast/web): Jellyfin app → Add server → http://192.168.9.50:8096
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

| Block | Range in use | # | Content (group label in clients) |
|---|---|---|---|
| 100–104 | 100–103 | 4 | **Madison Locals** — ABC 27, NBC 15 (WMTV), FOX 47 (WMSN), CBS 3 (WISC) |
| 105–114 | 105–112 | 8 | **Green Bay Locals** — ABC WBAY, NBC WGBA, FOX WLUK, CBS WFRV (main + DirecTV CITY backup feeds), PBS WPNE |
| 115–119 | 115–118 | 4 | **Milwaukee Locals** — ABC 12 (WISN), NBC 4 (WTMJ), FOX 6 (WITI), CBS 58 (WDJT) |
| 120–124 | 120–124 | 5 | **New York Locals** — ABC, NBC (WNBC), FOX 5 (WNYW), CBS 2 (WCBS), PBS WNJN |
| 125–129 | 125–129 | 5 | **Chicago Locals** — ABC 7 (WLS), NBC 5 (WMAQ), FOX 32 (WFLD), CBS 2 (WBBM), PBS WTTW |
| 130–134 | 130–134 | 5 | **Denver Locals** — ABC 7 (KMGH), NBC 9 (KUSA), FOX 31 (KDVR), CBS 4 (KCNC), PBS KBDI |
| 135–199 | 135–139 | 5 | **Los Angeles Locals** — ABC 7 (KABC), NBC 4 (KNBC), FOX 11 (KTTV), CBS 2 (KCBS), PBS KQIN |
| 200–299 | 200–256 | 57 | **US News** — majors first (CNN, MSNBC, FOX News, ABC/CBS News, …), rest A–Z |
| 300–489 | 300–479 | 180 | **US Cable** — A–Z (A&E … USA Network, incl. Big Brother feeds) |
| 490–519 | 490–507 | 18 | **HBO Max** originals channels |
| 520–599 | 520–526 | 7 | **BBC & Discovery** — BBC News/World/Parliament, Discovery+ 4K, BBC Earth |
| 600–649 | 600–638 | 39 | **Bally Sports** — Wisconsin first, then all RSN feeds |
| 650–679 | 650–668 | 19 | **NFL** — Network, RedZone, event slots 01–15 + 4K |
| 680–699 | 680–699 | 20 | **MLB** — Network ×2, event slots 01–18 |
| 700–739 | 700–715 | 16 | **NBA** — NBA TV, event slots 01–15 (panel duplicates deduped) |
| 740–799 | 740–760 | 21 | **NHL** — Alternate, Network, slots 01–18 + 4K |
| 800–839 | 800–836 | 37 | **UEFA** event slots |
| 840–879 | 840–873 | 34 | **UK Football** (Live Football event slots) |
| 880–899 | 880–898 | 19 | **BBC Streams** 1–19 (event streams) |
| 900–999 | 900–933 | 34 | **Bundesliga** — Sky Sport Bundesliga tiers + Mobil feeds |
| 1000–1299 | 1000–1199 | 200 | **Soccer PPV** — whole panel group, slot names stable ("Soccer PPV 042") |
| 1300–1499 | 1300–14xx | 139 | **DirecTV Stream** — "GO:" 24/7 streaming channels (trimmed 2026-07-18, see below) |
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
  commands it says it ran, don't just re-read its report), and
  anything touching the *live* media-core stack's running state,
  secrets, or git/PR history. Never delegated: CT 105 container
  recreates, config.json/docker-compose.yml changes to the production
  stack, anything that could interrupt a recording.
- **Trust boundary is the owner's call, revisit as needed** — currently
  "simple tasks, or whatever Claude Code is comfortable with the plan
  for." Isolated/new-container work: agy may apply directly. Anything
  touching CT 105's running state: proposed, not applied, without
  explicit sign-off.
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

**Mechanism:** `/root/bin/agy-task.sh <slug> <diagnose|plan|build|raw>
"<prompt>"` (or `@/path/to/file` for the prompt — multi-line prompts
nested through `sudo -> bash -c -> heredoc` break in ways not worth
debugging twice; use a file). Injects mode-appropriate guardrails
automatically (`diagnose` = read-only, `plan` = investigate + propose
only, `build` = may write/deploy but told explicitly never to touch
CT 105 unless the prompt says otherwise). Writes to
`/root/agy-reports/<UTC-timestamp>-<slug>.md` — read *that*, not
agy's own verbose internal CLI logs
(`~/.gemini/antigravity-cli/log/`), which cost real tokens to page
through and mostly aren't useful once a task completes cleanly.
Defaults to model "Gemini 3.1 Pro (High)" (proven quality on both the
channel-117 diagnosis and the log-server build); override per-call
with `AGY_MODEL=` for cheaper/faster trivial lookups.

**Dispatch pattern:** launch via `run_in_background`, then `Monitor`
watching the agy PID for exit (`while kill -0 <pid>; do sleep 5; done`)
— **not** repeated manual polling with scheduled wakeups, which burns
turns for no signal (learned the expensive way on the first diagnostic
run of the night before switching approaches).

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
      2. Disable **GoodCloud** remote admin (router keeps an outbound
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
Historical deep-dives preserved in [`docs/archive/`](docs/archive/):
the original Media-Core manifest (imported verbatim) and the network
cutover runbook (step-by-step with rollback, now fully executed).
