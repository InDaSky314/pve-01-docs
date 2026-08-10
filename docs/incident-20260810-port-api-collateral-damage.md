# Incident: port-assignment API investigation caused real, live collateral damage on an unrelated network (2026-08-10, ~17:09-17:33 CEST)

## What happened

While investigating why `vlan_subnet.set_fixed_subnet`/`update_custom_subnet`'s
`ifaces` parameter is unreliable (per Claude Code's own earlier finding —
repeated timeouts on 3.1), agy ran live test calls against 3.1 attempting to
reassign physical port LAN4. The call reported failure (`"Write execution
failed: Port binding API call timed out on router switch driver
(libcable)"`, logged correctly to the audit trail) — but **the call was not
actually a no-op**. It partially mutated `/etc/config/network` before timing
out: `br-lan`'s bridge port list (`network.@device[0].ports`) gained three
unexpected raw switch-port entries (`'5' '7' '6'`) alongside the correct
`'eth1.1'`.

That corruption, combined with 3.1's route-policy reconciliation
(`rtp2.sh apply`, likely triggered by the same libcable/switch-restart call),
resulted in `br-lan` — **3.1's own main/admin network** — gaining a priority
9920 blackhole rule identical to the ones intentionally applied to
vlan11/vlan12/iot/guest. Unlike those, `main`/`br-lan` was never meant to be
isolated; it has no VPN tunnel and no `isolate` flag. The practical effect:
**every device on 3.1's main network lost all internet access.**

Three real, active devices were affected: `Pixel-8-Pro` (192.168.3.101),
a `Mac` (192.168.3.123), `Pixel-10` (192.168.3.167) — all had live DHCP
leases on this network at the time.

## Why the safety framework didn't catch it

Agy's Phase 2 safety-rail framework (pre-write backup, API-path writes,
post-write verification, audit log) worked exactly as designed **for the
specific thing it was checking** — it correctly detected that the LAN4
reassignment itself failed and logged `verified_result: false`. What it
didn't check: whether the failed/timed-out call had side effects on
*anything else*. Post-write verification narrowly confirmed "did the target
change happen" rather than "did the system's overall health stay the same."
That's the real gap — not a missing backup (one existed and could have been
used to restore), but a verification step that was too narrowly scoped to
catch collateral damage on an unrelated network.

## How it was found and fixed

Found during Claude Code's independent re-verification pass on Agy's Phase 2
report (the same "don't just trust the report" discipline used throughout
this session) — the new Prometheus `/metrics` endpoint itself surfaced it:
`router_network_egress_reachable{network="main",vlan="1"} 0` and
`router_network_blackhole_active{network="main",vlan="1"} 1` didn't match
what should have been true for that network. Chasing that down (not
assuming the metric itself was wrong) led straight to the real bug.

Fix, in order:
1. Confirmed the blackhole was real via `ip rule show` (not just the metric)
   and confirmed real impact via `ip route get ... iif br-lan` for each
   affected device's actual IP (the correct simulation method — `curl
   --interface` does NOT exercise `iif`-matched rules the way real forwarded
   client traffic does, a methodology lesson worth remembering).
2. Found `/etc/config/network`'s mtime (17:09) matched Agy's own audit-log
   timestamp for the LAN4 attempt exactly — confirming cause.
3. Compared current `br-lan` port list against the `uci export` backup taken
   at 15:31 (before any of today's work) — confirmed the correct state was
   `list ports 'eth1.1'` only.
4. `uci del_list` the three extraneous ports, `uci commit network`.
5. `/etc/init.d/network reload` — did not clear the stale blackhole rule
   (expected; that command doesn't touch route-policy state, a lesson
   already documented earlier this session).
6. `/usr/bin/rtp2.sh apply` — also did not clear it (rtp2.sh only adds rules
   it thinks are newly needed, doesn't proactively remove ones it no longer
   thinks are needed — same "add-only" behavior already documented earlier
   this session for a different rule).
7. Removed the stale kernel rule directly: `ip rule del prio 9920 iif
   br-lan`.
8. Verified fully: blackhole list back to exactly the original 4 entries
   (guest/iot/vlan11/vlan12), all three real devices' traffic now correctly
   routes via `dev eth0`, all 3 tunnels still up, all tri-band bridges still
   correct, wholesale VLAN-tunnel routing untouched.

**Real user impact window: roughly 17:09 to 17:33 CEST (~24 minutes)** — the
affected devices had no internet for that period.

## Required follow-up (not yet done)

- **Harden the safety framework's post-write verification to check overall
  system health, not just the specific target** — e.g. re-run the blackhole/
  drift checks across *all* networks after any write, not only the one being
  changed. This is the real fix; without it, any future write action (not
  just port reassignment) has the same class of blind spot.
- **Reconsider whether the port-reassignment button should be live in the
  dashboard UI at all** until the above is done — it's proven real collateral
  -damage potential, not just an internal-to-itself failure.
- Agy's audit log entry for this action should probably be amended/annotated
  to reflect that `verified_result: false` understated what actually
  happened (a side effect occurred despite the "failure").

## Second, unrelated bug found during the same verification pass

While confirming the fix via `/metrics`, found a second, pre-existing bug
in the blackhole-detection logic itself (`router-dashboard`, the network
card / metrics builder) — unrelated to the incident above, but real and
worth fixing since it would have been silently wrong indefinitely.

**Bug**: `has_blackhole_rule = (blackhole_rule in ip_rules_31 and
"blackhole" in ip_rules_31)` did a naive substring search across the
*entire* `ip rule show` output blob. `main`'s check string
(`"from all iif br-vlan1"`, itself wrong — main's real bridge is `br-lan`,
not `br-vlan1`) is a **substring of** `"from all iif br-vlan11"` and
`"from all iif br-vlan12"` — GIOT and WALDO's own *legitimate* blackhole
rules. Net effect: `main` would always show as blackholed as long as any
numbered VLAN's intentional blackhole rule existed, regardless of main's
real state — a permanent false positive that predates today's incident
entirely (would have been wrong from the moment Phase 2 shipped).

**Fix**: exact per-line matching instead of blob-substring search, and a
correct bridge-name map (`main`→`br-lan`, not a formula-guessed
`br-vlan{vlan_id}`). Verified against real live data for all 5 networks
before and after — now correctly shows `main: False`,
`guest/iot/vlan11/vlan12: True` (their genuine, intentional fail-closed
design).

## Also disabled pending the safety-framework fix

Pulled the port-reassignment control from both the dashboard UI (now shows
a disabled state with a tooltip explaining why) and the backend endpoint
(`POST /api/actions/reassign-port` now returns `503` unconditionally) —
until the "Required follow-up" above (system-wide post-write verification)
is actually built. A feature that already caused one real incident stays
off until it's provably safe, not just "probably fine now."
