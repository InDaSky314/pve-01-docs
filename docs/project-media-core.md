# Project Media-Core — Adapted Deployment Plan for pve-01

Adaptation of the "Media-Core" systems manifest (self-hosted DVR + VOD stack:
Jellyfin, Threadfin, m3u2strm) to this environment. The original document
assumes a bare-metal headless mini-PC behind a **GL.iNet Brume 2** on
`192.168.9.0/24`. Here the "micro-computer" is `pve-01`, the stack lands in
the existing **Docker VM (103)**, and the network gets migrated from the
current AXT1800 `192.168.8.0/24` LAN to the Brume 2 `192.168.9.0/24` design.

> **2026-07-04:** the gateway actually deployed on `192.168.9.1` is a
> **GL-MT6000 "Flint 2"**, not a Brume 2. Everything below reads the same
> with "Flint 2" substituted; bonus: it has Wi-Fi radios, so the
> AXT1800-as-AP step is optional. Live router state and completed steps are
> tracked in [network-cutover.md](network-cutover.md).

## Target state

```
Internet
   │
Brume 2 gateway ── 192.168.9.1 ── DHCP, WireGuard policy routing
   │                              ├─ Tunnel A (USA): all normal clients
   │                              └─ Tunnel B (CH):  VM 103 only (by MAC/IP)
   ├── AXT1800 in Access-Point mode (Wi-Fi for Chromecasts etc.)
   │      └── Chromecast(s) — dynamic DHCP, Jellyfin client → 192.168.9.50
   │
   └── pve-01  enp2s0 → vmbr0 → 192.168.9.11 (host, was 192.168.8.11)
          └── VM 103 "Docker" → 192.168.9.50 (static DHCP lease)
                ├── Jellyfin   (host network, :8096)  ← DVR + VOD frontend
                ├── Threadfin  (:34400)               ← M3U proxy, 1-stream cap
                └── m3u2strm                          ← VOD links → .strm files
```

Key point for the policy routing: the Proxmox bridge preserves each VM's own
MAC address on the wire, so the Brume 2 sees VM 103
(`BC:24:11:59:1F:60`) as an independent device and can route **only its**
traffic through Tunnel B while the PVE host, the other VMs, and every
Chromecast go out Tunnel A / direct. No pfSense needed — VM 100 stays
stopped; the Brume 2 does all routing and VPN work.

## Divergences from the original manifest

| Manifest assumption | This deployment |
|---|---|
| Bare-metal Ubuntu/Windows mini-PC | Existing Docker VM 103 on pve-01 (2 vCPU / 8 GB — matches the doc's 8 GB minimum) |
| 1 TB external USB SSD (NTFS/EXT4) | VM 103's existing 1 TB virtio data disk (EXT4) on `local-lvm` — faster and already provisioned; no FAT32 4 GB-file concern |
| Server at `192.168.9.50` via static lease | Same IP, but leased to the **VM's** MAC, not the host's |
| Direct `/dev/dri` Quick Sync mapping | **Deferred** — see "Transcoding" below; the iGPU currently drives the host's KDE desktop |
| Headless node | pve-01 keeps its KDE desktop; the *VM* is the headless node |
| GL.iNet gateway is the only router | AXT1800 is repurposed as a Wi-Fi access point behind the Brume 2 (Brume 2 has no radios; Chromecasts must be on the same L2 for per-device policy routing) |

## Phase 0 — Build and test now (no network changes)

Nothing in the stack depends on the subnet until client setup, so the whole
stack can be built and smoke-tested on the current `192.168.8.x` network
first, then survive the router swap unchanged (it will just get a new IP).

1. On VM 103, verify Docker + compose, and that the 1 TB disk is mounted
   (target layout below).
2. Create `/srv/media-core/` on the 1 TB disk:

   ```
   /srv/media-core/
   ├── docker-compose.yml
   ├── jellyfin/{config,cache}
   ├── threadfin/conf
   └── media/{movies,recordings}
   ```

3. Deploy the compose stack from the manifest with these changes:
   - **Drop the `devices:` (`/dev/dri`) section** from Jellyfin for now
     (no GPU inside the VM yet — see Transcoding).
   - Keep `network_mode: host` for Jellyfin (needed for the emulated
     HDHomeRun tuner discovery) — fine inside a VM.
   - `TZ=Europe/Berlin` already matches the host.
   - **Verify image names on Docker Hub before pulling** — the manifest's
     `freetv/threadfin` and `jacobsnyder/m3u2strm` tags should be
     double-checked against the upstream projects (Threadfin's official
     image lives under the Threadfin project; several m3u2strm forks
     exist). Pin to specific versions rather than `:latest`.
4. Smoke test on the current LAN: Threadfin UI at `http://<vm-ip>:34400/web/`,
   Jellyfin at `http://<vm-ip>:8096`.

## Phase 1 — Network cutover to the Brume 2

> **Expanded into a step-by-step runbook with exact commands, verification
> and rollback: [network-cutover.md](network-cutover.md).** The outline
> below is kept for context; execute from the runbook.

Do this in one maintenance window; everything is reversible.

1. **Brume 2**: configure LAN `192.168.9.1/24`, DHCP on. Add the two
   WireGuard client tunnels (A: USA, B: Switzerland) and enable per-device
   policy routing (Tunnel B bound to `192.168.9.50` / the VM's MAC only).
2. **Static lease**: reserve `192.168.9.50` for MAC `BC:24:11:59:1F:60`
   (VM 103's `net0`).
3. **AXT1800**: switch to Access-Point mode, uplink LAN→LAN into the
   Brume 2. SSIDs and Wi-Fi clients (Chromecasts) carry over; they now get
   `192.168.9.x` addresses from the Brume 2.
4. **pve-01 re-IP** — do this from the **local KDE console**, not SSH
   (the SSH session dies mid-change): in the PVE web UI or
   `/etc/network/interfaces`, change `vmbr0` from
   `192.168.8.11/24` gw `192.168.8.1` → `192.168.9.11/24` gw `192.168.9.1`,
   then `ifreload -a`. Update `/etc/hosts` (pve-01 entry) and
   `/etc/resolv.conf` to match.
5. Re-check: PVE web UI now at `https://192.168.9.11:8006`; VM 103 pulls
   `192.168.9.50`; confirm the VM's egress IP is the Tunnel B endpoint
   (`curl ifconfig.me` from the VM) while a laptop shows Tunnel A/direct.

## Phase 2 — Application configuration

Follow the manifest's section 5 order, with our addresses:

1. **Threadfin** (`http://192.168.9.50:34400/web/`): add the provider M3U +
   XMLTV EPG URLs; set **Simultaneous Streams = 1** (software brake that
   keeps the account inside its 1-connection plan); filter the channel list
   to the needed sports networks, staying under ~500 channels.
2. **Jellyfin** (`http://192.168.9.50:8096`): add Live TV tuner as
   HDHomeRun at `http://localhost:34400/tuner/threadfin`; add the XMLTV
   guide source; add a "Movies" library at `/media/movies` for the `.strm`
   files m3u2strm generates.
3. **m3u2strm**: set `M3U_URL` to the provider playlist (keep it out of
   git — use a `.env` file; `docker-compose.yml` can reference
   `${M3U_URL}`), 24 h sync interval as per the manifest.
4. **Chromecasts**: install the Jellyfin app, add server manually:
   `http://192.168.9.50:8096`.

## Transcoding (Quick Sync) decision

The manifest maps `/dev/dri` into Jellyfin for Intel Quick Sync. On pve-01
there's a conflict the manifest didn't anticipate: **the N5105's iGPU
currently drives the KDE desktop on the host.** Options, in order:

1. **Start with no HW transcoding** (recommended). Chromecasts direct-play
   H.264/AAC, which is what IPTV sports streams overwhelmingly are; DVR
   recording is a straight remux (no transcode at all). There's a good
   chance QSV is never needed.
2. **If transcoding becomes necessary**: move the media stack from VM 103
   into an **LXC container** with `/dev/dri/renderD128` bind-mounted. LXC
   shares the render node with the host, so the KDE desktop keeps working.
3. **Full iGPU passthrough to VM 103** is the last resort — it takes the
   GPU (and local console/desktop) away from the host entirely.

## Housekeeping / risks

- **Backups**: exclude `media/recordings` from vzdump for VM 103 (set the
  1 TB data disk's `backup=0` flag or use fleecing) — otherwise every
  backup run hauls hundreds of GB of recordings.
- **Thin-pool watch**: recordings live on `local-lvm` (thin). 1 TB of the
  1.7 TB pool is promised to this disk; keep an eye on
  `lvs -a` data% as the DVR fills up.
- **Secrets**: the provider M3U/EPG URLs embed account tokens. Keep them in
  `.env` on the VM only — never in this repo.
- **Docs**: `README.md` and `docs/network.md` have been updated to the
  `192.168.9.x` addressing ahead of the cutover; the live status checklist
  is in [network-cutover.md](network-cutover.md).
- **Content sourcing**: the open-source stack itself is legitimate
  tooling; what's actually licensed to record/replay depends entirely on
  the IPTV provider behind the M3U URL.
