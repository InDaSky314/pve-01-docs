# Home network topology, power automation plan, and Tailscale investigation

**Status: planning + infrastructure setup done, power automation not yet built, Tailscale mystery unresolved.**
Started 2026-08-08, branched off the Wholphin/DVR work into its own thread. This doc exists so none
of tonight's network investigation has to be re-derived — read this first before touching any of the
routers or the power-automation plan.

## Why this thread exists

Owner wants to (1) reduce WiFi RF exposure at night — partly out of genuine caution about long-term
low-level exposure (not fully resolved science, but a reasonable precaution — see the chat transcript
for the fuller honest take on the radiation question), partly for real, uncontested reasons: energy
savings and a smaller nightly attack-surface window. (2) Tie that into the sports-recording auto-extend
idea from the Wholphin thread, which needs to *hold power open* on specific segments when a tracked
game is still live, not just follow a fixed nightly schedule.

## Network topology (confirmed, not guessed)

```
DSL (Telekom PPPoE)
  └─ 192.168.2.1  GL-MT2500 ("2.1") — dedicated PPPoE gateway, boots fast
       ├─ 192.168.1.1  UniFi UDR "R2D2" ("1.1") — different creds, MFA on the cloud account,
       │                slow to boot (why it was demoted from primary-gateway duty)
       │    UniFi fleet behind it (7 devices total, see below)
       └─ 192.168.3.1  GL-BE9300 ("3.1") — wireless mesh node
            └─ 192.168.9.1  GL-MT6000 ("9.1", "our" router) — reaches 3.1 wirelessly ("GIOT"
                 │           network) OR reaches 1.1 via a wired/powerline path as fallback WAN
                 └─ 192.168.9.0/24 — pve-01 and everything from the other doc lives here
```

### UniFi fleet (behind 1.1), by floor

| Device | Model | Uplink | Floor |
|---|---|---|---|
| R2D2 | UDR | wired (to 2.1) | — gateway, same room as 2.1/3.1 |
| Basement-Express | UniFi Express (UX) | wired (powerline) | Basement |
| Mid-Express | UniFi Express (UX) | **true wireless mesh** | Kids' floor |
| AC-Lite | U7LT (AP) | wired (powerline) | Kids' floor (same as Mid-Express) |
| U6 Lite | UAL6 (AP) | wired (powerline) | Top floor (owner + spouse) |
| USW-Flex-Mini-DMARC | switch | wired | — |
| USW-Flex-Mini-Finley | switch | wired, own subnet `192.168.8.x` | Finley's room — has its own VLAN/segment, not yet investigated further |

Masonry construction rules out running new ethernet between floors — powerline adapters bridge
everything that isn't true wireless mesh (only Mid-Express uses real mesh).

**All four remote-floor devices (Basement-Express, Mid-Express, AC-Lite, U6 Lite) already sit on
their own individual dumb mechanical 24-hour timers, on their own separate power outlets per floor.
Owner is fine leaving those exactly as they are — they are explicitly OUT of scope for the smart-plug
project below.** Only the two segments in the shared room are in scope.

### UniFi networks/VLANs (from `networkconf`)

| Name | Subnet | VLAN | Purpose |
|---|---|---|---|
| Default | `192.168.1.1/25` | none | Main flat network. **"IOT" is just an SSID label on this same network, not an isolated VLAN** — Nest Minis, the Chromecast when placed here, etc. all get plain `192.168.1.x` addresses, no isolation. |
| Guest | `192.168.3.1/24` | 3 | Real VLAN. (Confusing coincidence: same subnet numbering as the unrelated `3.1` GL-BE9300 router — different device, different broadcast domain, pure numbering coincidence.) |
| VPN | `192.168.4.1/24` | 4 | Real VLAN, routes through a VPN client tunnel. |
| Surf-Lath-UDP, AlbaniaWG, Ashburn, NYC | vpn-client tunnels | — | Per-tunnel VPN exit locations — this is how the household gets geo-appropriate IPs per streaming service (owner confirmed: e.g. The Office needs a German exit, Discovery+ needs a US exit). The Chromecast gets manually switched between these networks depending on what's being watched. |

## SSH access set up tonight (owner-authorized, owner provided all passwords live in chat — not
## repeated here, see the secure storage locations below)

- Dedicated key: `/root/.ssh/id_ed25519_routers` on pve-01 (ed25519, comment `pve01-router-mgmt`)
- SSH config aliases on pve-01 (`/root/.ssh/config`): `glinet-9.1`, `glinet-3.1`, `glinet-2.1`,
  `unifi-1.1` — all key-based, no more password needed for any of the four devices
- **LAN-side SSH restriction applied to all three GL-iNet routers** (UniFi intentionally left alone,
  see below): only pve-01 can reach SSH on `9.1`/`2.1`/`3.1` now, verified safe at every step (added
  the allow rule, verified pve-01 still had access, *then* added the block-everyone-else rule, verified
  again, confirmed a different LAN host — CT105 — was actually refused). Real observed source IPs
  differ per hop because of NAT:
  - `9.1` and `2.1`: see pve-01's real IP (`192.168.9.11`) directly — restricted to exactly that
  - `3.1`: sees `9.1`'s own NAT'd address on that segment (`192.168.3.185`) — restricted to that,
    which in practice means "anything routed through 9.1," not perfectly scoped to pve-01 alone,
    but a real reduction from "the whole four-router chain"
- All three GL-iNet routers already had `input='DROP'` on their WAN zone — confirmed never reachable
  from the actual internet, independent of tonight's LAN-side hardening
- **UniFi (`1.1`) firewall was deliberately NOT touched** — it's a complex, actively-managed nested
  iptables setup (`unifi-core` reconciling its own state, chains like `UBIOS_INPUT_JUMP`/`ALIEN`).
  Confirmed via topology instead that it's already unreachable from the real internet (its own "WAN"
  side just faces `192.168.2.x`, and `2.1` — the true internet boundary — already drops WAN input).

## UniFi local admin account (bypasses cloud MFA)

Created `pve01-automation` on the UDR: Super Admin role, **"Restrict to Local Access Only" enabled**
(never touches the cloud/ui.com account, so never subject to MFA). Verified working end-to-end via
the local API (`POST https://192.168.1.1/api/auth/login` → `HTTP 200`, `isSuperAdmin: true`).
Credential stored at `/etc/unifi-automation.auth` on pve-01 (root-only, `chmod 600`), same pattern as
`/etc/dvr-dashboard.auth`. This is real, persistent, MFA-free API access — use it for anything UniFi
going forward rather than asking the owner to log in again.

## RF-off investigation: neither platform's schedule feature powers down the radio

Checked this properly rather than trusting field names:

- **UniFi's WLAN Schedule**: confirmed via multiple independent Ubiquiti community threads — disables
  the SSID (stops beaconing that network), radio hardware stays powered/active. If other SSIDs share
  the same physical radio (main + guest + IoT, which is the case here on every band), scheduling off
  just one leaves the radio transmitting for the others regardless.
- **GL-iNet's `gl_timer` WiFi feature**: read the actual control script
  (`/usr/bin/gl_timer_control_wifi` on the GL-iNet routers) rather than trust the "per-band" framing —
  it calls `wifi.set_config {iface_name, enabled: false}`, an **interface/SSID-level disable**, same
  shape as UniFi, not a physical radio power-off. (Corrects an earlier over-claim made mid-session —
  worth remembering if this comes up again.) GL-iNet does have one advantage: it exposes a separate
  timer for *every* SSID on a band (main + guest + IoT), so scheduling all of them off together leaves
  nothing being served on that radio — no beacons, no client traffic — which is functionally close to
  "off" even though not a documented hardware power-down.
- **Bottom line**: neither software feature is a substitute for physical power-off if the actual bar
  is "guaranteed zero RF." That's the whole reason the smart-plug plan below exists.

## Physical power-off plan (not yet built — waiting on hardware)

### Segments, confirmed by the owner directly (not inferred)

- **Segment A**: `1.1` + `2.1` + `3.1`, one shared power strip, one dumb mechanical timer
- **Segment B**: `9.1` + pve-01, one shared power strip, a *different* dumb mechanical timer
- Both power strips are physically in the same room as each other (owner and pve-01/9.1 are
  co-located), even though they're electrically separate — the powerline adapters exist because
  running new ethernet between floors isn't practical (masonry), not because of distance within
  this room.
- pve-01, 9.1, and the physical Chromecast (when not manually moved to a different network — see
  below) are already co-located on Segment B's own subnet (`192.168.9.0/24`) — this was the basis for
  the original "no reconfiguration needed" recommendation, and still holds for the *local* traffic
  path. Segment A only matters as the upstream *internet* dependency.

### Current dumb-timer schedule (replicate this, or ask if the owner wants changes)

| Night | Off | On |
|---|---|---|
| Sun/Mon/Tue/Wed/Thu | 22:25 | 05:05 |
| Fri/Sat | 01:00 | 05:05 |

### The plan

Two smart plugs (not power strips — each existing strip stays wired exactly as-is, only the dumb
timer feeding it gets swapped for a controllable plug):
- Plug 1 → Segment A's strip
- Plug 2 → Segment B's strip

**Recording automation needs override authority over *both* plugs, not just Segment B's.** Segment B
being powered is useless for an in-progress recording if Segment A (the actual internet path) is dark
— there's no IPTV stream to record without it. When a tracked game (from the sports-auto-record
system being drafted in the Wholphin thread) is still live near the scheduled cutoff, the automation
needs to hold *both* segments open together, and release them once the game's actually over.

Product research already done (see the Wholphin-thread chat history for links/pricing) — Shelly Plug
S (Gen3 or Gen4) recommended over TP-Link Tapo for its properly-documented local HTTP API (no cloud
round-trip needed to fire the schedule or to be commanded by the automation).

**Not yet done**: owner hasn't purchased the plugs yet. Nothing to wire up until they arrive.

## The Chromecast / "IOT" network / Tailscale mystery — unresolved, real, worth a dedicated look

Owner moved the physical Chromecast onto the UniFi "IOT" SSID (Segment A, per the VLAN table above)
and it kept working — still able to reach pve-01/Jellyfin on Segment B, actively streaming at the
time. This shouldn't work under the network's own stated rules, so it was worth actually tracing
rather than assuming.

**Ruled out, confirmed by direct inspection:**
- Not a VLAN trick — IOT is a flat, unisolated part of the same `192.168.1.0/25` network as everything
  else on the UDR.
- Not a port-forward — checked `9.1`'s firewall directly (`uci show firewall`), no redirect/DNAT rules
  exist that would explain it.
- Not ordinary WAN→LAN forwarding — `9.1`'s WAN zone has `forward='REJECT'`, which should block this
  exact kind of cross-segment traffic by default.

**What was actually observed:** live connection-tracking on `9.1` during the active stream
(`/proc/net/nf_conntrack`, filtered for port 8096) showed the Jellyfin connection arriving with
source `192.168.2.110` — the UDR's own address on Segment A — and `9.1`'s own routing table sending
return traffic for that address via **its Tailscale interface** (`ip route get 192.168.2.110` →
`dev tailscale0 table 52`). This lines up with something already on record from the earlier router
security audit: Tailscale traffic is explicitly trusted on these routers and allowed to reach every
host on the LAN, bypassing the normal WAN-zone restrictions. So the working theory is **Tailscale is
the actual bridge between Segment A and Segment B**, not local NAT/routing.

**The part that doesn't add up yet**: Tailscale's own peer list (`tailscale status --json`, checked
from pve-01) shows the UDR (`R2D2`) as **currently offline**, and pve-01 isn't advertising
`192.168.9.0/24` as a subnet route at all — neither of which cleanly explains what conntrack just
showed happening live. Did not chase this further tonight; flagging honestly rather than guessing.

**New information from the owner, same conversation**: R2D2's Tailscale has been killed by device
firmware/OS upgrades before — this is a very plausible explanation for the "offline" status (it may
genuinely be down right now, post-upgrade, with the conntrack entry being a stale leftover from before
the last time it broke, rather than a live, currently-working path). This turns "Tailscale audit" from
a nice-to-have into something that directly explains tonight's mystery, not just unrelated cleanup.

**Also flagged by the owner**: the stream visibly pauses/buffers momentarily while running on the IOT
network. Worth investigating together with the routing question — if the real path is bouncing through
a Tailscale DERP relay (used when a direct peer-to-peer connection can't be established) rather than a
direct connection, or double-hopping unnecessarily, that would explain exactly this kind of stutter.
Next time this is being actively investigated, capture `tailscale status` / `tailscale ping <peer>`
output *during* an active buffering moment, plus a fresh conntrack snapshot, rather than after the fact.

### Recommended next steps for the Tailscale audit (separate task, not urgent tonight)

1. Full device inventory — `tailscale status` already shows many peers from much earlier in this
   session (pve-01, basement-brume, big-big-gl, big-gl, chromeos-google-corsola, dmarc-brume,
   fedora-tailscale, gl-axt1800, gl-be3600, google-chromecast, and more) — cross-reference against
   what's actually still in active use versus stale/decommissioned, matching the original concern
   already on record in the router security audit ("every one still holds valid tailnet credentials").
2. Specifically check R2D2/UDR's Tailscale state and whether it needs reinstalling after its last
   upgrade — this alone may resolve tonight's mystery.
3. Decide whether pve-01 (or another node) should properly advertise a `192.168.9.0/24` subnet route,
   rather than whatever ad-hoc/undocumented path is currently letting Segment A reach Segment B — a
   deliberate, documented route would be far easier to reason about than the current accidental one,
   and would matter even more once the power-automation plan starts cycling Segment A on/off nightly
   (a flaky, undocumented bridge is a bad thing to depend on for "does the recording still work
   tonight").
4. Check whether any of the per-content VPN networks (Surf-Lath-UDP/AlbaniaWG/Ashburn/NYC) do a full
   tunnel (would break Tailscale/local reachability while active) versus a split tunnel — not yet
   checked for any of them.

## Software shutdown schedule fixed to match the real physical timer (2026-08-08, done)

Both `/usr/local/bin/dvr-clean-shutdown` and `/usr/local/bin/dvr-dashboard` previously assumed a flat
single daily cutoff (~22:24, `POWER_ON` also slightly off at 04:57). Real physical timer (confirmed
directly by the owner) restores power at 05:05 every day and cuts at 22:25 on Sun/Mon/Tue/Wed/Thu
nights, but stays on until ~01:00 the *following* calendar day on Fri/Sat nights. Fixed both files to
be day-of-week aware:
- `dvr-clean-shutdown.timer` now has two `OnCalendar` lines (22:10 on Sun–Thu, 00:45 on Sat/Sun) instead
  of one flat daily trigger.
- `dvr-dashboard` gained `is_powered()` / `todays_power_off()` helpers used by both `outside_window()`
  (the "does this game need a manual power override" check) and the displayed power window — this was
  a real live bug: games airing 22:25–01:00 on Fri/Sat nights were being wrongly flagged as needing an
  override even though the real timer already covers them.
- Verified with 10 test scenarios (including both early-morning tail-end edge cases) before deploying,
  all passing; confirmed live afterward (`powerOff: 01:00 (+1d)` correctly shown on a Saturday).

## Full household playback device inventory (owner-provided, 2026-08-08 night)

Not previously documented — worth having in one place:

- **Living room (same room as pve-01/9.1/1.1/2.1/3.1)**: the main TV, currently has **two
  Chromecasts, one of which is powered down** (not yet investigated why — spare/leftover from earlier
  testing phases, or intentional redundancy; worth asking rather than assuming). A **Google TV
  Streamer has been ordered** (shipping from the US, ~1-2 weeks out) and will be **wired directly into
  `9.1`** (physically right under this TV) once it arrives — see the trade-off discussion above
  (rock-solid for local Jellyfin/DVR playback, but won't have access to the geo-VPN tunnels the way
  the Chromecast does unless separately VLAN-tagged).
- **Basement**: a TV (Hisense, Google TV built-in) behind `Basement-Express` (UniFi mesh) and a
  powerline adapter. Also has **its own separate GL-MT2500** ("Basement Brume" — this is the
  `basement-brume` Tailscale peer seen much earlier in this session with no context at the time;
  now explained) — a second, independent unit from `2.1`, dedicated to running a **US-exit VPN
  tunnel** so the basement TV can watch US-based content over a reliable wired connection. Not part
  of the four-router chain documented above; a self-contained setup.
- **Kids' floor**: has a TV that needs a Chromecast (behind `Mid-Express` + the AP on that floor).
- **Owner's floor (top floor)**: has a TV that needs a Chromecast (behind `U6 Lite`).

Not yet clear whether "needs a Chromecast" means these TVs currently lack one and need one acquired,
or already have one and this is just describing the existing setup — worth clarifying before assuming
either way.

## Open items carried from this thread

- [ ] Owner to purchase 2x smart plugs (Shelly Plug S Gen3/Gen4 recommended)
- [ ] Once plugs arrive: wire into `dvr-dashboard`'s override system + the sports-recording automation
      (both segments, not just Segment B)
- [ ] Tailscale audit — device inventory cleanup, R2D2's broken Tailscale specifically, subnet route
      decision, investigate the IOT-network buffering symptom
- [ ] Check full-tunnel vs split-tunnel behavior on each per-content VPN network
- [ ] Google TV Streamer arriving in ~1-2 weeks — wire into `9.1` once it's here (physically located
      right under the living room TV, no cabling run needed)
- [ ] Ask why the living room's second Chromecast is powered down — spare, or safe to remove/repurpose?
- [ ] Clarify whether the kids'-floor and owner's-floor TVs need Chromecasts acquired, or already have
      them
- [ ] (Unrelated to this thread, tracked separately in the Wholphin work) `NoCompatibleStream` bug fix
      for in-progress-recording playback
- [ ] Decide whether to actually enable `sports_dvr_auto_v2.py` on a live systemd timer (not yet
      scheduled — the dashboard toggles exist and work, but nothing is auto-scheduling recordings
      from them yet; deserves its own explicit go-ahead before flipping on)

## Sports auto-recorder dashboard (deployed 2026-08-08)

Per-team on/off toggles for the sports auto-recording system, built by agy (build-mode dispatch,
`gemini-3.6-flash-high`) after Claude Code laid out the brief, then independently reviewed and merged
into the live dashboard by Claude Code.

- **Default state**: Packers, Badgers Football, Badgers Basketball, and Bucks default **ON**;
  **Brewers defaults OFF** (per owner request — too many games in a season to auto-record by default).
- **State file**: `/var/lib/dvr-dashboard/sports-config.json`, per-team boolean, atomic writes
  (`.tmp` + `os.replace`). Designed to extend cleanly to a future `plug-config.json` once the smart
  plugs are in place.
- **API**: `GET`/`POST /api/sports-config` on the existing dashboard service (same Basic Auth as
  every other endpoint), mirroring the existing `/api/override` pattern.
- **UI**: new "Sports auto-recorder" card between "Server power" and "Scheduled recordings", with
  accessible toggle switches; games for a toggled-off team show an "auto off" pill in the schedule.

**Build/merge process, for the record**: agy's build was done against the dashboard file *before*
the day-of-week shutdown fix (above) had landed, so its file could not be deployed wholesale without
regressing that fix. Claude Code instead surgically extracted agy's feature (backend routes, CSS,
HTML, JS) and merged it into the already-fixed live file, then — per the owner's explicit ask for a
second opinion — had agy independently review the merged result before deploying. agy's review
(`/root/agy-reports/20260808T153753Z-sports-dashboard-merge-review.md`) confirmed the sports feature
and the shutdown fix were both fully intact, and caught two small JS wiring gaps (the "auto off" pill
not appearing until the next 60s refresh on first page load; toggles from another tab/device not
syncing until refresh). Both were fixed before deploy. Live-verified after deploy: `/api/status` and
`/api/sports-config` both correct, toggle round-tripped through the real POST endpoint with disk
persistence confirmed, then restored to the Brewers-off default.

**Not yet done**: `sports_dvr_auto_v2.py` (the script that actually reads this state file to decide
whether to auto-schedule a recording) is built but **not yet wired to any systemd timer** — the
toggles currently only affect what the dashboard *displays*, not yet real auto-scheduling. Tracked
above as an open item.
