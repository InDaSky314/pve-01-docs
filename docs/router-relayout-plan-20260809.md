# Router re-layout plan (2026-08-09) — research + recommendation, nothing executed

Planning only, per explicit instruction — no hardware moved, no router config changed.
Everything below is verified against the live devices via SSH (read-only) or measured
directly, not assumed. See `network-topology.md` for the base inventory this builds on.

---

## 1. IP addressing: re-IP 3.1 to take over `192.168.9.x`, don't touch pve-01

**Recommendation: re-IP 3.1's own LAN from `192.168.3.1` to `192.168.9.1`** (i.e., 3.1
takes over 9.1's current numbering), and drop 9.1's own routed subnet entirely once it's
switched into pure AP/bridge mode (see §2). Do **not** renumber pve-01, CT105, or anything
that already lives on `192.168.9.x`.

**Why:** measured the actual blast radius before proposing anything. At least 10
scripts/systemd units on pve-01 alone hardcode `192.168.9.x` literals (this doesn't count
the household's own devices — the real Chromecast, every saved Jellyfin server URL, DNS
entries, or the dozens of references throughout today's documentation). Migrating pve-01
to a new subnet would mean touching all of that, verifying nothing was missed, and fixing
whatever broke live in production. Re-IP'ing 3.1 instead touches exactly one device, once,
and everything downstream keeps working unmodified. This was the deciding factor, not
convenience — the two options are not close in risk once actually measured.

**Both 3.1 and 9.1 (same GL.iNet firmware family) confirmed to support switching into pure
AP/bridge mode** (`gl-sdk4-ui-bridge`, `ip-bridge` present on 3.1). This matters because it
resolves the awkward part of the plan cleanly: once 9.1 no longer needs to be a *router*
(it's just a wired hub for the Streamer/LG TV per your original ask), it doesn't need its
own subnet or DHCP server at all — bridge mode extends 3.1's single `192.168.9.x` network
straight onto 9.1's wired ports and its own WiFi radios, with 9.1 keeping only a single
management IP (e.g. `192.168.9.2`) rather than its own routed identity. No double-NAT, no
second DHCP server to keep in sync, no renumbering of the 11 devices currently leased on 9.1
— they just start getting their addresses from 3.1 instead, same subnet, same IPs if using
DHCP reservations (would need re-adding on 3.1 — the one real piece of manual work in this
whole plan).

**Sequencing implication:** this needs the DSL PPPoE credentials to actually move from 2.1
to 3.1 as part of the same change (2.1's current role, per `network-topology.md`, is
"role TBD" in the new layout) — that's the one step that actually can't be done gradually,
everything else here can be staged.

---

## 2. VPN tunnel architecture for 3.1

**Reuse the existing Surfshark account** already configured on 9.1 (same provider, same
credentials already stored in 9.1's WireGuard/OpenVPN client config) — no new subscription
needed, 3.1 just needs its own client tunnels set up against the same account, the same way
9.1's own three tunnels already are.

**Confirmed exactly what 9.1 already runs**, checked live via `uci show wireguard` +
`wg show all endpoints`, not assumed from memory:

| 9.1's tunnel | Interface | Real endpoint | Country |
|---|---|---|---|
| "media-core(ch)" | `wgclient1` | `ch-zur.prod.surfshark.com` | **Switzerland (Zurich)** |
| "Tunnel 1" | `wgclient2` | `us-buf.prod.surfshark.com` | US (Buffalo, NY) |
| "Primary Tunnel" | `wgclient3` | `us-ltm.prod.surfshark.com` | US |

**This directly answers one of your asks**: 9.1's existing "media-core(ch)" tunnel *is*
already the Swiss WireGuard tunnel, already proven working with the household's IPTV
stream. Replicating "the same VPN tunnels as 9.1" on 3.1 means standing up an equivalent
`ch-zur.prod.surfshark.com` WireGuard client on 3.1 — same server, new tunnel instance,
same account.

### New US OpenVPN UDP tunnel — measured, not guessed

Pinged the 7 US East Coast Surfshark candidates directly from pve-01:

| Server | City | Avg RTT |
|---|---|---|
| **`us-buf`** | Buffalo | **136.9 ms** |
| `us-nyc` | New York | 137.7 ms |
| `us-atl` | Atlanta | 141.9 ms |
| `us-ash` | Ashburn, VA | 145.4 ms |
| `us-clt` | Charlotte | 164.4 ms |
| `us-bos` | Boston | 172.9 ms |
| `us-mia` | Miami | 197.9 ms |

Buffalo and NYC are statistically tied (~1ms apart, within measurement noise) and both
meaningfully faster than Ashburn — worth calling out since Ashburn is usually the reflexive
"best" pick for US-East given its role as a major internet backbone hub, but it measured
8ms *slower* than both from this specific vantage point. Testing beat assumption here.

**Recommendation: `us-nyc.prod.surfshark.com` over UDP** for the new OpenVPN tunnel — not
`us-buf`, even though it measured marginally faster, specifically *because* Buffalo is
already in use as 9.1's "Tunnel 1." Using a different city for the new tunnel means the two
US exits aren't both riding through the same single Surfshark PoP, which is worth a little
diversity for basically free given the two options are functionally tied on latency.

---

## 3. SSID-to-tunnel mapping on 3.1

Reflecting your stated intent back against what's actually configured today:

| SSID | Segment/network | Routing |
|---|---|---|
| Ubiquiti (1.1) + its IOT network | Whole-house (1.1's own mesh) | **Direct German internet, no VPN** |
| GIOT (3.1's own IoT-labeled SSID) | 3.1-local only | New US OpenVPN UDP tunnel (`us-nyc`, per above) |
| "big-gl" / 3.1's main SSID | 3.1-local only | Swiss WireGuard (`ch-zur`) — **IPTV stream uses this** |
| WALDO (new SSID, doesn't exist yet) | 3.1-local only | US WireGuard — reuse `us-buf` (matches 9.1's existing "Tunnel 1") rather than standing up a fourth Surfshark tunnel for no real benefit |

One naming note worth flagging: 3.1's actual configured SSID text is `Big-Big-GL` (checked
live), not literally "big-gl" — I read "big-gl" as your shorthand for it, consistent with
how 9.1 gets called `big-gl` elsewhere (that's genuinely 9.1's Tailscale hostname, a
different device). Worth double-checking this is the SSID you meant before anything gets
built, since the names are close enough to genuinely mix up.

---

## 4. Where to actually put the smart-home / true IOT devices

**Recommendation: leave them exactly where they are — Ubiquiti's IOT network.** Don't move
them as part of this re-layout.

**Why:** the deciding factor is physical coverage, not VPN routing. Ubiquiti's mesh is the
only system with satellite coverage on every floor — 3.1 is a single access point, its own
radio range doesn't reach the whole house. Since the plan already routes Ubiquiti's network
as plain German passthrough with no VPN at all, that's also the *simplest possible* segment
for local smart-home control (no tunnel in the path to reason about for local automation
reachability at all) — the physical-coverage answer and the simplicity answer point the
same direction, so there's no real tradeoff to weigh here. The Shelly plugs recommended
earlier (see `home-network-power-automation.md`) specifically need direct local reachability
from pve-01's own automation — keeping them off any VPN-routed segment matters for that,
even though policy-based VPN client routing on this GL.iNet setup generally only affects
WAN-bound traffic (LAN-to-LAN traffic between devices on the same subnet typically doesn't
transit the tunnel at all) — simplest to just not have to reason about that distinction for
anything safety/automation-critical.

GIOT and WALDO (the VPN-routed SSIDs on 3.1) are better suited for things that specifically
*want* a non-German exit — geo-restricted apps/services, testing, etc. — not physical smart
devices.

---

## Open items before this gets built

1. **Confirm "big-gl" naming** (§3) — is that 3.1's SSID or 9.1's hostname? Cheap to get
   wrong, easy to confirm first.
2. **2.1's fate** — still genuinely undecided (`network-topology.md`'s own open item).
   Retiring it vs. repurposing it doesn't block this plan, but worth deciding before the
   DSL credentials actually move.
3. **9.1's 11 current DHCP reservations** would need manually re-adding on 3.1 once it takes
   over `192.168.9.x` — the one piece of real manual work in this whole plan, not something
   that migrates automatically with a bridge-mode switch.
4. Nothing here has been applied. This is the plan to execute whenever you're ready — happy
   to sequence it into concrete steps at that point.
