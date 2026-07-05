# CLAUDE.md — agent guide for pve-01

You are (most likely) running on `pve-01`, a single-node Proxmox VE 9.2
homelab (Debian 13, KDE Plasma installed directly on the host). This repo is
its documentation and the plan of record. The owner is Finley; the mission
is **Project Media-Core**: a self-hosted DVR/VOD stack (Jellyfin + Threadfin
+ m3u2strm) in **LXC CT 105 `media-core`** (Debian 13, unprivileged,
Docker inside). The stack was **deployed 2026-07-05** ("Phase 0 as-built"
in the plan) and the Swiss split-tunnel for the CT is verified working;
what remains is Phase 2 (provider M3U/EPG URLs + UI config). VM 103, the
original target, was destroyed by the owner on 2026-07-05 along with VMs
100 and 101.

## Read in this order

1. [docs/network-cutover.md](docs/network-cutover.md) — migration runbook
   with a **live status checklist**. Everything router-side is done; the
   server-side steps may or may not be done yet — *verify, don't assume*.
2. [docs/project-media-core.md](docs/project-media-core.md) — the deployment
   plan adapted to this machine. This is the authoritative plan.
3. [docs/media-core-manifest.md](docs/media-core-manifest.md) — the original
   manifest the plan adapts. Defer to the adaptation wherever they differ.
4. Reference: [network](docs/network.md), [storage](docs/storage.md),
   [VMs](docs/virtual-machines.md), [host quirks](docs/host-setup.md),
   [desktop GUI](docs/desktop-gui.md).

## First: establish where things stand

```bash
ip -4 addr show vmbr0 | grep inet   # expect 192.168.9.11 (cutover done 2026-07-05)
pct list; qm list                    # CT 105 media-core should be running; VMs 102/104 exist
pct exec 105 -- docker ps            # jellyfin + threadfin up; m3u2strm only after Phase 2
curl -s -o /dev/null -w '%{http_code}\n' http://192.168.9.50:8096        # 200
curl -s -o /dev/null -w '%{http_code}\n' http://192.168.9.50:34400/web/  # 200
```

The stack lives in `/srv/media-core/` **inside CT 105** (compose + `.env` +
data). Drive the container with `pct exec 105 -- …`.

## Network facts (verified 2026-07-04, from the router itself)

| | |
|---|---|
| Gateway/DNS/DHCP | `192.168.9.1` — GL.iNet **GL-MT6000 "Flint 2"**, fw 4.9.0 (docs may say "Brume 2"; the Flint 2 replaced it in the plan) |
| DHCP pool | `.100`–`.249`; statics `.11` and `.50` are outside it |
| pve-01 | `192.168.9.11/24` static on `vmbr0` (bridge over `enp2s0`, the only cabled NIC) |
| CT 105 | DHCP, reserved lease `192.168.9.50` ← MAC `BC:24:11:59:1F:60` (inherited from destroyed VM 103 — **never give this MAC to another guest**) |
| CT 105 VPN | **All WAN egress goes via the Swiss tunnel** (router tunnel `VM103-Swiss`, kill switch ON) — verified 2026-07-05: CT egresses via `146.70.134.252` (Zurich) while the host does not, no DNS leak. No internet in the CT = tunnel down on the router — check there first, not in the CT. LAN traffic is unaffected. |
| Other devices | default no-VPN; a separate Surfshark-US tunnel exists — leave both alone |
| Router access | web UI / SSH root at `192.168.9.1`; password is **not** in this repo — ask the user. Router-side work is complete; you shouldn't need it. |

## Hard rules

- **This repo is private and must stay free of secrets.** IPTV provider
  M3U/EPG URLs embed account tokens → they live only in
  `/srv/media-core/.env` inside CT 105 (mode 600; compose references
  `${MOVIES_M3U_URL}` / `${TVEPISODES_M3U_URL}`). Never commit them; never
  paste them into logs or commit messages.
- **No `/dev/dri` mapping into Jellyfin initially** — the N5105 iGPU drives
  the host's KDE desktop. Start without HW transcoding (IPTV is H.264
  direct-play); if it's ever needed, bind-mount `/dev/dri/renderD128` into
  CT 105 (LXC shares the render node with the host).
- **Threadfin Simultaneous Streams = 1** — this protects a 1-connection
  IPTV account. Never raise it.
- CT 105's 1 TB data mount (`mp0`, `/srv/media-core`) already has
  **`backup=0`** — keep it that way, or vzdump hauls the recordings
  library into every backup on `SSD`.
- Recordings land on the `local-lvm` thin pool (1 TB promised of 1.7 TB) —
  check `lvs -a` data% when touching storage.
- Images are verified and pinned (Jellyfin `10.11.9`, Threadfin
  `fyb3roptik/threadfin:1.2.37`, m3u2strm
  `jamieeburgess/m3u2strm-docker:docker-b1d57dd`). Keep pinning on
  upgrades; never `:latest` (Jellyfin's `latest` currently points at
  12.0 release candidates).
- Update the runbook checklist and relevant docs in the same commit as the
  work they describe; keep commits on a branch and PR to `main`.

## Environment quirks (details in docs/host-setup.md)

- `apt update` throws a 401 from the enabled-but-unusable PVE enterprise
  repo — disable it before installing anything:
  `mv /etc/apt/sources.list.d/pve-enterprise.sources{,.disabled}`
- Wired networking is Proxmox/ifupdown (`/etc/network/interfaces` +
  `ifreload -a`), **not** NetworkManager — NM only holds old Wi-Fi profiles.
- Machines that knew the server as `192.168.8.11` will hit SSH known-hosts
  warnings after the re-IP (`ssh-keygen -R 192.168.8.11`).
- Users: `root` (PVE admin), `nate` (KDE desktop login).
