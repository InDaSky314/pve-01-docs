# 3.1 full tri-band network build (2026-08-10)

Executed after extensive planning in chat — this is the complete build:
GIOT/WALDO/Open-Fields tri-band (all 3 SSIDs on all 3 radio bands), a
dedicated wired-only Swiss VLAN, wholesale VLAN-to-tunnel routing with
individual-MAC-override priority, and a scoped Jellyfin cross-VLAN
firewall rule. Full backup taken first (`uci export` +
`sysupgrade -b /tmp/3.1-backup-pretribuild-20260810.tar.gz`).

## Final network table

| Network | VLAN ID | Gateway | Tunnel (wholesale default) | Members |
|---|---|---|---|---|
| main (lan) | 1 | 192.168.3.1 | none | 3.1's own admin network |
| **guest** *(functionally "Swiss" — see naming caveat)* | 9 | 192.168.9.1 | Swiss (wgclient1) | wired-only, no SSID |
| iot (Open-Fields) | 10 | 192.168.10.1 | none — bare WAN | SSID, tri-band |
| vlan11 (GIOT) | 11 | 192.168.11.1 | GIOT (ovpnclient1) | SSID, tri-band |
| vlan12 (WALDO) | 12 | 192.168.12.1 | WALDO (wgclient2) | SSID, tri-band |

**Naming caveat, important**: `set_fixed_subnet`'s API (confirmed by
reading `vlan_subnet.lua` directly) only supports renaming *custom*
subnets (`update_custom_subnet`) — `main`/`guest`/`iot` are hardcoded fixed
subnets whose `display_name` cannot be changed via the API at all. So
GL.iNet's own GUI will keep showing this network as "**Guest**" even
though its actual role is now the wired-only Swiss network. Not fixable
without recreating it as a custom subnet (which would mean a new VLAN ID,
losing the clean `192.168.9.0/24` match to 9.1's existing addressing —
not worth it). The planned network dashboard should show a friendly
override name here rather than trusting GL.iNet's own label.

## Root cause fixed: GIOT's split-brain SSID (the original German-internet bug)

GIOT was broadcasting identically-named SSIDs from **4 separate radio
instances** simultaneously: 3 WiFi7 MLO band members (`wlanmld2g/5g/6g`,
which share one virtual `mld0` device) plus 1 redundant legacy 6GHz-only
radio (`wifi6g`). Only `wifi6g` had `network=vlan11` in UCI — but a
*separately confirmed* deeper bug meant even that one's UCI setting was
never applied to the actual kernel bridge (`wifi6g`'s underlying `wlan2`
interface was still physically in `br-lan`, not `br-vlan11`, at the time
of the incident) — so **100% of live GIOT traffic was going out
unisolated on the main LAN/bare WAN**, confirmed via `curl
https://api.ipify.org` returning the bare German WAN IP instead of the
GIOT tunnel's egress IP.

Fix:
- `wlanmld2g`/`wlanmld5g`/`wlanmld6g`: `network` changed `lan` → `vlan11`.
- `wifi6g` (the redundant legacy radio): disabled entirely, eliminating
  the split-brain rather than trying to maintain two parallel broadcast
  paths for the same SSID.
- Applied via `wifi reload`. Verified via `bridge link show` (not just
  UCI) — `mld0` (GIOT's real live MLO device) now shows
  `master br-vlan11`. Re-confirmed tunnel health (all 3 tunnels still up)
  after the reload.

## Second bug found during the tri-band build: `mld1` (Open-Fields 6GHz) never auto-attaches on fresh bringup

While filling in the 6 previously-dormant wifi slots for WALDO/Open-Fields
tri-band, `wlanmldguest6g` (Open-Fields' 6GHz MLO member, sharing virtual
device `mld1`) came up at the radio level (`ip link show mld1` → UP) but
**was not attached to any bridge at all** — confirmed via `bridge link
show` showing no `master` for it, meaning a client on that specific link
would associate successfully but get zero network connectivity.

Root cause (inferred, consistent with observed behavior): `mld0` (GIOT)
was already a *live* MLO group before this build — reconfiguring an
already-running MLO group's `network=` field and reloading correctly
migrates its bridge membership. `mld1` was coming up for the **first
time** (its member interface `wlanmldguest6g` was previously fully
disabled) — a genuinely fresh MLO group bringup does not trigger the same
automatic bridge-attach behavior. A second `wifi reload` did not fix it
either; only a direct `ip link set dev mld1 master br-iot` did. Confirmed
this manual attach persists across a subsequent `wifi reload` (survives
reconfiguration once established) — but reboot-persistence is not
guaranteed the same way, since boot is itself a "fresh bringup," so it's
covered by the persistence script below regardless.

## Live-verified final tri-band map

| SSID | 2.4GHz | 5GHz | 6GHz |
|---|---|---|---|
| GIOT | `wlan02` (mld0) | `wlan12` (mld0) | `wlan22` (mld0) |
| WALDO | `wlan01` (guest2g) | `wlan11` (guest5g) | `wlan21` (guest6g) |
| Open-Fields | `wlan06` (iot2g) | `wlan16` (iot5g) | `wlan23` (mld1) |

All 8 hostapd instances confirmed live with correct SSID via `ubus call
hostapd.wlanNN get_status`; all corresponding bridge devices confirmed via
`bridge link show` (GIOT's 3 bands all ride the single `mld0` device;
Open-Fields' 6GHz rides `mld1`; WALDO has no MLO grouping, 3 independent
radios).

**Deferred, not done**: binding a physical wired port (LAN1) to the
Swiss/guest network for real. `vlan_subnet.set_fixed_subnet`'s `ifaces`
port-binding path (which calls into `libcable` for real hardware port
mapping) failed/timed out repeatedly rather than erroring cleanly — a
more fragile, hardware-dependent code path than the pure-software wireless
edits above. Since no cable is physically plugged into any LAN port right
now anyway (all showed `state: down`), deferred rather than risk repeated
calls into an unreliable hardware-level API. **Manual step needed via the
GUI when a cable is actually run**: Network → Ethernet Port → LAN1 →
Access Network → guest (Swiss).

## Wholesale VLAN-to-tunnel routing, all three networks, with correct priority

Extends the raw `ip rule` technique from earlier this session (see the
"Pushing past the gaps on purpose" section above) to all three tunneled
networks, and fixes the priority ordering per the owner's explicit
requirement: **an individually MAC-assigned device (via the normal GL.iNet
VPN Client GUI/API, `route_policy`'s per-MAC `ipset` rules at priority
6000) must always win over a network's wholesale default** — important
since wired clients get assigned to tunnels by MAC (same mechanism as
9.1), and could in principle share a VLAN with a wireless SSID.

```bash
ip rule add from 192.168.11.0/24 lookup 1011 priority 6100   # GIOT -> GIOT tunnel
ip rule add from 192.168.12.0/24 lookup 1002 priority 6100   # WALDO -> WALDO tunnel
ip rule add from 192.168.9.0/24  lookup 1001 priority 6100   # Swiss -> Swiss tunnel
```

(Table numbers: 1011 = `ovpnclient1`/GIOT, 1002 = `wgclient2`/WALDO,
1001 = `wgclient1`/Swiss — confirmed via `ip route show table all`.)

Priority 6100 is deliberately *after* the per-MAC rules at 6000, reversing
the earlier WALDO-only experiment (which was at 5900, ahead of the MAC
rules — wrong, since it would have silently overridden any individual
MAC assignment sharing that VLAN).

**Verified live, both directions**:
```
$ ip route get 8.8.8.8 from 192.168.11.50 iif br-vlan11
    -> dev ovpnclient1 table 1011                     (wholesale default: GIOT tunnel)
$ ip route get 8.8.8.8 from 192.168.12.50 iif br-vlan12
    -> dev wgclient2 table 1002                       (wholesale default: WALDO tunnel)
$ ip route get 8.8.8.8 from 192.168.9.50 iif br-guest
    -> dev wgclient1 table 1001                        (wholesale default: Swiss tunnel)
$ ip route get 8.8.8.8 from 192.168.11.77 iif br-vlan11 mark 0x2000
    -> dev wgclient2 table 1002 mark 0x2000            (individual MAC override to WALDO
                                                          tunnel WINS over GIOT's wholesale
                                                          default, exactly as required)
```

## Cross-VLAN Chromecast→Jellyfin firewall rule

Researched Jellyfin's actual documented ports first
(`https://jellyfin.org/docs/general/post-install/networking/`) rather than
guessing: streaming/API traffic is TCP 8096 (HTTP) / 8920 (HTTPS),
strictly client-initiated — the Chromecast (or the phone driving it)
connects *to* the server, never the reverse, so only a one-directional
rule is needed; return traffic rides the same allowed connection via
normal stateful firewall behavior. (Separately: the "auto-discover server"
feature, port 7359/UDP, is explicitly broadcast-based per Jellyfin's own
docs and won't cross a VLAN boundary — first-time manual server-address
entry will be needed in casting apps; not fixed here, flagged as optional
future work if it becomes annoying.)

Scoped to the two ports rather than a single host, since multiple Jellyfin
instances live on the Swiss subnet (production/VOD/live/NextPVR):

```
config rule
    option name 'GIOT-to-Swiss-Jellyfin'
    option src 'vlan11'
    option dest 'guest'
    option proto 'tcp'
    option dest_port '8096 8920'
    option target 'ACCEPT'

config rule
    option name 'WALDO-to-Swiss-Jellyfin'
    option src 'vlan12'
    option dest 'guest'
    option proto 'tcp'
    option dest_port '8096 8920'
    option target 'ACCEPT'
```

Confirmed live in the actual nftables ruleset post-reload (`nft list
ruleset | grep 8096`) — both rules present in `forward_vlan11`/
`forward_vlan12` chains, correctly jumping to `accept_to_guest`.

## Reboot persistence

New file: `/etc/hotplug.d/iface/99-network-buildout-persist` — fires on
every iface hotplug event (cheap, idempotent, safe to over-fire), fixes
two things that don't survive a reboot on their own:
1. Re-attaches `mld1` → `br-iot` if not already attached (the fresh-bringup
   MLO quirk above).
2. Re-adds the three wholesale `ip rule` entries if missing.

**Verified for real, not just deployed**: manually broke both (detached
`mld1`, deleted all three `ip rule` entries), fired the script by hand
(`ACTION=ifup INTERFACE=test sh /etc/hotplug.d/iface/99-network-buildout-persist`),
confirmed both were correctly restored via `bridge link show` / `ip rule
show`, with clean log lines in `logread` for each action taken. The
firewall rules themselves are normal UCI-committed config and don't need
this treatment — only the two things that live outside GL.iNet's own
config surface do.

## 9.1 reservations added (for the eventual Swiss VLAN device migration)

Added 3 missing static DHCP reservations on 9.1 to complete the 5-device
Swiss-tunnel (`media-core(ch)`) MAC list — 2 already existed
(media-core/.50, jellyfin-vod/.171). Cross-referenced against Proxmox
(`grep -i <mac> /etc/pve/*/*.conf`) to identify the devices, and against
`/usr/local/bin/dvr-dashboard`'s existing `LINKS` list to get the
*intended* IPs (the dashboard already expected jellyfin-live and
jellyfin-npvr at specific addresses — DHCP just hadn't caught up to it):

| CT | Service | MAC | IP |
|---|---|---|---|
| 113 | android-emulator | `BC:24:11:6C:21:D3` | 192.168.9.204 (matches its own hardcoded Proxmox static IP) |
| 112 | jellyfin-npvr | `BC:24:11:01:33:58` | 192.168.9.219 (matches dashboard's existing "Jellyfin - NextPVR" entry) |
| 110 | jellyfin-live | `BC:24:11:34:1C:E8` | 192.168.9.195 (matches dashboard's existing "Jellyfin - live (Threadfin)" entry) |

No dashboard edit needed — it already had the correct target IPs. When
CT112/CT110 next renew DHCP (or reboot), they'll pick up these reserved
addresses.

## 9.1 post-cutover subnet — decided, not yet applied

Owner's choice for 9.1's LAN after the physical cutover to 3.1 (once 3.1's
`guest`/Swiss VLAN takes over `192.168.9.0/24`): **`192.168.5.0/24`**.
Not applied yet — 9.1 is still live production on `192.168.9.0/24` today;
this is the target for whenever the actual physical relocation happens.

## Not yet built

- TiViMate → Swiss tunnel via Android per-app VPN split-tunneling (needs a
  separate Surfshark WireGuard device credential + the official WireGuard
  Android app's per-app restriction, done on the Chromecast/Google TV
  device itself — not something scriptable from the router side).
- Physical wired port → Swiss VLAN binding (deferred above, needs a real
  cable and a GUI click, or another attempt at the `set_fixed_subnet`
  ifaces API once its reliability is better understood).
- Network dashboard (architecture plan dispatched to agy in parallel with
  this build — see separate report/handoff once it lands).

## Native GL.iNet radio scheduling — confirmed via GUI (2026-08-10)

Corrects an earlier session finding ("radio scheduling — no native
feature") — that was wrong, or at least incomplete. Found via the SDK4
GUI directly (owner logged in, walked the pages):
**System → Scheduled Tasks** has a full per-band scheduling section,
confirmed live on 3.1 (v4.10.0):

- Separate cards for **MLO**, **6 GHz**, **5 GHz** (and presumably
  2.4 GHz further down, not screenshotted).
- Each card's "Wi-Fi Scheduled Mode" dropdown has exactly two options,
  matching `gl_timer_control_wifi`'s two functions found earlier via the
  binary/UCI:
  - **Turn On/Off** — separate "Enable Main Wi-Fi Schedule" and "Enable
    Guest Wi-Fi Schedule" toggles, each presumably opening day-of-week +
    on-time/off-time fields once enabled (not expanded/screenshotted to
    avoid changing live config).
  - **Switch TX Power** — scheduled power-level changes (Max/High/Medium/
    Low per the binary's usage string), same per-band structure.
- Backed by real UCI (`gl_timer.*` sections, e.g. `gl_timer.6gwifi` with
  `func`, `band`, `guest`, `turnon_hour/min`, `turnoff_hour/min`, `week`)
  — currently all `enable='0'`, nothing scheduled yet.

**Implication for the dashboard project**: don't build a custom
scheduler — wrap this existing, already-shipped mechanism instead. Phase 1
(agy building now) only *displays* current `gl_timer` state; the eventual
write-capable version should be a form that writes this same UCI schema
via the normal `uci set gl_timer.*` + reload path, not new logic.
