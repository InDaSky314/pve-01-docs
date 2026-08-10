# Incident: wholesale VLAN-to-tunnel routing leaked to bare WAN since it was built (2026-08-10, discovered ~19:35 CEST)

## What happened

The wholesale VLAN-to-tunnel routing built earlier this session (the raw
`ip rule` technique giving WALDO/GIOT/Swiss VLANs a default tunnel without
needing per-device MAC enrollment) had a real bug from the moment it was
built: **it never actually worked for unassigned devices.** Every device on
GIOT, WALDO, or the Swiss network that wasn't individually MAC-enrolled in
the GUI/API was silently leaking straight to bare WAN — the wholesale
routing rule was simply never being reached.

This went undetected through hours of `ip route get` simulation testing
(which all passed) because those simulations never included the one thing
that actually determines real routing behavior: GL.iNet's own
`route_policy` engine marks **every packet not individually MAC-enrolled**
with `fwmark 0x8000` ("no VPN, use main table") before the packet ever
reaches `ip rule` evaluation. That mark matches GL.iNet's own existing
`6000: from all fwmark 0x8000/0xf000 lookup main` rule — which sits at a
**lower priority number (evaluated first)** than the wholesale rule, which
had been placed at priority 6100 specifically to let individually-enrolled
devices win. The wholesale rule never got a chance to run at all.

**Confirmed via a real, live device**: the owner's Mac, genuinely connected
to WALDO (confirmed via the phone's native OS network settings, not just a
scanner app), reported getting a German public IP instead of WALDO's real
tunnel exit. Checked the actual live connection tracking table
(`/proc/net/nf_conntrack`) for that exact device's real traffic — every
single one of its ~58 active connections showed the NAT reply address as
`192.168.2.241`, which is **3.1's own bare WAN IP**, not WALDO's tunnel
egress at all.

## How this differs from the earlier port-API incident

That incident was a one-time, ~24-minute event with a specific trigger
(a failed API call). This one was a **standing, silent leak** — any device
using WALDO/GIOT/Swiss without individual MAC enrollment has been leaking
to bare WAN since the wholesale routing was first built earlier today,
until this fix. Given the whole point of these tunnels is to route traffic
through a specific VPN/location, this is a real privacy exposure, not just
a connectivity gap.

## Root cause, precisely

`ip route get` simulations used throughout this session's earlier
verification never included a `mark` parameter, so they always tested the
routing decision for an *unmarked* (mark=0) packet — which correctly found
the wholesale rule. Real packets are never actually unmarked by the time
they reach `ip rule` evaluation; GL.iNet's route_policy engine has already
stamped them with either a specific per-device mark (if individually
enrolled) or the default `0x8000` (if not) well before that point. The
verification method itself had a blind spot that made a real bug look
fixed.

## The fix

Changed the wholesale rules from a plain source-subnet match to a
source-subnet **plus mark** match, moved to priority 5900 (before *all* of
the 6000-priority rules, not just the default one):

```bash
ip rule add from 192.168.11.0/24 fwmark 0x8000/0xf000 lookup 1011 priority 5900   # GIOT
ip rule add from 192.168.12.0/24 fwmark 0x8000/0xf000 lookup 1002 priority 5900   # WALDO
ip rule add from 192.168.9.0/24  fwmark 0x8000/0xf000 lookup 1001 priority 5900   # Swiss
```

This is safe for the "individual MAC assignment always wins" requirement
specifically *because* of the mark condition: it can only ever match
traffic GL.iNet's own engine already decided was unassigned (mark 0x8000).
A device individually enrolled in a tunnel carries a different, specific
mark (0x1000/0x2000/0xa000) and never matches this rule at all — it falls
through untouched to its own existing 6000-priority rule, exactly as
before.

**Verified all four scenarios live, not just the target one:**
```
unassigned device on GIOT/vlan11   -> GIOT tunnel     (correct)
unassigned device on WALDO/vlan12  -> WALDO tunnel     (correct, matches
                                       the real device's now-corrected path)
unassigned device on Swiss/guest   -> Swiss tunnel     (correct)
simulated individually-enrolled device on vlan12 (mark 0xa000, as if
  MAC-assigned to GIOT) -> still correctly routes to GIOT, not WALDO,
  proving individual assignment still wins
```

## Also fixed

- **Flushed conntrack** for the affected real device (58 stale connections
  deleted) plus the other tunneled subnets as a precaution, since an
  already-established connection keeps its original (leaked) NAT decision
  for its lifetime even after the routing rule is fixed — the owner's phone
  would otherwise have kept leaking on existing connections until they
  naturally expired.
- **Updated the persistence hotplug script**
  (`/etc/hotplug.d/iface/99-network-buildout-persist`) to install the
  corrected mark-conditioned rules and to actively clean up the old
  unconditional rule if it's still present from an earlier deploy —
  verified with a real break/restore test (reintroduced the old broken
  rule, fired the script, confirmed it removed the old one and installed
  the corrected one).

## Lesson

The standing "verify against real data, don't trust a simulation that
looks plausible" discipline used throughout this session needed to extend
to the simulations themselves — `ip route get` is a real kernel query, not
a guess, but it's only as good as the inputs it's given. Missing the mark
condition made a broken rule look verified for hours. The thing that
actually caught this was a real user, on a real device, actually using the
feature — worth remembering that live traffic inspection
(`/proc/net/nf_conntrack`, real WireGuard handshake/transfer stats) is a
strictly higher bar of evidence than routing-table simulation, and should
be reached for earlier when something "should" be working but a real user
reports it isn't.

## Related, separate experiment: GIOT visibility — WPA3-SAE vs WPA2 test

Not the leak above, but the same investigation session. Owner authorized
testing whether GIOT's encryption mode is why it doesn't appear in scans
(confirmed via native Android WiFi settings too, not just a 3rd-party
scanner — genuinely invisible, not mislabeled: cross-checked the "hidden"
MAC-only entries in the scan against GIOT's real current BSSIDs
(`FE:A2:8B:24:F8:CE`, `1E:2E:AF:B5:B6:01`, `42:CF:09:A3:76:1D`) — none
matched, ruling out "it's there but unresolved").

Real structural difference found: GIOT was pure WPA3-SAE only
(`encryption='ccmp'`, `sae='1'`) across all three MLO members, while
WALDO — which *does* show up fine — is pure WPA2-PSK
(`encryption='psk2+ccmp'`, no SAE at all). Not actually "mixed vs pure" as
first assumed; two different pure modes.

Changed all three GIOT MLO members (`wlanmld2g/5g/6g`) to match WALDO's
exact working config (`psk2+ccmp`, SAE removed) as a clean single-variable
A/B test. Applied via `wifi reload`, verified: `mld0`→`br-vlan11` and
`mld1`→`br-iot` both correctly attached (mld1 briefly flapped mid-reload,
same as previous reloads, settled correctly), all 3 GIOT hostapd instances
confirmed live with correct SSID, wholesale routing rules and all 3
tunnels unaffected.

**Awaiting owner's re-scan to confirm whether this fixes visibility.** If
it does, that isolates the cause to WPA3-SAE (possibly combined with MLO)
specifically. If it doesn't, encryption mode is ruled out and the
investigation needs to look elsewhere (e.g. a genuine MLO beacon/RNR
parsing issue independent of security mode).
