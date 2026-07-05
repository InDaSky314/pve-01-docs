# CLAUDE.md — agent guide for pve-01

You are (most likely) running on `pve-01`, a single-node Proxmox VE 9.2
homelab. **Everything is documented in [README.md](README.md)** — one
consolidated file; read it first. Owner: Finley (desktop user `nate`).

## Establish where things stand

```bash
ip -4 addr show vmbr0 | grep inet        # expect 192.168.9.11
pct list; qm list                         # CT 105 running; VMs 102/104 exist
pct exec 105 -- docker ps                 # jellyfin + threadfin up
curl -so /dev/null -w '%{http_code}\n' http://192.168.9.50:8096       # 200
curl -so /dev/null -w '%{http_code}\n' http://192.168.9.50:34400/web/ # 200
pct exec 105 -- wget -qO- https://am.i.mullvad.net/json               # Switzerland
```

The media stack lives in `/srv/media-core/` inside CT 105; drive it with
`pct exec 105 -- …` (the CT has no SSH by design).

## Hard rules

- **Secrets stay out of this repo** — provider credentials live only in
  `/srv/media-core/.env` (600) and generated files inside CT 105. Never in
  commits, logs, or chat pastes.
- **Threadfin tuner stays at 1** (1-connection IPTV account) and the
  playlist stays under ~500 channels.
- **MAC `BC:24:11:59:1F:60` belongs to CT 105 only** — it carries the
  `.50` lease and the Swiss-VPN binding on the router.
- **No `/dev/dri` into Jellyfin** (iGPU drives the host's KDE desktop);
  escalation path is a render-node bind mount into the LXC, documented in
  the README.
- **`mp0` keeps `backup=0`**; note this also excludes app config from
  vzdump (see Operations in README).
- **Pinned image tags only** — never `:latest`.
- No internet inside CT 105 = Swiss tunnel down on the router (kill switch
  working). Check the router first, not the CT.
- Keep commits on a branch and PR to `main`; update the README in the same
  commit as the work it describes. The apt enterprise repo throws 401 —
  disable it before host `apt` work
  (`mv /etc/apt/sources.list.d/pve-enterprise.sources{,.disabled}`).
- Wired networking is Proxmox ifupdown2, not NetworkManager. Never re-IP
  the host over SSH — use the local KDE console.
