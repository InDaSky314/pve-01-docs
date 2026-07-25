# Router security audit — Flint 2 (GL-MT6000), 2026-07-25

Post-rebuild audit. Everything below was verified against the live
device; nothing is inferred from documentation or assumed from defaults.

## Verified good — no action

| Area | Finding |
|---|---|
| WAN input policy | `DROP`. SSH (22), admin UI (80/443) and the metrics exporter (9100) all bind `0.0.0.0`, but WAN-side input is dropped. |
| **Repeater in WAN zone** | The *active* internet path is the WiFi repeater (`apclix0` = logical iface **`wwan`**). Confirmed `wwan` is a member of the `wan` firewall zone, so it inherits `input=DROP`. This was the subtle one — a repeater outside the zone would have been an open hole. |
| Port forwards | **None.** No WAN→LAN redirects at all. |
| Cloud remote access | No GL.iNet cloud / GoodCloud config, no DDNS. No vendor-tunnel exposure. |
| Guest / IoT zones | `input=REJECT`, `forward=REJECT` — properly isolated. |
| Double NAT | Router WAN sits behind an upstream router, so inbound is filtered twice. |
| VPN exit-node abuse | No exit node selected (`ExitNodeID` empty) and not advertising as one → no traffic bypasses the VPN tunnels via tailscale. |
| Tunnel egress | All three tunnels verified leak-free by egress-IP comparison against the bare-WAN reference. |
| Scheduled reboot | Still disabled (`gl_timer.reboot.enable=0`). |

## Findings, by severity

### MEDIUM–HIGH — the tailnet is a fully trusted zone, with 16 stale nodes

The firewall grants the tailnet complete access:

```
firewall.tailscale0.input   = ACCEPT      # all router services: SSH, admin UI, exporter, DNS
firewall.tailscale_lan      = tailscale0 -> lan   # the entire LAN
```

Combined with `AdvertiseRoutes = 192.168.9.0/24`, **any device on the
tailnet can reach the router's admin interface and every host on the
LAN.** That is a reasonable design for remote admin, but it means
tailnet membership is equivalent to full trust — and **16 of 21 peers
are offline**, several for a very long time:

| Peer | Last seen |
|---|---|
| `ipad-gen-6` | 972 days |
| `plex` | 924 days |
| `win11-pve` | 564 days · *offers exit node* |
| `google-chromecast` | 544 days |
| `hisense-smarttv-4k-ffm` | 529 days |
| `samsung-sm-s906u1` | 514 days |
| `samsung-sm-a256b` | 505 days |
| `r2d2` | 456 days · *offers exit node* |
| `fedora-tailscale` | 249 days · *offers exit node* |
| …plus 7 more | 2–230 days |

Every one still holds valid tailnet credentials. If any of those devices
was sold, lost, or reimaged without being removed from the tailnet, it
remains a standing key to the router and the whole LAN.

**Remediation** (owner action, in the Tailscale admin console):
1. Remove nodes that no longer exist or are no longer trusted — the
   multi-hundred-day entries are the priority.
2. Consider tailnet **ACLs** so that reaching the router/LAN is limited
   to specific devices, rather than every node by default. That also
   closes the exporter exposure below.
3. Optionally enable device-approval and key-expiry so dormant nodes
   can't silently retain access.

### MEDIUM — current WireGuard private keys are in Loki history

`wg show <iface> dump` includes the interface's **private key**, and the
previous `wg-snapshot.sh` shipped that dump to Loki verbatim every 2
minutes. Redaction was added 2026-07-25 and verified, but entries
written *before* that change still contain the keys currently in use
(they were regenerated during the rebuild, so today's runs logged live
material).

- Blast radius is limited: Loki is LAN-only on CT 107, and retention is
  ~30 days, so the entries age out on their own.
- Until then, read access to Loki is equivalent to WireGuard key
  disclosure.

**Remediation options**, in ascending effort: accept the ~30-day
retention window; purge the affected streams via Loki's delete API; or
rotate the tunnel keys through the VPN provider's UI. Given the LAN-only
exposure, letting retention handle it is defensible — but it should be a
deliberate decision, not an oversight.

### LOW–MEDIUM — SSH password authentication enabled

```
dropbear.main.PasswordAuth     = on
dropbear.main.RootPasswordAuth = on
```

Key auth works (pve-01's key is installed), so passwords are a
brute-force surface with no upside for normal use. Exposure is limited
to LAN + tailnet, since WAN input is dropped.

**Caveat before disabling:** the rebuild runbook *depends* on password
auth — after a factory reset there is no trusted key, and password login
is the only way back in. Disabling it is safe for day-to-day, but if it
is ever disabled, that dependency must be handled another way (console
access, or re-enabling via the web UI first).

### LOW — WPA2 (`psk2`) rather than WPA3

All SSIDs use `psk2` (WPA2-PSK), including the new `Big-GL`. WPA3
(`sae`, or `sae-mixed` for compatibility) is stronger. Given the device
mix here — FireStick, Chromecast, various IoT — a hard WPA3-only switch
risks breaking clients, and `sae-mixed` weakens the benefit. Reasonable
to leave as-is; recorded so it's a decision rather than an omission.

### LOW — metrics exporter reachable from the tailnet

Port 9100 binds `0.0.0.0` and the tailnet zone is `ACCEPT`, so any
tailnet peer can read it. The data is read-only but does disclose
network topology, client MACs/IPs and traffic volumes. Tailnet ACLs
(above) would close this.

### INFO

- **`IPSec-ESP` / `ISAKMP` (udp 500) are `ACCEPT` on WAN input.** Stock
  OpenWrt defaults for IPSec passthrough; no IPSec server is running, so
  there is no practical exposure. Could be removed for tidiness.
- **`192.168.2.0/24` bypasses VPN policy routing.** Tailscale's routing
  rule sits at priority 5270, ahead of the VPN policy rules at 6000, so
  destinations matching tailscale's table skip the tunnels. A priority-0
  rule protects `192.168.1.0/25` (which also stops it hijacking the WAN
  gateway) but there is no equivalent guard for `192.168.2.0/24`. This
  is RFC1918 traffic going encrypted to another of the owner's own
  networks — not a public-internet leak, but it is a VPN bypass.
- **Guest subnet collides with LAN.** ROM defaults the guest network to
  `192.168.9.1`, which is now the customized LAN address. Guest is
  disabled (`network.guest.disabled='1'`) so it is inert, but enabling
  it without renumbering would conflict.

## Monitoring added this session

- `wg-snapshot.sh` now covers **all** WireGuard interfaces (discovered
  from `wg show all dump`, not hardcoded), closing the `wgclient2` blind
  spot — and redacts private keys before pushing.
- Tailscale health is exported to Prometheus via the node-exporter
  textfile collector (`/etc/tailscale-metrics.sh` → `/var/prometheus/`,
  cron every 2 min). `tailscale_exit_node_in_use` is the
  security-relevant one: it must stay `0`, since a selected exit node
  would route traffic outside the VPN tunnels.
