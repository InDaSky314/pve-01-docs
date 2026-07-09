# pve-01 / Project Media-Core

One document for the whole project: a single-node Proxmox homelab (`pve-01`)
whose main job is **Media-Core** — a self-hosted sports DVR + VOD stack
(Jellyfin + Threadfin + a custom Xtream-API sync) running in an LXC behind a
per-device Swiss VPN tunnel.

> ⚠️ This repo documents a private home network. Keep it **private** on
> GitHub. It contains internal addressing but **no secrets** — provider
> credentials and passwords live only on the server (see [Secrets](#secrets)).

**Status (2026-07-06): fully deployed and verified end-to-end — lineup v6
(regional block numbering) + TV-series library live.**
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
| Recordings SMB share | `\\192.168.9.50\recordings` — user `tivimate` (password not in this repo) |

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
sync/cache/series/       # per-show get_series_info cache (keyed by last_modified)
threadfin/conf/          # threadfin settings + generated playlist.m3u
epg/epg.xml              # filtered guide (mounted read-only into jellyfin at /epg)
jellyfin/{config,cache}  # jellyfin state — note: excluded from vzdump with the rest of mp0
media/{movies,shows,recordings}
```

### Channel map v7 — the guide numbering protocol (2026-07-09)

Owner spec (v7, replaces v6): guide priority order **Locals → News →
Cable → Sports → 24/7 → German**, compact-hundreds numbering, built from
the owner's hand-picked channel list (review-artifact CSV export,
`refined-live-channels-no-espn.csv` — 1,599 explicit stream ids + the
whole `US| SOCCER PPV` panel group; ESPN dropped by owner choice). Live
and verified in Threadfin XEPG + Jellyfin (1,856/1,856 in the lineup):

| Block | Range in use | # | Content (group label in clients) |
|---|---|---|---|
| 100–139 | 100–115 | 16 | **Wisconsin Locals** — Milwaukee → Madison → Green Bay (main + DirecTV CITY backup feeds, PBS WPNE) |
| 140–149 | 140–144 | 5 | **Chicago Locals** — WLS, WMAQ, WBBM, WFLD, WTTW |
| 150–199 | 150–164 | 15 | **NY/LA/Denver Locals** — ABC/NBC/CBS/FOX + PBS per city |
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
| 1300–1499 | 1300–1456 | 157 | **DirecTV Stream** — "GO:" 24/7 streaming channels |
| 1500–2099 | 1500–2079 | 580 | **Prime 24/7** — PRIME looping channels |
| 2100–2499 | 2100–2407 | 308 | **Cinema TV** — "CM" Apple TV+/Disney+/Amazon/Netflix 4K loops |
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

**EPG layers (v7).** In order of preference per channel: (1) provider
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
- **Streaming bandwidth ceiling:** the Swiss OpenVPN tunnel tops out
  around **10–20 Mbit/s** (1.3 MB/s measured 2026-07-05 daytime;
  2.25 MB/s sustained 2026-07-06 ~01:00 — varies with exit-server
  load). 1080p web-rips play fine; Blu-ray/4K remuxes (15–80 Mbit)
  **will buffer** — pick the non-4K copy or set a client bitrate limit
  (~8 Mbit). The initial metadata scan competes for the same tunnel
  (posters via TMDB); `LibraryScanFanoutConcurrency=2` set to keep it
  polite. Real fix: switch the router tunnel to **WireGuard** — see the
  migration plan in [Loose ends](#7-loose-ends). Manual sync:
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

## 7. Loose ends

- [x] Create the first Threadfin web-UI user (done by owner, 2026-07-05).
- [x] Change the Jellyfin `root` password (done by owner, 2026-07-05).
- [ ] Jellyfin's scan of the `.strm` movies (~27.7k since the 2026-07-08
      exclude-mode expansion) takes hours and hammers TMDB; artwork fills
      in progressively — let it finish before judging the "Movies"
      library (stale pre-rename entries disappear when the scan's cleanup
      pass runs). Same for the "Series" library (2026-07-06) — genre
      views in both libraries fill in as TMDB metadata converges
      (~240 items/h behind the 10 Mbit tunnel).
- [ ] Many v7 picks are high-bitrate feeds (Cinema TV "4K" loops, Soccer
      PPV "8K EXCLUSIVE" slots) — they will buffer behind the ~10 Mbit
      OpenVPN tunnel until the WireGuard migration happens, or unless the
      client sets a bitrate limit.
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
- [ ] Optional: TiviMate on the Chromecast for casual channel-surfing —
      but **close it before scheduled recordings** (1-connection account).
- [ ] Host housekeeping (pre-existing): disable enterprise apt repo, delete
      VM 102's `unused0` disk, consider off-host backups.
- [ ] **Proposed — OpenVPN → WireGuard on the Flint 2** (owner action,
      needs ~20 min + a no-recording window; approved in principle
      2026-07-05, plan below):
      1. In the Surfshark dashboard: *VPN → Manual setup → Router →
         WireGuard* → generate a key pair, download a **Switzerland
         (Zurich)** `.conf`.
      2. Flint 2 UI → *VPN → WireGuard Client → Add profile → upload the
         .conf* (name it `VM103-Swiss-WG`).
      3. *VPN Dashboard → Proxy mode "Based on the target device"*: move
         the MAC `BC:24:11:59:1F:60` binding from the OpenVPN profile to
         the WireGuard profile; re-tick **Block non-VPN traffic** (kill
         switch) for that device.
      4. Verify from pve-01: `pct exec 105 -- wget -qO-
         https://am.i.mullvad.net/json` → still Switzerland; re-run the
         speed test (expect ~5–10× — the MT7986 does WireGuard in
         hardware, several hundred Mbit vs ~20 for OpenVPN-TCP).
      5. Keep the OpenVPN profile disabled-but-saved as rollback; if the
         panel ever blocks the WG exit IP range, switch back in one click.
      Afterwards: raise `LibraryScanFanoutConcurrency` 2 → 4 and drop the
      client bitrate caps — remux buffering should be gone.

## 8. History

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
| 2026-07-09 | **Lineup v7 — owner's hand-picked 1,856-channel guide.** Built from the review-artifact CSV (1,599 explicit stream ids + whole Soccer PPV group; ESPN dropped, German general TV kept but moved to 2500s per owner). Compact-hundreds priority order: Locals → News → Cable → Sports → 24/7 → German. Sync gained explicit-`ids` selections, stable slot display names (`slot`), PPV event parsing (`epg_mode: "ppv"`, ~340 slots incl. times parsed as UTC from names), synthesized looping guide for ~1,143 24/7 channels, i.mjh.nz external feeds, and an hourly `media-core-ppv.timer` refresh (change-detected; Threadfin `update.xmltv` via its now-enabled API + Jellyfin guide refresh). Threadfin quirks found: keys channels by name and truncates at apostrophes (names now apostrophe-stripped — VEVO '70S/'80S had collapsed into one channel); tvg-chno only adopted for new channels (renumber-xepg.py handles renumbering). Panel carries every NBA slot twice — deduped to 16. The old "<500 channels" rule was owner-superseded; practical scale limits are guide-refresh time and EPG size, tuner stays 1. |
| 2026-07-08 | **Exclude-mode VOD/series + library renames.** Owner supplied panel-wide exclude lists (782 live / 24 VOD / 26 series categories, all verified against the panel). Sync switched to exclude-based selection for VOD (66 cats, ~27.7k movies) and series (52 cats, ~7.5k shows); old include keys now only order the dedupe (HD before 4K/Dolby — VPN can't play high-bitrate remuxes — then EN-first). Jellyfin libraries renamed "IPTV Cinema"→**Movies**, "IPTV Series"→**Series** (owner naming). Series backfill confirmed complete (6,124 shows); the pending empty-`PresentationUniqueKey` stamp applied to 28,225 `/media/shows` rows (jellyfin stopped → UPDATE → started). Live lineup unchanged pending owner's channel-level selection from the review artifact (v6 keeps working; 4 blocks flagged as source-less under the new list). |

Historical deep-dives preserved in [`docs/archive/`](docs/archive/):
the original Media-Core manifest (imported verbatim) and the network
cutover runbook (step-by-step with rollback, now fully executed).
