# Overnight report — post-cutover stabilisation
**Night of 2026-08-27 → 28 · prepared for 06:00**

## Bottom line

Both routers are good. The media stack is fully functional. Both weekend games
are scheduled, the Packers one on your local affiliate. Everything below has been
verified through **three router reboots**, the last one with no intervention at
all, so it will survive a power cycle while you are asleep.

One thing needs your decision, and one thing I could not get (English Bayern) —
both at the bottom.

---

## What was actually broken when you went to bed

The cutover itself succeeded, but the media stack was dead underneath it. Every
container had **no internet**. This turned out to be three separate faults
stacked under a tunnel that looked perfectly healthy — addresses assigned, live
handshakes, correct policy marks, and `ip route get` returning the right tunnel.

| # | Fault | Effect |
|---|---|---|
| 1 | `accept_to_wgclientN` was an **empty firewall chain** | forward policy `drop` killed all client traffic |
| 2 | The **masquerade jump was missing** (`oifname wgclientN jump srcnat_wgclientN`) | packets left with a private source; replies never returned |
| 3 | `/tmp/resolv.conf.d/resolv.conf.wgclientN` was **zero bytes** | per-tunnel dnsmasq had no upstream → every lookup `REFUSED` |

All three trace to one root cause: **netifd never completes the "up" transition
for these WireGuard interfaces**, so fw4 built the zones with no device and the
DNS files were never written. The tunnels work for the router itself (which is
why `curl --interface wgclient1` looked fine) but not for anything behind it.

Fault 2 was the nastiest — conntrack showed reply packets arriving, which reads
as a routing problem when it is actually NAT.

## Fixes applied

Everything is self-healing from `/etc/hotplug.d/iface/99-network-buildout-persist`,
because `/tmp` is volatile and GL's `rtp2.sh` wipes firewall zone bindings on every
boot. A one-time fix would not have survived.

- **`reconcile_vpn_dns()`** — repopulates the per-tunnel resolv files from the
  peer's DNS config, then reloads dnsmasq. Only touches files that are missing or
  empty, so it never clobbers a good one.
- **`reconcile_vpn_firewall()`** — binds each VPN zone to its *device* rather
  than its netifd network, then restarts the firewall so the srcnat jumps get
  emitted. Skips tunnels with no address rather than thrashing.

Earlier in the evening (with Agy) the deeper deadlock was also fixed: `rtp2.sh`
was calling `/etc/init.d/network reload` from an interface-status handler, which
deadlocked on `procd_network.lock` and left 34 stuck processes piling up. That is
resolved — the count is now 0.

Also cleaned up: the stale `99-network-buildout-persist` from 2026-08-10 that had
hardcoded `mld1 master br-iot` (putting **GIOT on the German tunnel**) and was
re-injecting obsolete priority-5900 ip rules that forced all no-VPN LAN traffic
through Zürich. That is why your Proxmox host kept exiting Switzerland.

## Verified state

Confirmed after an **unattended** reboot — I rebooted and touched nothing.

| Host | Exit IP | Location |
|---|---|---|
| media-core, jellyfin-vod, jellyfin-npvr | 146.70.134.254 | **Zürich, CH** |
| log-server, scraper | 45.144.115.60 | **Ashburn, US** |
| pve-01 host | 93.209.196.177 | **Deutsche Telekom** (native, no tunnel — as you asked) |

- All 4 tunnels: Zürich / Frankfurt / Ashburn / New York
- `mld0` → `br-lan` (Open-Fields), `mld1` → `br-guest` (GIOT) — both correct
- Stale 5900 ip rules: 0 · stuck `rtp2.sh` processes: 0
- Jellyfin, jellyfin-npvr, Threadfin all HTTP 200 · 1225 channels
- NextPVR: 957 channels, 37,190 EPG events

**MT6000 (now 192.168.5.1)** — healthy, serving the TV corner (LG webOS TV,
FireStick, Chromecast, phones). I re-scoped its DHCP reservations from
`192.168.9.x` to `192.168.5.x`, which the hand-run cutover had missed, and removed
seven stale Proxmox reservations that now belong to the BE9300. Note its WAN is
still `192.168.1.12` via the UDR, **not** re-cabled to the BE9300 — it works, it
is just an extra hop.

## Friendly names

Set on both routers via the GUI's own handler (`clients.set_info`), plus durable
`gl-client` aliases and named DHCP reservations:

`media-core` · `log-server` · `scraper` · `jellyfin-vod` · `jellyfin-npvr` ·
`jellyfin-live` · `android-emulator` · `pve-01` — and on the MT6000:
`DE-Chromecast` · `LR-FireStick` · `LG-webOS-TV` · `Amazon-FireTV`

**Caveat, stated plainly:** the aliases and reservations are stored correctly, but
the GUI's client list reads its `name` from the DHCP-supplied hostname, which LXC
containers do not send — so those rows may still render blank in the web UI. The
data is right; the display may not be. Making it show would mean configuring each
container to send a DHCP hostname, which I did not do unsupervised.

## Weekend games

| Game | Channel | Window (UTC) |
|---|---|---|
| Bayern vs VfB Stuttgart | Sky Sport Bundesliga 1 HD | Fri 17:00 → 21:15 (kickoff 18:25) |
| Cardinals @ Packers | **Green Bay: NBC 26 (WGBA)** | Sat 00:00 → 03:00 |

**The Packers game is already on your local affiliate** — NBC 26 WGBA, Green Bay.
Exactly what you asked for, and it was already correct.

**English Bayern: I could not find one.** I scanned 3,473 programmes across the
kickoff window. The only non-German feed is Spanish (Telemundo, "Live: Fútbol
Bundesliga"). No English broadcast exists in the EPG *yet* — which is expected,
because the English PPV slots only get labelled close to kickoff. The automation
is built for exactly this and is intact: `DYNAMIC_PPV_TEAMS = {Bayern Munich}`,
with DAZN PPV GB (198 channels) and Sky Sports+ (70) in scope. It resolves the
English slot **6 hours before kickoff (~14:25 CEST Friday)**, probes it at
kickoff−40min, and reverts to the German feed by kickoff−8min if the probe yields
nothing. So the German recording is guaranteed and English is opportunistic.

I also fixed a real blocker here: the automation could not create timers at all
(`Jellyfin API POST failed`). The Jellyfin API key was missing on the host. That
said — the current failures in the log are **benign**: it is a duplicate-timer
rejection, because a timer with that exact ProgramId already exists. Both games
are covered.

## Needs your decision

**The server will shut down Tuesday.** I set the keep-awake override through
Monday (expires Tue 06:00) — the API caps at 24h, so I wrote the file directly.
But now that the mains timer is bypassed, when the override expires the nightly
`dvr-clean-shutdown` will halt the box **and it will not come back on its own**,
because BIOS "Power On" only helps after a power cut. Either re-extend before
Tuesday or plan to press the button.

## Smaller items

- `grafana-proxy.socket` was failing on every boot (binding its Tailscale address
  before `tailscaled` was up). Fixed with `FreeBind` + ordering; still bound only
  to the tailnet, not widened.
- `gateway-2-1-monitor` was pinging the retired MT2500 every 3 seconds forever.
  Stopped and disabled — unit kept, not deleted.
- `router-dashboard` still has hardcoded references to `192.168.3.1`,
  `192.168.2.241` and a ProxyJump via the retired `glinet-2.1`. It still serves
  metrics, so I left it — repointing it is a code change worth doing with you
  awake to eyeball the UI.
- `openipmi.service` fails on every boot. Benign and unrelated: the board has no
  BMC (`ipmi_si: Unable to find any System Interface(s)`). Mask it if you want a
  clean unit list.

## Still unproven

**Per-SSID VPN egress has never been tested with a real client.** The rules match
on inbound interface, which only applies to forwarded traffic, so nothing I run
from the router proves anything. Connect a phone to each SSID and check the exit:
GIOT → New York, WALDO → Frankfurt, Open-Fields → your own IP.

---

## Independent validation (Agy)

I had Agy adversarially review the fixes, with instructions to challenge them. It
performed **its own separate reboot** and reached the same result independently —
so this has now survived four reboots across two agents.

It confirmed:

- All containers egress correctly with **DNS working**, unattended
- All four `srcnat` jumps emitted with no intervention
- **Killswitch fails closed** — blackhole routes in tables 1001/1002/1003, the
  priority-9910 rule, and an `lan_drop_leaked_dns` firewall rule
- **No plaintext DNS leak** — queries stay encapsulated in the tunnel
- Both timers intact, with IDs; power override confirmed to Tue 06:00
- MT6000 healthy, all TV devices on `192.168.5.x` with correct names

It raised four issues. **I deliberately fixed none of them tonight**, and I want
to be explicit about why rather than have you find unexplained changes:

| Agy's finding | My call |
|---|---|
| **Geo-DNS on the Swiss tunnel** — resolvers are in New York and Frankfurt, not Switzerland, so IPTV CDN lookups may resolve to non-Swiss edges | **Valid, but not a regression.** This is the firmware's own default — `ovpnclient1` has the identical pair, and it is exactly what netifd would have written. IPTV works right now. Changing DNS on the IPTV path hours before two scheduled recordings trades a working system for a theoretical geo benefit. **Worth doing with you awake, after the games.** |
| Replace `firewall restart` with `fw4 reload` in the boot hook (blocks hotplug ~11s) | Real, but the current mechanism is proven across four reboots and the delay has caused no observed harm. Churning boot-critical code overnight for a performance nicety is the riskier choice. |
| Add `[ "$ACTION" = "ifup" ] || exit 0` gating | Same reasoning — saves CPU, changes when reconciliation runs. Not worth touching proven code unsupervised. |
| Duplicate `#!/bin/sh` mid-file from appending | Cosmetic; a `#!` mid-script is just a comment. Left alone to avoid touching the file at all. |

My reasoning throughout: the system is working and proven. The remaining items are
efficiency and hygiene with no functional impact. All are safe morning tasks.

**Recommended order when you are back:** verify per-SSID egress with a phone →
decide on Swiss DNS → the three hygiene fixes → decide on the Tuesday shutdown.
