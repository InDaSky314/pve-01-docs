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
- **Threadfin tuner stays at 1** (1-connection IPTV account). The
  playlist is ~996 channels as of the 2026-07-18 guide-speed trim (was
  ~1,856 under lineup v8; owner's explicit picks — the old "<500
  channels" cap is superseded); don't grow it back without checking
  Jellyfin guide-refresh time.
- **MAC `BC:24:11:59:1F:60` belongs to CT 105 only** — it carries the
  `.50` lease and the Swiss-VPN binding on the router.
- **`/dev/dri/renderD128` is shared into CT 105 for Jellyfin QSV since
  2026-07-18** (`dev0` in the LXC config, gid 992; compose `devices` +
  `group_add`). The iGPU still drives the host KDE desktop — the render
  node is shared, don't move to full GPU passthrough.
- **`mp0` keeps `backup=0`**; note this also excludes app config from
  vzdump (see Operations in README).
- **Pinned image tags only** — never `:latest`.
- No internet inside CT 105 = Swiss tunnel down on the router (kill switch
  working). Check the router first, not the CT.
- Keep commits on a branch and PR to `main`; update the README in the same
  commit as the work it describes. Work isn't done until the PR is merged
  to `main` — agents (Claude Code, Antigravity, or others) resume from
  `main`, so an unmerged branch is invisible to the next session. The apt enterprise repo throws 401 —
  disable it before host `apt` work
  (`mv /etc/apt/sources.list.d/pve-enterprise.sources{,.disabled}`).
- **`origin` push goes to two GitHub repos** (set up 2026-07-18):
  `InDaSky314/pve-01-docs` (primary, HTTPS via `gh`; PRs live here) and a
  mirror `nk-sys-ops/pve-01-docs` (SSH `github-mirror` alias in
  `/root/.ssh/config`, deploy key `/root/.ssh/id_ed25519_pve01docs_mirror`,
  write-enabled). `git push origin <ref>` hits both. **`gh pr merge`
  updates only the primary server-side**, so after a merge run
  `git -C /root/pve-01-docs checkout main && git pull && git push origin main`
  to propagate the merge to the mirror (PRs themselves don't mirror).
- Wired networking is Proxmox ifupdown2, not NetworkManager. Never re-IP
  the host over SSH — use the local KDE console.
