# Network Topology & Router Inventory

Written 2026-08-09 while evaluating a proposed re-layout (3.1 as new DSL
head-end). Everything below was verified directly via SSH against each
device the same day, not assumed from memory. Router-specific rebuild
detail for 9.1 stays in `router-rebuild-runbook.md`; this doc is the
cross-device map that didn't exist before.

## Inventory

| Device | IP | Model | Firmware/OS | SSH alias |
|---|---|---|---|---|
| R2D2 | `192.168.1.1` | Ubiquiti UniFi Dream Router (UDR) | UniFi OS (kernel `4.4.198-ui-mtk`) | `unifi-1.1` (owner-provided password, key installed 2026-08-08) |
| — | `192.168.2.1` | GL.iNet **GL-MT2500** | GL firmware 4.7.4 | `glinet-2.1` |
| Big-Big-GL | `192.168.3.1` | GL.iNet **GL-BE9300** ("Flint 3", WiFi 7) | GL firmware 4.10.0, OpenWrt 23.05-SNAPSHOT | `glinet-3.1` |
| (Flint 2) | `192.168.9.1` | GL.iNet **GL-MT6000** ("Flint 2") | see `router-rebuild-runbook.md` | `glinet-9.1` |

All four SSH aliases use `IdentityFile /root/.ssh/id_ed25519_routers`,
`IdentitiesOnly yes` — confirmed working against all four 2026-08-09
(1.1 needed the `unifi-1.1` alias specifically; connecting to
`root@192.168.1.1` directly fails since `IdentitiesOnly` scopes the key
to the matching `Host` block).

pve-01 itself sits behind 9.1 today (`192.168.9.11`); see
`router-rebuild-runbook.md` for 9.1's VPN policy engine, backup/restore
procedure, and known gotchas — not repeated here.

## Current topology (as of 2026-08-09)

- **2.1 (GL-MT2500) is the real internet head-end.** Confirmed via its
  `network.wan` config: PPPoE dialing a `t-online.de` (Deutsche Telekom
  DSL) account directly. It feeds **3.1** and **1.1**.
- **9.1 (Flint 2)** mesh-links to **3.1** and is also hardwired to **1.1**
  via a powerline adapter. pve-01 and the rest of media-core sit behind 9.1.
- Credentials (PPPoE login, WiFi passwords, VPN keys) live on-device and
  in `/root/router-backups/` (600/700, outside git) — deliberately not
  reproduced in this doc.

```
DSL ─▶ 2.1 (GL-MT2500, PPPoE head-end)
         ├─▶ 3.1 (Flint 3) ── mesh ──▶ 9.1 (Flint 2) ─▶ pve-01 / media-core
         └─▶ 1.1 (UDR/R2D2)              │
                                      powerline
                                          ▼
                                        1.1
```

## Proposed re-layout (under discussion, not yet applied)

Owner is considering moving the Proxmox host's own router dependency
from 9.1 to 3.1 (more capable hardware — see capability notes below),
and making **3.1 the DSL head-end** directly, feeding **1.1** and **9.1**.
9.1 would then step back from "head-end path" duty and instead serve as
a wired hub for new devices (Google Streamer, LG TV) that shouldn't
depend on wireless.

Open question raised during this review: **2.1's role in the new
layout isn't decided yet** — whether it's retired, or repurposed. Confirm
before actually cutting over, since it currently holds the live PPPoE
session.

```
DSL ─▶ 3.1 (Flint 3, proposed new head-end)
         ├─▶ 1.1 (UDR/R2D2)
         └─▶ 9.1 (Flint 2) ─▶ wired hub for Google Streamer / LG TV
                                (no longer on the path to pve-01 unless
                                 pve-01 also moves to 3.1)
2.1 (GL-MT2500) — role TBD
```

## 3.1 (GL-BE9300) capability notes, gathered for this evaluation

Confirmed live via SSH, relevant to the proposed head-end swap:

- **Per-SSID/per-network VPN tunnel binding is supported natively.**
  `gl-sdk4-vpn-policy` + `gl-sdk4-ui-vpndashboard` installed, WireGuard +
  OpenVPN client/server + multi-WAN (`kmwan`) all present. The device
  already separates SSIDs onto distinct bridged networks (`lan`, `guest`,
  `iot` — each with its own `br-*` interface and per-band SSIDs), and
  `route_policy` rules route by source (device/group/process). Binding a
  dedicated network (i.e., a dedicated SSID) to a dedicated VPN tunnel is
  the same mechanism already used for the existing `iot` segment — not a
  new capability, just an additional instance of the existing pattern.
- **Native WiFi radio scheduling**, via the `gl_timer` UCI config —
  per-band (2G/5G/6G/MLD) and per-guest-vs-main-SSID entries, each
  supporting both `turn_onoff` (full radio kill) and `power_switch`
  (scheduled tx-power drop to "Low", restore to "Max") on an hour/minute/
  day-of-week schedule. Present in config, currently all `enable='0'`
  (UI toggle away, no scripting needed).
- No `client_group` entries configured yet (checked, empty) — would need
  to be set up as part of implementing the 3-tunnel SSID plan.

## Related docs
- `router-rebuild-runbook.md` — 9.1 (Flint 2) backup/restore, VPN policy
  gotchas, reboot-survival checklist.
- `home-network-power-automation.md` — R2D2 (1.1) Tailscale/UniFi OS
  version issue.
