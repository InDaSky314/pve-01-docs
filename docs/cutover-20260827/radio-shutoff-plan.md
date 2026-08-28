# Radio shut-off & powerline timer plan
**2026-08-28 · reconciled from an Agy audit, my own testing, and vendor research**

---

## Executive summary

**You do not need powerline timers for either GL router.** Both the BE9300 and the
MT6000 can go fully radio-silent in software while continuing to route over wire —
the BE9300 keeps PPPoE and the whole LAN up with all three radios down.

**You do need them (or PoE scheduling) for the UniFi APs.** UniFi's WiFi scheduler
only disables the *SSID*; the radio stays powered and transmitting. That is a vendor
limitation, confirmed in Ubiquiti's own community guidance.

**Scheduling works, but not the way it was first reported to me.** `wifi reload`
does **not** apply a radio enable/disable on the BE9300 — I tested it and the VAPs
kept beaconing. The command that actually works is `wifi down <radio>` /
`wifi up <radio>`, and it works in **both** directions with **no reboot**. That is
what makes a nightly schedule viable.

**Your orphaning concern is already solved by your own idea.** Mid-Express uplinks
wirelessly to the UDR, so cutting UDR radios strands it — but it re-associates
automatically when the radio returns. Time-boxing the outage bounds the risk.

**Two devices emit no RF at all** and are irrelevant to this: the MT2500 (Brume 2,
no wireless hardware) and the USW-Flex-Mini (a switch).

---

## Decision table

| Device | Radios | Stop RF without cutting power? | Powerline timer needed? |
|---|---|---|---|
| **GL-BE9300** (main router) | 3 (2.4/5/6) | **Yes** — `wifi down wifi0 wifi1 wifi2` | **No.** Never cut power — it is the PPPoE edge |
| **GL-MT6000** (TV corner) | 2 (2.4/5) | **Yes** — `wifi down mt798611 mt798612` | **No** |
| **GL-MT2500** (Brume 2) | none | N/A — no wireless hardware | **No** |
| **USW-Flex-Mini** | none | N/A — pure switch | **No** |
| **UniFi UDR (R2D2)** | 2 | Only via per-device disable/CLI, not the scheduler | **No** — cutting power collapses the controller |
| **AC-Lite-Mid-Floor, U6 Lite** | 2 each | Per-device API disable, or PoE off | **PoE scheduling preferred** |
| **Basement-Express** | 2 | Per-device API disable | Optional |
| **Mid-Express** | 2 | Per-device API disable | **Timer viable** — no wired clients to lose |

---

## Where I disagree with the Agy audit

Agy's inventory work was good and I confirmed most of it. Three corrections:

**1. The disable method is wrong — this one matters.**
Agy states `uci set wireless.wifiN.disabled=1 && wifi reload` "tears down all
hostapd VAPs, uninitializes the Qualcomm QSDK radio PHYs, halts beaconing
completely." I tested exactly that on the 6 GHz radio, which had zero clients:

    after uci disabled=1 + wifi reload:
      wlan2* interfaces : 4    <- unchanged, still up
      hostapd 6GHz confs: 4    <- still beaconing

It does nothing. The working command is `wifi down wifi2`, which took the VAP count
to 0 while 2.4 GHz and 5 GHz stayed at 5 each and the internet was unaffected. This
is the same class of trap already recorded in the runbook — on this box `wifi reload`
does not apply radio state changes in *either* direction.

**2. "AC-Lite makes Mid-Express redundant" is not established.**
Agy argues Mid-Express can be dropped because AC-Lite-Mid-Floor is wired on the same
floor. Measured client distribution does not support that: the APs are carrying 5, 5,
3, 2 and 1 clients respectively, and earlier in the week AC-Lite was carrying **zero**.
Co-location on a floor is not proof of equivalent coverage. Treat Mid-Express as
load-bearing until you have walked the house with it off.

**3. Reboot persistence is asserted, not tested.**
Agy says the disable "survives reboots (written to persistent UCI flash)". The UCI
flag certainly persists; whether the boot path honours it is a separate question, and
given finding 1 I would not assume it. Worth one test before you rely on it.

---

## Recommended scheduling design

### GL routers — cron, verified working

No native scheduler exists: GL's System → Scheduled Tasks offers only *LED Display
Schedule* and *Schedule Reboot*. Cron is enabled and already in use, so:

```sh
# on the BE9300 (192.168.9.1)
30 23 * * *  /sbin/wifi down wifi0; /sbin/wifi down wifi1; /sbin/wifi down wifi2
30 06 * * *  /sbin/wifi up wifi0;   /sbin/wifi up wifi1;   /sbin/wifi up wifi2
```

Both directions verified without a reboot. Do **not** use `uci set ... disabled` for
this — it is what does not work.

**One safeguard I would add:** a `wifi up` at boot regardless of schedule, so a power
blip at 02:00 never leaves the house without WiFi until 06:30.

### UniFi — the scheduler will not do what you want

Ubiquiti's scheduler disables the SSID only; radios keep transmitting. For genuine
RF-off you need either per-device disable (`PUT /rest/device/<id> {"disabled": true}`)
driven by cron, or PoE port scheduling on the upstream switch, or an outlet timer.

Given the UDR hosts the controller and must stay powered, the clean split is:
**software-disable the UDR's own radios, and PoE- or timer-schedule the APs.**

---

## Mid-Express: the one dependency to plan around

It uplinks over RF to the UDR (`uplink_type=wireless`), so UDR radios off = Mid-Express
and its clients offline. Your instinct is the right mitigation: because the outage is
time-boxed, it re-associates on its own when the radio returns — no manual recovery.

If you would rather remove the dependency entirely, getting a cable to Mid-Express
turns it into an ordinary wired AP and also lets you disable the UDR's radios
permanently, which is the bigger prize.

---

## Verification evidence

- 6 GHz test target chosen deliberately: 0 clients on all four 6 GHz VAPs
- `wifi reload` after `disabled=1`: VAPs 4 → 4 (no effect)
- `wifi down wifi2`: VAPs 4 → 0; 2.4 GHz 5, 5 GHz 5, internet OK
- `wifi up wifi2`: VAPs 0 → 4, all SSIDs and bridges correct
  (`wlan2`→`br-lan`, `wlan21`→`br-guest`, `wlan22`→`mld0`, `wlan23`→`mld1`)
- Post-test: `wifi2.disabled=0`, MLO intact, containers still exiting Zürich
- Apparent "lingering hostapd" during the off window was `hostapd_cli` and the global
  supervisor, not per-VAP beaconing instances

**Not verified:** whether a disabled radio's PHY is fully de-powered versus merely
having no active BSS. Vendor and OpenWrt documentation are both silent on this, and
the forums contradict each other. What is proven is that beaconing stops and no BSS
is served. If your goal is RF-exposure reduction rather than tidiness, only cutting
power is a guarantee — which is an argument for timers on the APs specifically.
