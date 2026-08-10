# GL.iNet router API/CLI runbook (2026-08-09)

How to drive GL.iNet's SDK4 web admin (the "oui" UI, seen on both the GL-MT6000/9.1
and GL-BE9300/3.1) programmatically, without the browser GUI — discovered live
against 3.1 while building its VPN tunnels this session. Verified against the
actual router, not assumed.

## The headline finding: `gl-session call` bypasses the whole web layer

The web UI's every action goes through a single endpoint, `POST /rpc`, with a
JSON-RPC 2.0 body shaped like:

```json
{"jsonrpc":"2.0","id":<n>,"method":"call","params":["<session_token>","<module>","<func>",<args_object>]}
```

That `<session_token>` normally has to come from a browser login. But the `/rpc`
handler is itself just a thin wrapper around one real ubus method,
**`gl-session call`** — and calling that method *directly* over SSH, as root,
needs **no session token at all**, because local root ubus access is already
fully trusted. Confirmed live:

```bash
ssh root@<router> "ubus call gl-session call '{\"module\":\"vpn-client\",\"func\":\"get_tunnel\",\"params\":{}}'"
```

This returns the exact same JSON the browser would get from `/rpc`, no login
flow, no cookies, no session juggling. **This is the actual CLI for this UI** —
every module/func pair captured from the browser (see catalog below) works the
same way, substituted into that one-liner. Mutations (add/remove/rename) are
presumed to follow the identical pattern (same dispatch, only reads verified
so far this session — see "Not yet verified" below).

General shape:

```bash
ssh root@<router> "ubus call gl-session call '{\"module\":\"<mod>\",\"func\":\"<func>\",\"params\":<json>}'"
```

## How this was found

1. Captured the browser's real traffic by monkey-patching `window.fetch`/`XMLHttpRequest`
   from the page's own JS console (via automation), logging every `/rpc` call's
   request+response to `window.__rpcCapture`.
2. Read back the captured `(module, func)` pairs and full payload shapes.
3. Noticed the ubus object list (`ubus list`) didn't include `vpn-client` etc.
   directly — those aren't real ubus objects, they're routed *through* one:
   `gl-session`, whose own method list (`ubus -v list gl-session`) includes
   `"call":{"module":"String","func":"String","params":"Table"}` — the exact
   shape of the web body's `params` array.
4. Tested calling it locally without the web session token at all. Worked.

## Known gotcha carried over from `docs/lessons-learned.md` / `router-rebuild-runbook.md`

**Tunnel *creation* must still go through the web UI** (or, now, through this
same `vpn-client.add_tunnel` call — see below — which is the identical code
path the UI itself uses, not a raw-UCI bypass). The 2026-07-25 incident that
produced that rule was specifically about hand-editing `/etc/config/route_policy`
directly with `uci set`, which skips the numeric `group_id`/`peer_id` + profile
file bookkeeping the UI (and this `gl-session call` path, since it's the *same*
backend call) performs automatically. Calling `vpn-client.add_tunnel` via
`gl-session call` is safe because it's the real handler, not a UCI shortcut —
only raw `uci` edits to `route_policy`/`wireguard`/`openvpn` configs bypass that
bookkeeping and are the actual danger.

## Method catalog (captured live on 3.1, 2026-08-09)

Modules seen so far, via the browser interceptor:

| Module | Purpose |
|---|---|
| `vpn-client` | VPN tunnel CRUD (the VPN Dashboard) |
| `wg-client` | WireGuard provider groups (VPN Client Profile page) — **hyphen** |
| `wg_client` | WireGuard provider *server list*/OTP/config generation — **underscore, different module from above** |
| `ovpn-client` | OpenVPN provider groups/configs |
| `clients` | LAN client list (device picker in tunnel wizard) |
| `tethering` | Tethering status (dashboard topology graphic) |
| `system` | Host status (dashboard topology graphic) |
| `tailscale` | Tailscale config/status/exit-node list |

### `vpn-client` — full CRUD, all confirmed live

**Read all tunnels:**
```bash
ubus call gl-session call '{"module":"vpn-client","func":"get_tunnel","params":{}}'
```
Returns `{default_tunnels:[...], tunnels:[...]}`. Each tunnel: `id` (UCI anon
section id), `tunnel_id` (numeric, used everywhere else), `name`, `enabled`,
`killswitch`, `options`, `via` (`{type, configs:[{group_id, id_list}], via:<iface>}`),
`from` (`{type:"mac", mac_list:[...]}` or `{type:"default"}`), `to` (`{type:"default"}` etc).

**Create a tunnel** (`vpn-client.add_tunnel`) — this is what the "Add New
Tunnel" wizard's Apply button calls:
```json
{
  "via": {"type": "wireguard", "configs": [{"group_id": 4557, "id_list": [1526]}]},
  "from": {"type": "mac", "mac_list": ["BC:24:11:59:1F:60", "..."]},
  "to": {"type": "default"},
  "name": "Tunnel 1"
}
```
`type` is `"wireguard"` or `"openvpn"`. `group_id`/`id_list` identify which
provider-profile server config to use (see `wg-client`/`ovpn-client` below for
how to find these). Response: `{"tunnel_id": <new numeric id>}`. New tunnels
land named `"Tunnel 1"` regardless of anything else and start **disabled** —
two more calls are needed (see `set_tunnel`) to actually turn it on and give
it a real name.

**Modify a tunnel** (`vpn-client.set_tunnel`) — minimal-diff, only send the
field(s) changing:
```bash
# Enable/connect
ubus call gl-session call '{"module":"vpn-client","func":"set_tunnel","params":{"tunnel_id":2430,"enabled":true}}'
# Rename
ubus call gl-session call '{"module":"vpn-client","func":"set_tunnel","params":{"tunnel_id":2430,"name":"Swiss (media-core)"}}'
```

**Delete a tunnel** (`vpn-client.remove_tunnel`):
```bash
ubus call gl-session call '{"module":"vpn-client","func":"remove_tunnel","params":{"tunnel_id":10}}'
```
Response: `{"status":"success"}`.

### Session/auth internals (for completeness — not needed for the SSH path above)

If ever driving the HTTP `/rpc` endpoint remotely (not via SSH) instead, the
real ubus object behind login is `session` (stock OpenWrt rpcd), not
`gl-session`:
```
'session' login: {"username":"String","password":"String","timeout":"Integer"}
```
`gl-session` (GL.iNet's own layer on top) additionally exposes `login`
(`username`+`hash`, not raw password — hashed client-side first), `challenge`,
`touch`, `status`, `logout`. The browser stores its resulting token in
`sessionStorage` under a device-specific key (seen as `9483C4B18437_be9300` on
3.1 — MAC-suffix + model). None of this matters for the SSH-local path, which
skips auth entirely.

## Not yet verified this session

- Whether `add_tunnel`/`set_tunnel`/`remove_tunnel` behave identically called
  via `gl-session call` over SSH vs. via the browser's `/rpc` — only *reads*
  (`get_tunnel`) have been tested through the SSH path so far. High confidence
  they match (same backend function either way) but not yet proven live.
- The exact `wg-client`/`ovpn-client`/`wg_client` method shapes for listing
  available provider servers and resolving a server name to its `group_id`/
  `id_list` pair (needed to build `add_tunnel`'s `via.configs` from scratch,
  e.g. for a brand-new provider/server not yet used anywhere) — captured the
  method *names* but not yet the full param/response shapes.
- Network/Wireless/DHCP modules (needed for the Open-Fields/GIOT/WALDO SSID
  and subnet build) — not yet explored, in progress.

## `vlan_subnet` module — custom VLAN/subnet CRUD (confirmed live, built GIOT+WALDO+Open-Fields with it)

Backing Lua source readable directly at `/rom/usr/lib/lua/gl/vlan_subnet.lua` —
reading source is faster than reverse-engineering from the GUI when you just
need the param names. Exported functions: `add_custom_subnet`,
`update_custom_subnet`, `remove_custom_subnet`, `get_subnets`, `list_all`,
`get_client_view`, `add_static_bind`/`set_static_bind`/`remove_static_bind`/
`get_static_bind_list` (DHCP reservations), `sync_port_map` (physical port
assignment), `get_vpn_interfaces`, `vlan_id_exists`.

**Create a custom subnet** — this is a *network*, separate from any wireless
SSID (see gap below):
```bash
ubus call gl-session call '{"module":"vlan_subnet","func":"add_custom_subnet","params":{
  "vlan_id":11,"gateway":"192.168.11.1","netmask":"255.255.255.0",
  "dhcp_enable":true,"dhcp_start_ip":"192.168.11.100","dhcp_end_ip":"192.168.11.249",
  "display_name":"GIOT"}}'
```
Response is `{"result":[]}` on success — **empty array, not an object with a
status field**. Don't mistake that for failure; verify with `uci show network`
(look for `network.vlan<N>` + a new `br-vlan<N>` device) or `get_subnets`.

Constraints found in source: `vlan_id` must be 9–4000 (1–8 reserved for
built-ins: main=1, guest=9, iot=10 on this router), max 20 custom subnets,
`display_name` ≤32 chars and must be unique, `ip`/`gateway` conflicts are
rejected, DHCP range can be given as absolute (`dhcp_start_ip`/`dhcp_end_ip`)
or offset-based (`dhcp_start`/`dhcp_limit`).

**Side effects confirmed in `uci show network` after creation:** a new
`network.vlan<N>` interface + `network.@device[]` bridge (`br-vlan<N>`), and
automatically in `uci show firewall`: a new zone (`forward='REJECT'` — see
isolation section below), a forwarding rule to `wan`, `Allow-DHCP`/`Allow-DNS`
rules, a `Block-LAN-mgmt-vlan<N>` rule, and one `<X>2wgclient1`-style
forwarding rule *per already-existing VPN client interface* — all wired up
automatically, no manual firewall editing needed.

## Wired port ↔ custom subnet binding — works, confirmed via GUI (not yet via direct call)

**Network → Ethernet Port → (click a port's gear icon)** opens a per-port
config dialog with an "Access Network" dropdown that lists *every* network
including custom ones — confirmed both `GIOT (11)` and `WALDO (12)` appear
there, selectable, alongside `main (1)`/`guest (9)`/`iot (10)`. This is the
answer for wiring pve-01's spare NIC or the UDR into a specific
subnet — no gap here, unlike the wireless-SSID side.

Did not click Apply (didn't want to reassign a port that might already be
serving something without knowing the real cabling plan first) so the
resulting UCI diff wasn't captured. But the likely backing call, from
source: `vlan_subnet.update_custom_subnet` takes an `ifaces` param (seen
referenced at `vlan_subnet.lua:3316`, `handle_port_binding(c, network,
params.ifaces, params.vlan_id)`) — port binding is very likely just another
field on the same update call, not a separate function. **Note:**
`vlan_subnet.sync_port_map` — despite the name — takes *no parameters* and
is just `post_subnet_change()` (an internal reconciliation trigger called
after other changes), not the actual port-assignment call itself. Don't be
misled by the name.

Ethernet Mode also offers **"Multiple VLANs"** (trunk, tagged) as an
alternative to "Standard" (single untagged network per port) — not explored
further this session, but exists if a single physical port ever needs to
carry more than one of these networks at once (e.g. a managed switch
downstream).

## Gap found: subnets and wireless SSIDs are separate concerns — CONFIRMED as a known GL.iNet firmware limitation, not something we missed

Web/GitHub research (2026-08-09, via a research subagent) independently confirmed this from two directions:

**1. GL.iNet's own official API schema has no such function.** An archived
copy of GL.iNet's SDK4.0 JSON-RPC schema (found bundled in the
[`tomtana/python-glinet`](https://github.com/tomtana/python-glinet) project,
raw file
[`api_description.json`](https://raw.githubusercontent.com/tomtana/python-glinet/main/pyglinet/api/api_description.json))
shows the `wifi` module has exactly four functions: `get_status`,
`get_config`, `set_config`, `set_txpower`. `set_config`'s `iface_name`
parameter is documented as "obtained from get interface" — i.e. it can only
target an **already-existing** interface (the hardcoded
`default_radio0`/`guest2g`/`guest5g`/`iot2g`/`iot5g`-style slots), never
create a new one bound to an arbitrary custom VLAN. (Schema is a stale 2022
snapshot — it also predates `vlan_subnet`, which we know exists on current
firmware — but combined with reading `wifi.lua` directly off the router
ourselves and the forum evidence below, the capability plainly doesn't exist
on any version.) GL.iNet's own developer docs site (`dev.gl-inet.com`) has
been offline for an extended period per their own forum
([thread](https://forum.gl-inet.com/t/where-is-the-documentation-of-the-api/44358)) —
this archived schema is the best available primary source.

**2. GL.iNet's forum confirms it's a known, acknowledged, unfixed limitation** — not
just undocumented, actively broken:
- ["Fix the firmware so adding new Wireless SSID works as expected"](https://forum.gl-inet.com/t/fix-the-firmware-so-adding-new-wireless-ssid-works-as-expected/61801) —
  GL.iNet staff ("Bruce") acknowledged multi-SSID/multi-radio support "doesn't
  work perfectly," speculated a real fix might land in "v5.0," no committed
  timeline.
- ["Can't setup new wireless SSID/access point on my VLAN"](https://forum.gl-inet.com/t/cant-setup-new-wireless-ssid-access-point-on-my-vlan/52777)
  (GL-MT6000/Flint 2, same firmware family as 9.1) — a LuCI-created SSID on a
  VLAN stayed "not associated" and disabled; GL.iNet staff offered no fix.
- ["Adding a custom wifi interface via LuCI requires manual UCI tweaks (GL-BE9300, 4.9.0)"](https://forum.gl-inet.com/t/adding-a-custom-wifi-interface-via-luci-requires-manual-uci-tweaks-gl-be9300-4-9-0/68901) —
  **the concrete failure mode**: GL.iNet's `qsdk-wifi` manager (the same ubus
  object we found is driver-level-only) **crashes** —
  `attempt to concatenate local 'ifname' (a nil value)` — on any LuCI-created
  `wifi-iface` unless three GL.iNet-specific UCI fields are added by hand:
  `init='1'`, `guest='1'`, and a correctly-sequenced `ifname` (e.g. `wlan03`).
  A moderator called it "a quirk of QSDK," pointed to SSH workarounds instead
  of a real fix.
- Two more threads
  ([1](https://forum.gl-inet.com/t/4-317-2-on-gl-ar750-does-not-preserve-new-wireless-interfaces-in-etc-config-wireless/45891),
  [2](https://forum.gl-inet.com/t/etc-config-wireless-getting-overwritten-on-reboot/32377))
  confirm hand-added `wifi-iface` sections get **dropped on reboot** by the
  SDK4 firmware layer — the exact same "GUI fights hand-edited UCI and wins"
  pattern already learned the hard way with `route_policy`/VPN config this
  session, not a wireless-specific exception. Editing *existing* interfaces
  (SSID/password/enabled) is fine and persists; creating *new* ones via raw
  UCI/LuCI is the unsafe part.

**Practical conclusion — the safe subset of "use LuCI":** don't create a
brand-new `wifi-iface` via LuCI (documented to crash `qsdk-wifi` and/or not
survive reboot). Instead: use the *already-existing*, SDK4-GUI-created
interfaces (which have the correct `init`/`guest`/`ifname` fields GL.iNet's
own onboarding flow sets), and edit **only their `network` field** via LuCI —
functionally identical to the already-proven-safe "edit MACs on an
already-UI-created tunnel" pattern from the VPN lesson. For GIOT, the
existing "GIOT" SSID (created earlier by GL.iNet's own onboarding, currently
on `main`) is exactly this case. For WALDO, no existing interface has that
name yet — the plan is to use SDK4's own "Add Guest Network" flow (still has
an unused 5GHz slot) to generate a *properly-formed* interface, then redirect
just its `network` field via LuCI the same way, rather than raw-creating one.

## Gap CLOSED — verified live recipe (2026-08-09)

Tested and confirmed working on 3.1: redirected the existing "GIOT" SSID
(already properly created by the SDK4 GUI, sitting on `lan`) to `vlan11` via
**LuCI → Network → Wireless → find the SSID → Edit → General Setup → Network
dropdown → uncheck old network, check the target `vlanN` → Save → Save &
Apply**. LuCI shows the real pending UCI diff before committing — for this
change it was exactly:
```
uci del wireless.wifi6g.hidden
uci set wireless.wifi6g.network='vlan11'
```
LuCI's built-in apply-with-rollback safety (a countdown timer that
auto-reverts if the router becomes unreachable) fired and completed
successfully — router stayed reachable the whole time.

**Verified after, not just trusted the "success" response** (per standing
discipline): `uci show wireless.wifi6g` on the router directly afterward
confirmed `network='vlan11'` persisted, and — critically — `init='1'` and
`ifname='wlan2'` were both still present (these are the exact fields the
forum thread warned are required or `qsdk-wifi` crashes on LuCI-created
interfaces). `ubus call qsdk-wifi status` returned `{"up": true}` —
confirmed **not crashed**. This works precisely because we edited an
*already-correctly-formed* interface (created originally by GL.iNet's own
onboarding, which sets `init`/`ifname` correctly) rather than raw-creating a
new one — exactly the safe subset predicted from the research findings above.

**The repeatable recipe**, for GIOT/WALDO/Open-Fields/any future custom
network: create the SSID first through the normal SDK4 GUI (any built-in
slot — Guest, IoT, etc. — it doesn't matter which, since we're only going to
redirect it), *then* go to LuCI and change only its Network field to the
target `vlanN`. Never use LuCI's own "Add" button to create a wifi-iface
from scratch — that's the path confirmed to risk a `qsdk-wifi` crash and/or
not surviving reboot.

**Not yet done**: a reboot-survival check (per the forum warning that new
interfaces specifically don't always survive reboot) — worth confirming
before fully trusting this for WALDO/production use. Should also confirm
whether the WMM Mode field seen in the edit dialog is a design.

## Gap found (superseded above, kept for context): subnets and wireless SSIDs are separate concerns

Creating a custom subnet via `vlan_subnet.add_custom_subnet` does **not**
create a broadcastable Wi-Fi network — it's IP/DHCP/firewall plumbing only,
reachable over wired ports (via `sync_port_map`) or an SSID bound to it
separately. The **Wireless** page's "Add" buttons are hardcoded to specific
built-in slots (Main/Guest/IoT) — there is no GUI-native "bind a new SSID to
an arbitrary custom VLAN" flow found this session. `wifi.lua`'s exported
functions (`connect`, `devices`, `get_channel_list`, `scan_trigger`, etc.)
don't include an obvious "create SSID on network X" call either — the real
SSID-creation RPC module/method wasn't identified before time ran out this
session. **Open item**: figure out the actual wifi-config module (the
Wireless page must call *something* — `qsdk-wifi` is a real ubus object,
separate from the `gl-session call` dispatch pattern, worth checking next)
to bind GIOT/WALDO SSIDs to `vlan11`/`vlan12` — or fall back to manually
editing `uci set wireless.<band>.network='vlan11'` directly (which, per the
lessons-learned precedent, risks the same UI/CLI divergence unless verified
carefully against a real GUI-driven example first).

## Inter-VLAN routing (confirmed via `uci show firewall`)

Every network gets its own firewall zone with **`forward='REJECT'` by
default** — full isolation from every other zone and from `lan`, with exactly
two categories of carve-out auto-added: `dest='wan'` (internet) and
`dest='<vpn client iface>'` (for every already-configured VPN tunnel — this
is the plumbing the "route this network through this VPN" tunnel feature
relies on; the actual traffic-steering decision is `vpn-client`'s per-tunnel
`from.mac_list` matching, not this firewall permission alone). There is no
built-in path from one custom VLAN to another, or to `lan` — by design, and
matches what you'd want for GIOT/WALDO/Open-Fields. To allow a specific
exception, add a `firewall.@forwarding[]` UCI section (`src=<zoneA>`,
`dest=<zoneB>`) — same CRUD pattern as everything else here, just not yet
exposed as its own module/func (may be plain `uci` + `/etc/init.d/firewall
reload`, or a `firewall`-prefixed module — not checked yet).

## TX power (found the knob, no GUI page for it yet)

Real, current values: `uci show wireless | grep txpower` →
`wifi0.txpower='9'`, `wifi1.txpower='9'`, `wifi2.txpower='30'` (one per
radio/band). The simplified "Modify SSID" dialog on the Wireless page doesn't
expose this — no advanced/per-radio power page found this session. Setting it
directly via `uci set wireless.wifi0.txpower=<dBm> && wifi reload` would work
in principle (same caveat as any raw-`uci` wireless edit — verify against a
real reboot/reconnect before trusting it, per standing discipline) but doing
it through whatever RPC module the (not-yet-found) real wifi-config page uses
would be safer, once found.

## Radio scheduling — no native feature

Checked **System → Scheduled Tasks**: only two built-in schedules exist,
**LED Display Schedule** and **Schedule Reboot** — no WiFi radio on/off
schedule. This would need a custom cron job (either on the router itself via
`/etc/crontabs/root`, or orchestrated from pve-01 over SSH) directly toggling
`wireless.wifi<N>.disabled` + `wifi reload` (or the real wifi RPC module's
enable/disable func, once identified) on a schedule — not a built-in.

## Incident (2026-08-10): 3.1 unreachable via LAN — mesh backhaul to 9.1 dropped

**Symptom:** 3.1 (`192.168.3.1`) became 100% unreachable from pve-01 overnight —
not just SSH, full packet loss. 9.1 and general internet stayed completely
healthy throughout.

**Root cause, isolated by elimination, not fully resolved as of this
writing:** 9.1's wireless mesh/backhaul link to 3.1 (`apcli0` on 9.1)
dropped association (`Access Point: 00:00:00:00:00:00`) and did not recover
on its own. 3.1 itself never rebooted (7-day uptime confirmed) and every
wireless radio/interface on 3.1's own side reports `up: true`/`disabled:
false` — the AP side looks healthy and waiting. The disconnect is one-sided:
9.1's *client* radio isn't reassociating, not 3.1's *AP* side being down.

**What it wasn't** — ruled out with real evidence, not assumption:
- **Not the LuCI wireless edit** done earlier (redirecting the "GIOT" SSID's
  `network` field to `vlan11`). The *live* broadcasting interface for GIOT is
  the MLO combo (`wlanmld2g`/`5g`/`6g` + `mld0`), confirmed via
  `ubus call network.wireless status` still showing `"network": ["lan"]`
  unchanged on that interface. The edit touched a separate, inactive
  legacy `wifi6g` section instead — cosmetically "changed" in UCI but not
  live/broadcasting, so it can't be what broke the mesh link.
- **Not a device crash/hang.** 7-day uptime, `qsdk-wifi` ubus status
  `{"up": true}`.
- **Not Astromesh.** Confirmed via the GUI itself — Astromesh on 3.1 is
  still showing the unconfigured first-run setup wizard (`gl-mesh` UCI
  config also shows `enabled='0'`, `onboard='0'`). The mesh link was never
  managed by GL.iNet's own mesh feature at all.
- **Not prplMesh either**, despite `uci show prplmesh` showing a config
  section on 3.1 (`enable='1'`, `management_mode='Multi-AP-Agent'`) — no
  `prplmesh` init script or running process exists on either router. The
  UCI section is just unused packaged defaults, not a live service.
- **Not fixed by a plain `wifi reload`** on 9.1 — tried, `apcli0` still shows
  no AP afterward.

**What the log shows:** 9.1's `logread` has repeated
`ap_peer_disassoc_action() ... ASSOC - 1 receive DIS-ASSOC request` entries,
recurring roughly hourly through the morning (04:36, 05:35, 05:54, 06:00) —
an ongoing retry-and-fail pattern, not a single clean break. Root cause of
*why* the disassociation keeps happening was not identified this session.

**What actually is managing this link:** genuinely not resolved. Neither of
GL.iNet's two documented mesh subsystems (Astromesh, prplMesh) is active.
`apcli0` doesn't appear in `uci show wireless` under any obviously-named
section on 9.1 (searched `sta`/`apcli`/`repeater`/`mesh` substrings, no
hits) — it may be dynamically created by a different, still-unidentified
mechanism. **Open item for next investigation.**

**The genuinely useful discovery — Tailscale as an out-of-band admin path:**
3.1 has its own independent wired WAN uplink (to 2.1, via `Ethernet 1`,
separate entirely from the wireless mesh to 9.1) and Tailscale running on
that path. When the LAN LAN link died, `ssh root@<3.1's-tailscale-IP>`
still worked perfectly — confirming the device itself was fine, isolating
the problem to specifically the wireless mesh, and providing a full
continued admin path throughout the outage. **Key gotcha:** the router's own
SSH key alias (e.g. `glinet-3.1` in `~/.ssh/config`) is bound to the LAN
IP/hostname — connecting via the Tailscale IP directly needs the identity
file spelled out explicitly:
```bash
ssh -i /root/.ssh/id_ed25519_routers -o IdentitiesOnly=yes root@<tailscale-ip>
```
(first connection also needs `-o StrictHostKeyChecking=accept-new`, since
it's a different host identity than the LAN-IP alias's known_hosts entry).

**Also confirmed:** the SDK4 web GUI is reachable the same way, on the same
port, just swap the IP: `http://<tailscale-ip>/` (main GUI, port 80) and
`http://<tailscale-ip>:8080/cgi-bin/luci` (LuCI) both work. **The browser
session does not carry over** between the LAN-IP origin and the Tailscale-IP
origin — expect to log in fresh (this is normal browser same-origin cookie
behavior, not a router-side restriction).

**On reaching 3.1 via the 1.1→2.1→3.1 physical path (the "backup path"
question):** tested directly — `192.168.2.1` (2.1 itself) is reachable from
pve-01 through that hop chain, but `192.168.2.241` (3.1's WAN-side IP,
sitting on 2.1's own LAN) is not. This is a plain NAT-boundary problem: 2.1
does NAT on its own WAN↔LAN boundary like any home router, so nothing
upstream of 2.1 can address anything on `192.168.2.x` without an explicit
port-forward configured **on 2.1** (external port → `192.168.2.241:22`) —
enabling "SSH Remote Access" on 3.1's own Security → Admin Access page
(confirmed already ON, unrestricted) only opens the *last* hop and doesn't
help, since nothing upstream can reach that door to knock on it in the first
place. Not pursued further this session since Tailscale already provides a
working path.

**Update — a second, genuinely independent physical path was found and
confirmed working, no port-forward needed:** 2.1 itself sits directly on
`192.168.2.x` (it's 2.1's own LAN), so SSH **ProxyJump through 2.1** reaches
3.1's WAN IP without any NAT/forwarding change at all — NAT only blocks
*unsolicited inbound* from outside 2.1's LAN, it doesn't block 2.1 (or an
SSH session already inside 2.1) from reaching its own LAN peers:
```bash
ssh -J glinet-2.1 -i /root/.ssh/id_ed25519_routers -o IdentitiesOnly=yes root@192.168.2.241
```
(needs `-o StrictHostKeyChecking=accept-new` on first connect — this IP has
its own separate known_hosts entry from both the LAN-IP alias and the
Tailscale-IP path, all three are the *same device* with three different host
identities as far as SSH is concerned.) Confirmed via matching uptime (7
days) against the already-verified Tailscale session. **This means 3.1 now
has three independent admin paths documented**: direct LAN IP (down during
this incident), Tailscale (works even when LAN is down, since it rides 3.1's
own separate WAN uplink), and this 2.1-jump physical path (also survives a
LAN-side outage, since it doesn't depend on the 9.1↔3.1 wireless mesh at
all — genuinely useful redundancy, not just a Tailscale workaround).

**Resolution (2026-08-10, ~06:50):** Owner manually reselected the target
SSID on 9.1's WiFi-as-WAN page in the GUI (Network → WAN → the wifi-as-WAN
interface). `apcli0`'s syslog shows a clean re-association immediately
after:

```
06:50:25 sta_mlme_assoc_req_action() ASSOC - Send ASSOC request...
06:50:25 LinkUp() !!! LINK UP !!! wdev(name=apcli0...)
06:50:25 netifd: Network device 'apcli0' link is up
06:50:28 kmwan: ... "interface": "wwan", "netdev": "apcli0",
         "force_ip": "192.168.10.185", tracks: [ping 1.1.1.1, 8.8.8.8, ...]
```

Zero disassoc entries since — the hourly retry-fail loop from overnight is
gone. **The true root mechanism behind the original disassociation was never
positively identified** (no crash, no config change, no Astromesh/prplMesh
involvement found); it's logged here as an open item in case it recurs. A
GUI-side SSID reselect is the confirmed recovery action if it does.

**Important nuance — this fix restored 9.1's backup internet uplink, not
admin access to 3.1.** `apcli0` is registered in GL.iNet's `kmwan`
(multi-WAN manager) as a NAT'd `wwan` interface — a client-mode WiFi
uplink for 9.1 to get *internet* via 3.1 as a failover path (metric 2,
behind the wired `eth1`→Ubiquiti-1.1 WAN at metric 1). It is **not** a LAN
bridge: `ip route` on 9.1 shows no route to `192.168.3.0/24` over `apcli0`,
by design. So this repair did not and could not affect reachability to
3.1's admin LAN IP (`192.168.3.1`) — that was never routed through this
link. Reachability to 3.1 for admin purposes depends only on the three
paths in the section above (LAN IP, Tailscale, 2.1-jump), all independent
of `apcli0`'s health. Re-confirmed all three still intact after this fix:
2.1-jump path and Tailscale both responded cleanly; direct LAN IP to 3.1
remains unreachable from pve-01 simply because pve-01 has no route to
`192.168.3.0/24` at all (expected — it was never on that subnet; access
there has always gone through the dedicated paths, not straight-line
routing).

**Takeaway for future incidents:** "the mesh reconnected" and "3.1 is
administratively reachable" are two separate facts on this topology — don't
conflate a WAN-uplink fix with an admin-access fix. Always verify the
specific path you actually need (LAN ping, `ssh glinet-3.1`, or one of the
documented alternates) rather than inferring it from an unrelated
interface's status.

## Verification discipline (per `lessons-learned.md` — still applies here)

Never trust a `{"result":...}` success response alone. Same rule as always:
compare real egress IP per tunnel against a bare-WAN reference, and confirm
the UCI-level state actually changed (`uci show route_policy` / `wireguard` /
`openvpn` on the router) before believing a change landed.

## `repeater` module — the real mechanism behind "WiFi as WAN" (found while closing the incident above)

Separate compiled ubus service, not a `gl-session`/Lua module — call it directly:
`ubus call repeater <method> '<json>'`. This is what both the GUI's WiFi-as-WAN
page and the 9.1 GIOT/apcli0 incident above actually ride on.

```
'repeater' methods:
  scan {cached:Bool}            — site survey
  surveys / disabled_bss / devices
  connect {ssid, key, network, protocol, remember, manual, macaddr, bssid,
           wds, netmask, gateway, dns, ip, mtu, hl, ttl, disguise,
           auto_portal, identity, hostname, hl}
  disconnect / reload / status / save_config
  enter_bare_mode {client_macaddr} / exit_bare_mode / set_exit {exit:Bool}
```

Live `status` captured on 9.1 right after the incident's GUI-side fix
(confirms the exact state a healthy link looks like):

```json
{
  "state_s": "connected", "state": 2, "running": true,
  "network": "wwan", "ssid": "Open-Fields", "bssid": "F2:8E:A5:8A:EA:19",
  "connected": "11m,2s", "signal": -58, "channel": 6, "htmode": "HE40",
  "ipv4": {"ip": "192.168.10.185/24", "gateway": "192.168.10.1",
           "dns": ["192.168.10.1"]},
  "config": {"ssid": "Open-Fields", "key": "goodlife", "remember": true,
             "manual": false, "auto_portal": false, "protocol": "dhcp"}
}
```

**`config.remember: true` matters** — it means this reconnect is saved to
UCI/flash, not just a live association. It will survive a reboot of 9.1
without needing to be re-clicked in the GUI. This resolves the earlier open
question of whether a fresh GUI reconnect would need to be redone after a
restart — it won't.

`repeater.devices` lists the physical radios available for wifi-as-WAN use
(band + `iface` + `phy`), useful for scripting a reconnect without the GUI
if this ever needs automating:
```json
{"devices": [
  {"sid": "mt798611", "band": "2g", "iface": "apcli0",  "phy": "ra0"},
  {"sid": "mt798612", "band": "5g", "iface": "apclix0", "phy": "rax0"}
]}
```

`repeater.status` on 3.1 itself (the AP side) is `"running": false,
"state_s": "idle"` — expected, since 3.1 uses its own wired WAN and never
acts as a wifi-as-WAN client.

## Other top-level ubus services found (catalog, not yet deep-dived)

Full `ubus list` on 3.1, minus `luci.*` and things already covered above:

| service | maps to GUI section | notes |
|---|---|---|
| `gl-clients` | Clients page | `status`/`list`/`get_speed`/`get_wan_speed`; live: `{"wireless_total":1,"cable_total":0,"auto_remove_offline":false}` |
| `gl-cloud` | Cloud Services (GoodCloud) | `bind`/`unbind`/`status`; live: `{"connected":"02:23:43"}` — 3.1 **is currently bound to GL.iNet's GoodCloud service**, worth a deliberate call on whether that's wanted on a homelab router (remote-management surface) rather than something to leave on by default |
| `gl-dpi` | Flow Control / bandwidth-by-app | `get_dpi_status`/`enable_dpi_func`/`enable_dpi_base_service`; live: `{"status":"0","lib_version":"20260226_01"}` — DPI lib present but function disabled (status 0) |
| `sms_manager` | Cellular SMS | `set_sms_log_level` only exposed; 3.1 has no cellular modem installed so mostly inert |
| `cellular.*` (9 objects) | Cellular/Multi-WAN failover | not applicable — no modem in either router |
| `container` | (LXC/Docker support, SDK4 feature) | not yet explored |

`gl-session`'s own Lua module set (`/usr/lib/lua/gl/*.lua` +
`/rom/usr/lib/lua/gl/*.lua`) is the smaller, complete list:
`common`, `kmwan`, `reset_network_utils`, `validator`, `vlan_subnet`,
`vpn_client`, `vpn_err_code`, `wg_client`, `wifi` — i.e. everything under
`gl-session call` is one of these eight; `vlan_subnet` and `vpn_client` are
the two already fully documented above. `kmwan.lua` is GL.iNet's multi-WAN
manager (governs the `eth1` vs `apcli0` metric-1-vs-2 failover seen in the
incident above) and `wifi.lua` is the module confirmed too limited to
create new SSID-to-VLAN bindings (see the gap section above) — neither
deep-dived further this session.

**Flagged, not acted on:** the GoodCloud binding (`gl-cloud status` →
`connected`) means 3.1 has an active outbound connection to GL.iNet's cloud
service for remote management. Worth a deliberate decision with the owner
on whether to keep, since it's a standing remote-admin surface on top of
the router's own LAN/Tailscale access — flagging here rather than touching
it, since disabling it is a settings change on shared infra.
