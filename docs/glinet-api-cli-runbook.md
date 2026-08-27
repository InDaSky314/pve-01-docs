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

## Pushing past the gaps on purpose (2026-08-10) — associating a whole VLAN/SSID with a VPN tunnel

Explicitly requested: try to bind an entire VLAN (and by extension whatever SSID
ends up on it) to a specific VPN tunnel wholesale, rather than GL.iNet's GUI-only
per-device MAC list — via CLI/LuCI, even where there's no GUI option, specifically
to characterize the limitation rather than just read about it. Backed up
`route_policy` (`uci export route_policy`) and took a full `sysupgrade -b` config
backup before touching anything; all changes below were additive/reversible and
the three production tunnels (Swiss, WALDO, GIOT) were re-verified intact
afterward.

**Attempt 1 — ask GL.iNet's own `route_policy` engine to do it (failed cleanly, no
crash, just a silent no-op).** The existing UCI schema already has a `from_type`
field with more than one observed value: `ipset` (what every real tunnel rule
uses — a MAC-list-backed ipset), plus `device` and `process_gid` seen on
GL.iNet's own built-in default/system rules (`@default[0]`, `gl_process`,
`gl_process_vpn`). Since `device` sounded like exactly "a whole
interface/VLAN," a new test rule was added (`from_type='device'`, `from='vlan12'`,
`via='wgclient2'`, its own unused `tunnel_id`/`mark`, additive — didn't touch the
existing rules) and applied via the correct path, `/usr/bin/rtp2.sh apply`
(per the standing lesson — never `/etc/init.d/network reload`).

Result: **UCI accepted it uncritically (no schema validation at that layer), and
`rtp2.sh apply` exited 0 with zero errors logged anywhere — but produced no
effect at all.** No ipset created, no `ip rule` entry, nothing in `nft list
ruleset` referencing the new mark. **`from_type=device`/`process_gid` are
reserved for GL.iNet's own hardcoded internal rules and are not wired into the
apply logic for user-created ones** — only `ipset` (i.e. only the MAC-list path)
actually does anything when a user (or the API) creates a rule. This is the
precise, verified shape of the limitation: not a crash, not a validation error,
just a silent no-op — the same "fails quietly, not loudly" pattern already seen
elsewhere in this stack. Test rule removed and `rtp2.sh apply` re-run to confirm
clean state after.

**Attempt 2 — bypass GL.iNet's abstraction entirely, raw Linux policy routing
(worked).** Every VPN client already gets its own real routing table
(`ip route show table all`, e.g. WALDO's `wgclient2` → table `1002`, with its own
`default dev wgclient2`). GL.iNet's per-MAC rules are just `ip rule` entries
pointing `fwmark X/0xf000 → lookup <table>` at priority `6000`. Nothing stops a
plain **source-subnet-based** `ip rule` from pointing at the same table, with no
mark/ipset/MAC list involved at all:

```bash
ip rule add from 192.168.12.0/24 lookup 1002 priority 5900
```

Verified with `ip route get 8.8.8.8 from 192.168.12.50 iif br-vlan12`:
**before** the rule, this returned `RTNETLINK answers: Invalid argument` (see
next paragraph for why); **after**, it cleanly resolved to
`dev wgclient2 table 1002` — i.e. **every device on vlan12 would now ride
WALDO's WireGuard tunnel automatically, with zero per-device configuration**,
which is exactly the capability the GUI doesn't expose. Confirmed the rule
survives a real `rtp2.sh apply` run (GL.iNet's script only manages its own
rules, doesn't flush/rebuild the whole table) — so it's safe from being wiped
by *other* future tunnel/VLAN edits made through the normal GUI/API.

**Bonus finding, previously undocumented — vlan11/vlan12/iot/guest fail CLOSED
by default, not open:** `ip rule show` on 3.1 has, at priority `9920`:
`from all iif br-vlan12 blackhole` (and the same for `br-vlan11`, `br-iot`,
`br-guest`). This is *why* the "before" `ip route get` above errored instead of
returning a normal WAN route: **any device on these networks with no matching
higher-priority rule (i.e. not yet assigned to a tunnel) gets its traffic
silently blackholed, not leaked to the bare WAN.** This is the opposite of the
`from_mac`-as-scalar failure mode documented in [[pve01-glinet-ui-vpn-sync]]
(which was about 9.1, a different router/firmware moment, and was a real
fail-open bug) — 3.1's current config for these specific subnets is fail-closed
by design. Worth knowing either way before assuming a new VLAN's devices get
default internet like normal LAN would.

**Caveats before relying on this for real:**
- The `ip rule add` above is **runtime-only** — it does not survive a reboot or
  a full `/etc/init.d/network` restart. It's not written into any UCI config or
  init/hotplug script. Making it permanent would mean adding a small custom
  hotplug script (e.g. `/etc/hotplug.d/iface/`) — a standing boot-time change,
  deliberately **not** done unilaterally this session since that's a persistent
  config decision, not a reversible experiment. Left as a live, working,
  easily-removed (`ip rule del from 192.168.12.0/24 lookup 1002 priority 5900`)
  proof-of-concept — say the word if you want it made permanent.
- No live client has actually ridden this path yet — WALDO's SSID still doesn't
  exist (same GUI gap as GIOT before it was closed), so this is proven at the
  routing-table level but not yet with real traffic. Once a wifi-iface gets
  redirected onto `vlan12` the same way GIOT's was, this is ready to test for
  real with a `curl ipify` comparison.
- This is real GL.iNet-abstraction-bypass territory — future GUI/API-driven
  tunnel or VLAN edits won't know this rule exists, won't remove it if the VLAN
  itself is later deleted, and won't show it anywhere in the GUI. If this
  becomes permanent, it needs to be documented wherever VLAN/tunnel lifecycle
  changes get made, or it'll become an invisible landmine.

## AstroMesh research (2026-08-10, via research subagent — see full report for citations)

Asked specifically: what AstroMesh actually is, whether 9.1 (GL-MT6000/Flint 2,
MediaTek) can get it despite not officially supporting it today, and whether
it's worth pursuing over the current manual WiFi-as-WAN bridge.

- **What it is**: GL.iNet's branded implementation of the Wi-Fi Alliance
  **EasyMesh** standard for the local unified-SSID/roaming layer, plus a
  separate proprietary remote-access layer (**AstroLink**, formerly
  "AstroWarp") that tunnels a traveling node back to the home network.
  Confirmed via a GL.iNet staff post, not just marketing copy.
- **Model support today**: public beta on exactly two models, both
  Qualcomm-based — **Flint 3 (GL-BE9300, i.e. 3.1)** and **Slate 7
  (GL-BE3600)**, firmware v4.10+. **9.1 (GL-MT6000/Flint 2, MediaTek) is
  explicitly on GL.iNet's own roadmap next** — a staff member named "Later
  August" (2026) as the target, attributing the delay to MediaTek "WiFi driver
  complexities," not a hardware wall. Given today is Aug 10, that window is
  imminent — check `https://dl.gl-inet.com/?model=mt6000` and the beta thread
  directly before doing anything else.
- **DIY porting to 9.1 now: not worth attempting.** No forum/GitHub evidence
  of anyone running AstroMesh or equivalent on MT6000 hardware. No standalone
  `gl-mesh`/`astromesh` package found in GL.iNet's public GitHub — consistent
  with it being baked into the Qualcomm (`qualcommax`) firmware image rather
  than something extractable and side-loadable onto MediaTek's completely
  different (`mediatek/filogic`) target and closed driver blobs. The rational
  move is waiting weeks for the official build, not reverse-engineering it.
- **No documented UCI schema.** 3.1 already has a `gl-mesh` config section
  (`enabled='0'`, untouched, still on the first-run wizard) but nothing
  publicly documents what a configured version should look like — GL.iNet's
  own guide is GUI-wizard-only (Admin Panel → ASTROMESH → pairing
  code/Wi-Fi scan). Not recommended to hand-edit this given zero confirmed
  persistence/rollback behavior.
- **Known beta gotchas (official)**: Router mode only (no AP mode); **LuCI is
  disabled on a node once it's in Mesh Node mode**; enabling Tailscale or
  ZeroTier disables Astro Node mode (conflicts with AstroLink's own tunnel);
  mobile app doesn't support it yet; max 8 nodes, 1 Main Router.
- **Verdict**: real upgrade once both ends support it (genuine unified
  SSID/roaming vs. today's WISP-style bridge), but 9.1 isn't there yet through
  no fault of ours — check GL.iNet's rollout before considering hardware
  replacement (a second Flint 3 would be the like-for-like path if MediaTek
  support slips indefinitely).

---

## Session addendum (2026-08-27) — per-SSID VPN binding, and the `clients` module

### `route_policy` `from_type` taxonomy — the thing that makes per-SSID steering work

A tunnel's "From" selector maps onto exactly one `from_type` in
`/etc/config/route_policy`. This is the whole mechanism behind "which SSID goes
out which tunnel":

| GUI wizard tab | `from_type` | `from` value | Matches on |
|---|---|---|---|
| All Clients | `default` | — | everything unmatched |
| Specified Connection Types | `interface` | network name (`guest`, `iot`, `lan`) | `iifname "br-<net>"` |
| Specified Devices | `ipset` | `src_mac<tunnel_id>` | `ether saddr @src_mac<id>` |
| Exclude Specified Devices | `ipset` (negated) | as above | inverted match |

**GUI navigation aid:** VPN → VPN Dashboard → click the **"From"** area of a
tunnel card (not the gear icon) → the 3-step wizard opens on step 2 →
tab across to **"Specified Connection Types"** → tick the network → **Apply All
Changes**. The card then reads `From: 1 Connection Type`.
The VPN Dashboard page is **slow to render** — allow ~10s after navigation and
after Apply before screenshotting or clicking, or you will act on a stale frame.

### How the rules actually compose (verified live on 3.1, 2026-08-27)

All rules land in one nft chain and are evaluated **in order, first match wins**,
each gated on `meta mark & 0x0000f000 == 0`:

```
897: ether saddr @src_mac2430           -> mark 0x1000   (MAC rule, Swiss)
902: iifname "br-iot"                   -> mark 0x2000   (interface rule, WALDO)
907: iifname "br-guest"                 -> mark 0xa000   (interface rule, GIOT)
912: (catch-all)                        -> mark 0x8000   (novpn)
```

Then `ip rule` priority 6000 maps mark → table → tunnel interface.

Two consequences worth knowing:

1. **MAC rules and interface rules coexist fine.** They are separate
   `route_policy` rules in the same chain. A MAC rule sitting *above* an
   interface rule wins for that device on *any* network — which is exactly what
   you want for "this box always exits via Swiss no matter where it plugs in".
   Rule order = UCI section order, so put the narrow MAC rules first.
2. **One `from_type` per tunnel, and one tunnel per rule.** Each rule carries its
   own `tunnel_id` and its own `/etc/vpn_profiles.d/profile<tunnel_id>` file.
   You therefore **cannot** have a single tunnel serve both an SSID *and* a MAC
   list. Wanting both against the same VPN endpoint means creating a **second
   tunnel instance** to the same server. Use `vpn-client.add_tunnel` (see above)
   — that is the UI's own handler and does the `group_id`/`peer_id`/profile
   bookkeeping correctly.

**Concurrency is not the limit:** 9.1 runs `wgclient1`+`wgclient2`+`wgclient3`
simultaneously, and 3.1 runs `wgclient1`+`wgclient2`+`ovpnclient1`. Multiple
WireGuard *and* OpenVPN client instances coexist on 4.10.0.

### `clients` module — friendly names (and a trap that cost real time)

Discovered by probing `gl-session call` with candidate func names; `clients` was
already in the module catalog above but its funcs were never enumerated.

```bash
# Read the list the GUI renders (name, mac, ip, online, ...)
ubus call gl-session call '{"module":"clients","func":"get_list","params":{}}'

# Rename a device — this is what the GUI's pencil icon calls
ubus call gl-session call '{"module":"clients","func":"set_info","params":{"mac":"BC:24:11:59:1F:60","name":"media-core"}}'
```

Confirmed funcs: `get_list`, `set_info`. **Not** present: `set_name`, `rename`,
`get_name`, `set_client`, `list`, `get_info` (all return `-32601 Method not found`).

`set_info` writes to **three** places at once — this is the bookkeeping you must
not skip:
- `/etc/config/gl-client` → `@client[].alias` (the durable friendly name)
- `/etc/config/dhcp` → a `config host` static reservation pinning the current IP
- `/etc/oui-tertf/client.db` → the `name` column

**TRAP — same class as the raw-UCI trap above.** `/etc/oui-tertf/client.db` is a
*derived cache*. Writing names into it directly with `sqlite3` appears to work
and even survives a few minutes, but `ubus call gl-clients sync` silently
reverts the row to the DHCP-reported hostname. Renames **must** go through
`clients.set_info`. Symptom when you get this wrong: the name is right in the db
right after the write, and blank again after the next sync or reboot.

Note also that the GUI displays the **alias**, while `client.db.name` holds the
raw DHCP hostname — so a device can legitimately show a name in the GUI while
`client.db.name` is empty. Verify renames with `clients.get_list`, not with
sqlite.

Offline devices are absent from `get_list` output entirely, so a rename applied
to a powered-down host cannot be verified until it comes back up; the alias in
`/etc/config/gl-client` is the proof in the meantime.

### Proxmox CT/VM MAC map (pve-01, for the Swiss/media rules)

| MAC | CT/VM | Name |
|---|---|---|
| `BC:24:11:59:1F:60` | CT 105 | media-core |
| `BC:24:11:EF:79:09` | CT 107 | log-server |
| `BC:24:11:28:55:77` | CT 108 | scraper |
| `BC:24:11:34:1C:E8` | CT 110 | jellyfin-live |
| `BC:24:11:9A:ED:DB` | CT 111 | jellyfin-vod |
| `BC:24:11:01:33:58` | CT 112 | jellyfin-npvr |
| `BC:24:11:6C:21:D3` | CT 113 | android-emulator |
| `BC:24:11:18:0E:A7` | VM 104 | SRV-STD-2022 |
| `52:9F:12:A3:47:63` | VM 102 | WIN11 |
| `7C:2B:E1:13:DE:30` | host | pve-01 (`enp2s0`/`vmbr0`) |

Regenerate with:
```bash
for c in $(pct list | awk 'NR>1{print $1}'); do
  printf "%-5s %-20s %s\n" "$c" "$(pct config $c | sed -n 's/^hostname: //p')" \
    "$(pct config $c | grep -oiE 'hwaddr=[0-9A-F:]+' | cut -d= -f2)"
done
```

### `wifi` module — radios and SSIDs (3.1, 2026-08-27)

The module is **`wifi`**, not `wireless` (that name returns `Method not found`).

```bash
ubus call gl-session call '{"module":"wifi","func":"get_status","params":{}}'
ubus call gl-session call '{"module":"wifi","func":"get_config","params":{}}'
```

`get_config` returns `{dfs_support, bandmode, res:[radio,...]}`; each radio has
`device` (`wifi0`/`wifi1`/`wifi2`), `band` (`2G`/`5G`/`6G`), `htmode`, `txpower`,
`channels[]` (with `psc` and `dfs` flags) and `ifaces[]`. Note the radio object
carries **no `enabled` field** — per-radio enable lives only in
`/etc/config/wireless` as `wireless.wifi<N>.disabled`. `set_config` exists but
its payload shape was not captured; the Wireless GUI page is the safer editor.

**GUI caution:** the Wireless page on 3.1 hangs on a "Processing, please wait…"
modal that blocks scrolling and clicks — it did not clear across a reload and
two 10s waits. Fall back to `gl-session call` / SSH when that happens.

### 6 GHz on the BE9300 does not come up from `wireless.wifi2.disabled=0` alone

Attempted 2026-08-27. Setting `wireless.wifi2.disabled='0'` + `wifi reload`
brings the *radio* up and is harmless — 2.4 GHz and 5 GHz were re-verified
unchanged and correctly bridged afterwards — but **no 6 GHz AP appears**:

- `logread`: `Wireless device 'wifi2' is now up`
- `ubus call network.wireless status`: `wifi2 up=True pending=False`, listing
  `wifi6g` / `wlanmld6g` / `wlanmldguest6g` all with `disabled=False`
- but **no `wlan2*` netdev is created** and **no `/var/run/hostapd-wlan2*.conf`
  is generated** — only the seven 2.4/5 GHz configs exist

So netifd reports success while `qca-wifi-configurator` never instantiates the
VAPs. Same family as the earlier hostapd-drift incident on this box: netifd's
view is not evidence that a VAP exists. **Verify 6 GHz with `iwinfo` /
`/var/run/hostapd-*.conf`, never with `network.wireless status`.**

Unresolved. Prime suspect is MLO: `mld-phy0` also advertises 28 6 GHz channels
and `mld0` is already a member of `br-lan`, while both MLO toggles in the GUI
are **off** — so the 6 GHz chain may be claimed by the MLD phy and only
reachable by enabling MLO. Next step is the GUI's
"2.4 GHz + 5 GHz + 6 GHz" MLO toggle (needs the hung Wireless page working),
or a reboot — note a reboot previously killed 3.1's Tailscale, so do it with
someone on site.

`wireless.wifi2.disabled='0'` was left in place: it is the intended end state,
costs nothing while no VAP exists, and may resolve on the next clean boot.

### Latent misconfiguration found while investigating

`wlanmldguest6g` is configured with **`ssid='Open-Fields'` but `network='iot'`**.
Every other Open-Fields VAP is on `lan`. IoT is bound to the German WireGuard
tunnel, so if that VAP ever instantiates, a client joining "Open-Fields" on
6 GHz would silently egress through Frankfurt instead of natively. It is inert
today only because no 6 GHz VAP comes up. **Fix this before enabling MLO/6 GHz.**

### Verified: adding a tunnel via the UI handler (2026-08-27)

Full working sequence, `add_tunnel` → `set_tunnel` → verify, on 3.1:

```bash
# 1. create (lands disabled, named whatever you pass)
ubus call gl-session call '{"module":"vpn-client","func":"add_tunnel","params":{
  "via":{"type":"wireguard","configs":[{"group_id":4557,"id_list":[1504]}]},
  "from":{"type":"mac","mac_list":["BC:24:11:28:55:77"]},
  "to":{"type":"default"},"name":"US-Ashburn (WG)"}}'
# -> {"tunnel_id": 3742}

# 2. enable it
ubus call gl-session call '{"module":"vpn-client","func":"set_tunnel","params":{"tunnel_id":3742,"enabled":true}}'

# 3. verify the EXIT, not the config
curl -s --interface wgclient3 https://api.ipify.org
```

The firmware allocated `wgclient3` automatically and the rule appeared as
`route_policy.@rule[3]` with its own `profile3742`. Surfshark WireGuard peer ids
are stable in `uci show wireguard` (`peer_1500` New York … `peer_1504` Ashburn …
`peer_1524` Los Angeles), all under Surfshark `group_id=4557`.

**Always verify a tunnel by its exit IP.** All Surfshark peers hand out the same
client address `10.14.0.2/24`, which produces three same-priority
`from 10.14.0.2 lookup 100X` ip rules — the config alone cannot tell you which
exit you actually got. Confirmed live: wgclient1→Zürich, wgclient2→Frankfurt,
wgclient3→Ashburn, ovpnclient1→New York.

### RESOLVED (2026-08-27): 6 GHz needs a full reboot, not `wifi reload`

The 6 GHz failure documented above was **not** an MLO lock. `wireless.wifi2.disabled='0'`
is correct and sufficient — but `wifi reload` will not instantiate the 6 GHz VAPs.
Only a full `reboot` does. After the reboot, on the same config that had produced
nothing:

```
  wlan2    Open-Fields              br-lan     ch=5   5.975 GHz
  wlan22   Open-Fields              br-lan     ch=5   5.975 GHz   (MLO)
  wlan23   GL-BE9300-437-MLO-Guest  br-guest   ch=5   5.975 GHz   (MLO guest)
```
with `hostapd-wlan2.conf`, `hostapd-wlan22.conf`, `hostapd-wlan23.conf` all
generated. `wifi.get_status` then reports `wifi2 band=6g channel=5`.

**Rule for this box: a radio that is enabled in UCI but has no
`/var/run/hostapd-<vap>.conf` needs a reboot, not another reload.** Do not
conclude the radio is broken and do not go hunting for an MLO/regulatory cause
before rebooting once.

Reboot notes: plain `reboot` over SSH works (a previous `nohup sh -c "sleep 2;
reboot"` silently never fired). Down/up took ~80 seconds; SSH and Tailscale both
came back on their own. Prove it with `/proc/uptime`, never with "ping answers".

### The `wlanmldguest6g` fix was load-bearing — confirmed by the reboot

Before the reboot, `wlanmldguest6g` had `ssid='Open-Fields'` and `network='iot'`
while both its siblings had `ssid='GL-BE9300-437-MLO-Guest'` / `network='guest'`.
It was corrected to match. The reboot then **brought that VAP up as `wlan23`** —
so had it not been corrected, a VAP advertising **"Open-Fields" would now be
bridged to `br-iot` and egressing through the German WireGuard tunnel**, while
every other Open-Fields VAP exits natively. The leak would have been live.

Note the firmware reset `wlanmldguest6g.disabled` from `1` back to `0` across the
reboot — it manages MLO member enablement itself — but it **kept** the corrected
`ssid` and `network`. So disabling an MLO member is not durable; fixing its
network binding is.

**Generalisation:** after any SSID rotation on this box, diff every `wlanmld*`
section against its same-group siblings. The MLO sections are not visible in the
GUI's normal SSID list and silently keep whatever a bulk edit wrote into them.

### Stale `known_hosts` entries after a router swap

Post-reboot, `192.168.3.1` presented a key that did not match `known_hosts` and
SSH refused with the MITM warning. It was **not** an attack: the key answering
(`SHA256:Xixuagkw2Z/…`) was already the trusted entry for the *same host* under
its Tailscale IP `100.82.158.23`; the `192.168.3.1` line was stale from an
earlier device on that address. Resolve this by comparing against the host's
other known address before touching `known_hosts` — never by blindly accepting.
