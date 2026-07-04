# CLAUDE.md — agent guide for pve-01

You are (most likely) running on `pve-01`, a single-node Proxmox VE 9.2
homelab (Debian 13, KDE Plasma installed directly on the host). This repo is
its documentation and the plan of record. The owner is Finley; the mission
is **Project Media-Core**: a self-hosted DVR/VOD stack (Jellyfin + Threadfin
+ m3u2strm) inside the existing Docker VM (VMID 103).

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
ip -4 addr show vmbr0 | grep inet     # 192.168.9.11 = cutover done; 192.168.8.11 = NOT done
qm list                                # VM state; 103 = Docker host
qm guest cmd 103 network-get-interfaces 2>/dev/null | grep -o '"ip-address":"192[^"]*'  # VM 103 IP
```

- If the host still has `192.168.8.11`: execute runbook steps 3–6 **from the
  local KDE console, never over SSH** (the session dies mid-change). If you
  are an SSH session, stop and tell the user to run step 4 at the console.
- If the host has `192.168.9.11`: cutover is done; tick the runbook
  checklist, then proceed to the media stack (Phase 0, then Phase 2 in
  [docs/project-media-core.md](docs/project-media-core.md)).

## Network facts (verified 2026-07-04, from the router itself)

| | |
|---|---|
| Gateway/DNS/DHCP | `192.168.9.1` — GL.iNet **GL-MT6000 "Flint 2"**, fw 4.9.0 (docs may say "Brume 2"; the Flint 2 replaced it in the plan) |
| DHCP pool | `.100`–`.249`; statics `.11` and `.50` are outside it |
| pve-01 | `192.168.9.11/24` static on `vmbr0` (bridge over `enp2s0`, the only cabled NIC) |
| VM 103 | DHCP, reserved lease `192.168.9.50` ← MAC `BC:24:11:59:1F:60` |
| VM 103 VPN | **All WAN egress goes via OpenVPN TCP to Zurich** (router tunnel `VM103-Swiss`, kill switch ON). No internet on VM 103 = tunnel is down on the router — check there first, not in the VM. LAN traffic is unaffected. |
| Other devices | default no-VPN; a separate Surfshark-US tunnel exists — leave both alone |
| Router access | web UI / SSH root at `192.168.9.1`; password is **not** in this repo — ask the user. Router-side work is complete; you shouldn't need it. |

## Hard rules

- **This repo is private and must stay free of secrets.** IPTV provider
  M3U/EPG URLs embed account tokens → they live only in a `.env` on VM 103
  (compose references `${M3U_URL}`). Never commit them; never paste them
  into logs or commit messages.
- **Do not start pfSense (VM 100)** — the Flint 2 does all routing; pfSense
  wiring predates the current network and would conflict.
- **No `/dev/dri` mapping into Jellyfin initially** — the N5105 iGPU drives
  the host's KDE desktop. Start without HW transcoding (IPTV is H.264
  direct-play); see the Transcoding section of the plan for the escalation
  path (LXC with shared render node, not GPU passthrough).
- **Threadfin Simultaneous Streams = 1** — this protects a 1-connection
  IPTV account. Never raise it.
- **Before DVR use, set `backup=0` on VM 103's 1 TB data disk** — otherwise
  vzdump hauls the whole recordings library into every backup on `SSD`.
- Recordings land on the `local-lvm` thin pool (1 TB promised of 1.7 TB) —
  check `lvs -a` data% when touching storage.
- Verify Docker image names/tags upstream before pulling (the manifest's
  `freetv/threadfin` and `jacobsnyder/m3u2strm` are unverified); pin
  versions, don't use `:latest`.
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
