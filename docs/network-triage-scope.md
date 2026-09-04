# Scope: `network-triage` — deterministic outage diagnosis and targeted recovery

**Status 2026-09-04:** scoped, not built. Intended as the first item agy builds once the
Gemini quota resets, with Claude Code reviewing.

## Why this before a local LLM

The owner's motivating case is "troubleshoot a network outage when Claude Code and agy are
unreachable". For that, certainty beats cleverness: the diagnosis is a decision tree we already
know, and a script answers it in seconds using no CPU. A 1.7B model on an N5105 would answer it
slowly and sometimes wrongly. The LLM belongs on top of this as a natural-language layer, not
in place of it.

## The concrete problem this must solve first

The owner has had to **reboot the router** several times because "the provider PPPoE tunnel
just sits there". A full reboot is the heaviest possible remedy: it drops WiFi, LAN, every VPN
tunnel and the media stack's egress, and takes minutes.

Findings from a read-only inspection of the GL.iNet BE9300 on 2026-09-04:

* WAN is `proto=pppoe` on `eth0`, l3 device `pppoe-wan`.
* **LCP keepalive is already aggressive**: pppd runs with
  `lcp-echo-interval 1 lcp-echo-failure 5 lcp-echo-adaptive`. The router would notice a session
  whose peer stops answering within ~5s.
* Therefore the hang is almost certainly **not** "the router failed to notice a dead session".
  It is the classic **zombie PPPoE**: the session is `up`, LCP echoes are answered by the
  provider's BRAS, but no traffic routes. LCP cannot detect this by design, so pppd keeps the
  session and the router sits there exactly as described.

**The detection therefore has to be end-to-end reachability, not link state.** Ping/curl a
known external target through the WAN; if that fails while `network.interface.wan` reports
`up`, the session is a zombie.

**And the remedy should be targeted, not a reboot:**
```
ubus call network.interface.wan down && ubus call network.interface.wan up
# or: ifdown wan; ifup wan
```
This re-establishes PPPoE in seconds and leaves WiFi, LAN and the LXCs untouched. A reboot
should be the last resort, not the first.

## What to build

A single script, host-side, safe to run at any time:

1. **Layered checks, each reported separately** so the failure is located, not guessed:
   host link -> router reachable -> WAN state -> **end-to-end egress** -> DNS ->
   VPN tunnel state -> provider reachability -> Jellyfin/Threadfin health.
2. **A plain-English verdict** naming the layer that failed and the single next action.
3. **`--fix` as an explicit opt-in**, never automatic. The only fix in v1 is the targeted WAN
   bounce above, and it must refuse while a recording is in progress - bouncing the WAN kills
   an active capture. Reuse the same reservation data the tuner broker reads.
4. **JSON output** (`--json`) so a future LLM layer or dashboard can consume it.

## Prove it in both directions
A healthy system must produce a clean PASS with no action suggested. Each failure mode must be
provoked or simulated and produce the right layer attribution - a DNS failure must not be
reported as a WAN failure. `--fix` must refuse during a recording and proceed when idle.

## Explicitly out of scope for v1
Automatic remediation without `--fix`. Rebooting the router. Anything that touches the tuner.

## Note on credentials
`uci show network.wan` prints the PPPoE username and password in plaintext. The script must
never log, print, or persist that output. Redact as `mct` does for provider stream URLs.
