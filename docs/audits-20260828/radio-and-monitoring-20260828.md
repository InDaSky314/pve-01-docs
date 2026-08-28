# Radio shutdown & post-cutover monitoring — audit and repair
**2026-08-28 · pve-01 estate · Claude + Agy, cross-validated**

---

## BLUF

**Radios.** Every device except the UniFi APs can stop serving WiFi in software while
its wired path keeps running — so **only the UniFi side needs a powerline timer.**
Two GL routers, the UDR and the switch do not. But two claims needed correcting:

- **Agy said `wifi reload` disables a GL radio. It does not.** I tested it: the VAPs
  stayed up and kept beaconing. The command that actually works is
  **`wifi down <radio>` / `wifi up <radio>`**.
- **"Radio fully powered off" is not proven.** Beaconing and all AP interfaces stop,
  but the PHY still reports `ready` and remains listed. If your goal is *no WiFi
  service*, software is enough. If it is *zero RF emission*, only cutting power
  guarantees that.

**Scheduling works.** The radio comes back with `wifi up` **without a reboot** —
verified on 6 GHz end to end. GL has no native WiFi scheduler, so this is a cron job.
Your instinct about orphaning is right: Mid-Express loses its uplink while the UDR's
radio is off and re-joins by itself when it returns, so the risk is bounded and
self-correcting.

**Monitoring.** Agy found **13 items**, and its summary is the right one:
*silence was masquerading as health.* The worst were **not** alarms firing — they were
alarms that could never fire. **I fixed the highest-value ones this morning**; four
larger items need your call.

**SSH to R2D2 over WAN: don't.** You already have a better path — Tailscale works
today, and I repaired `uni.sh` to use it. Detail below.

---

## What I fixed this morning

| Fix | Why it mattered |
|---|---|
| `uni.sh` now falls back to the UDR's Tailscale address | pve-01 lost all UniFi visibility at the cutover — the UDR is now on the far side of its own WAN |
| SSH identity for router IPs added to `/root/.ssh/config` | `wg-snapshot.sh` and `router-backup.sh` called `ssh root@…` with no key and had been failing silently since the swap. One config change repaired both — verified exit 0 |
| MLO member VAPs now self-heal in the boot hook | My own radio test proved a `wifi` restart silently unbridges `wlan02/03/12/13` |

Backups: `uni.sh.pre-cutover-fix-20260828`, `.ssh/config.pre-cutover-20260828`,
`99-nbp.pre-mldmembers-*`.

---

## Needs your decision

1. **`router-dashboard` is emitting false metrics** — `gateway_2_1_wan_up 1` for a
   retired device, plus a bogus subnet-conflict flag. It needs re-architecting for the
   2-router topology, which is a code change worth doing with you available.
2. **Prometheus `job="router"` has been DOWN since the cutover and no alert exists**
   for it. Needs either a node exporter on the routers or removal of the target.
3. **Router syslog is dead** — the receiver only accepts `192.168.9.1`, the MT6000 moved
   to `.5.1`, and neither router is configured to ship logs.
4. **No alert rules for "target down" anywhere.** This is the root cause of the whole
   "silence = health" problem.

---
<!-- ------------------------- technical detail below ------------------------- -->

## 1. Radio audit — per device

| Device | Radios | Uplink | Software radio-off? | Method | Verdict |
|---|---|---|---|---|---|
| **GL-BE9300** (main) | 3 (2.4/5/6) | eth0 PPPoE **wired** | **Yes** | `uci set wireless.wifiN.disabled=1` + **`wifi down wifiN`** | Software-off. **Never cut power** — house edge |
| **GL-MT6000** (TV) | 2 (2.4/5) | eth1 DHCP **wired** | **Yes** | same, radios are `mt798611` / `mt798612` | Software-off. No timer needed |
| **GL-MT2500** | none | retired | N/A | no wireless hardware | Zero RF by design |
| **UniFi UDR** (R2D2) | 2 | wired | Yes (controller/CLI) | AP-group exclusion + mesh off | Software-off. **Never cut power** — hosts the controller |
| **AC-Lite, U6 Lite, Basement-Express** | 2 each | wired | Yes | controller `{"disabled": true}` or PoE | Software-off **or** PoE/timer |
| **Mid-Express** | 2 | **wireless** | Yes | controller, or power cut | **Depends on RF for its own uplink** |
| **USW-Flex-Mini** | none | wired | N/A | switch | Zero RF by design |

### Where Agy and I disagree

| Agy's claim | What I measured |
|---|---|
| `disabled=1 && wifi reload` tears down VAPs and halts beaconing | **False.** After reload: 4 VAPs still present, 4 hostapd configs still present. `wifi down wifi2` is what actually worked (VAPs → 0) |
| Disabling "uninitializes the Qualcomm QSDK radio PHYs" | **Overclaimed.** No AP vdevs remain on phy3 and beaconing stops, but `phy3` is still listed and `wifi.get_status` still reports the radio `state: ready`. Full depowering unproven |
| Mid-Express is redundant because AC-Lite covers the middle floor | **Unproven.** At audit time AC-Lite had **0** clients and Mid-Express had **5**. Same floor is not the same coverage |
| MT6000 radios are `mt798611` / `mt798612` | **Correct** — validated against live UCI |

Agy's per-device table, UniFi API methods and the Mid-Express dependency call-out were
all sound. The failure mode was asserting mechanism from documentation instead of
running the command.

### The test, in full

No clients were on 6 GHz, so it was a zero-impact target.

```
disabled=1 + wifi reload  -> wlan2* = 4, hostapd = 4   (NO CHANGE - still beaconing)
wifi down wifi2           -> wlan2* = 0                (VAPs torn down)
                             2.4G = 5, 5G = 5, internet OK  (other bands untouched)
wifi up wifi2             -> wlan2* = 4, correct SSIDs and bridges restored
                             NO REBOOT REQUIRED  <-- this is what makes scheduling viable
```

Side effect found and fixed: the full `wifi` restart left `wlan02/03/12/13` with no
master. Non-MLO SSIDs kept working, so it was silent. The boot hook now re-attaches
MLO members by SSID (Open-Fields → `mld0`, GIOT → `mld1`); tested by detaching
`wlan02` and re-running the hook.

### Scheduling recommendation

GL has **no native WiFi scheduler** — System → Scheduled Tasks offers only LED Display
and Schedule Reboot. Use cron (already enabled on the BE9300):

```sh
# 23:30 radios off, 06:30 radios on - BE9300
30 23 * * * for r in wifi0 wifi1 wifi2; do wifi down $r; done
30  6 * * * for r in wifi0 wifi1 wifi2; do wifi up   $r; done
```

Do **not** use `wifi` (full restart) in a schedule — it unbridges the MLO members.
Per-radio `wifi down` / `wifi up` avoids that.

For UniFi the picture is different and important: **the built-in WiFi scheduler only
disables SSIDs, not radios — the radio stays powered and transmitting.** Ubiquiti's own
community confirms this, and the standard answer for true RF-off is PoE scheduling or an
outlet timer. So the UniFi APs are the only gear where a powerline timer buys you
something a schedule cannot.

## 2. SSH to R2D2 over its WAN — recommendation: no

Worth separating two things, because "WAN" is misleading here. The UDR's WAN is now your
own `192.168.9.0/24`, not the internet — so this would expose SSH to your LAN, not the
world. That is much less alarming than it sounds.

I still would not do it:

- **You already have a working, better path.** Tailscale to the UDR works today
  (verified, HTTPS 200), is authenticated and encrypted, and needs no inbound rule.
  `uni.sh` now uses it automatically.
- **Source-restricted rules on UniFi are fragile.** They live in the controller, and
  firmware updates have a history of resetting exactly this kind of override — so the
  restriction can quietly disappear while the opening remains.
- **It adds a second credential path to your most privileged device.** The UDR is the
  controller, the VLAN gateway and the WireGuard endpoint.

If you later want a LAN-side path that does not depend on the tailnet, the cleaner
option is a static route on the BE9300 plus a narrowly-scoped allow rule for pve-01's
address only — reviewed after each firmware update.

## 3. Monitoring — the full 13

Agy's severity ranking, with my verification status:

| Sev | Item | Verified | Status |
|---|---|---|---|
| CRITICAL | `wg-snapshot.sh` failing SSH every 60s; all four tunnels unmonitored | ✅ confirmed | **FIXED** |
| CRITICAL | Edge watchdog uncovered since `gateway-2-1-monitor` was disabled | ✅ confirmed | open — needs repoint to 9.1 |
| CRITICAL | No container VPN egress enforcement check | ✅ confirmed | open |
| HIGH | `router-dashboard` emitting false metrics | ✅ confirmed | open — needs rewrite |
| HIGH | Prometheus `job="router"` DOWN, no alert | ✅ confirmed | open |
| HIGH | Router syslog pipeline dead | ✅ confirmed | open |
| HIGH | `router-backup.sh` failing SSH | ✅ confirmed | **FIXED** |
| HIGH | Boot hook has no health surveillance | ✅ confirmed | open |
| MEDIUM | `chromecast-logcat` pointing at old Chromecast IP | plausible | open |
| MEDIUM | Stale `.ssh/config` aliases, no `glinet-5.1` | ✅ confirmed | **FIXED** |
| MEDIUM | No node exporters on CT 111/112 | plausible | open |
| MEDIUM | No `up == 0` alert rules anywhere | ✅ confirmed | open |
| LOW | Grafana panels querying retired `gateway_2_1_*` | plausible | open |

Mail delivery via Postfix/Gmail relay was verified working and survives the nightly IP
rotation.

**The gap that matters most** is the last one: with no "target down" alerting, every
other break above was invisible. That is the thing to fix first — it is what turns the
remaining items from silent into noisy.
