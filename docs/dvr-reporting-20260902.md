# DVR schedule sourcing and automated reporting — 2026-09-02

Follows the media-core health sprint of 2026-08-31 (`docs/media-core-sprint-20260831.md`).
Claude Code briefed and verified; agy executed each build in `build` mode. Every item had a
recorded pre-state and a back-out plan before it ran.

## What prompted it

The owner asked why Bayern Munich had no schedule on the DVR dashboard. It turned out the
2026-09-05 Schalke–Bayern match was real, three days away, **and had no recording timer** —
he would have missed it. Investigating that surfaced two further problems and one hardware
behaviour that matters more than any of the software.

## 1. ESPN cannot supply Bundesliga fixtures — replaced with OpenLigaDB

Full detail in `lessons-learned.md`. Summary: `soccer/ger.1/teams/132/schedule?seasontype=2`
returns **0 events**, and the scoreboard only carries the current matchday slice, so a fixture
three days out is invisible. The dashboard's config was never wrong.

`https://api.openligadb.de/getmatchdata/bl1/<year>` is free, unauthenticated, and returns the
full season — 306 matches, 34 of them Bayern. Bayern's schedule went **1 → 35 fixtures**.
UEFA Champions League stays on ESPN, whose scoreboard genuinely does return a full slate.

Fixtures now appear **whether or not the EPG has them yet** — the season runs months ahead of
the ~54-day guide window. A fixture with no broadcast match shows `not in guide yet` rather
than being dropped, which is what previously made the whole schedule look empty.

Where the EPG *does* have it, the fixture is enriched with the real broadcast: channel number,
channel name, and the actual coverage window. Two traps are documented in `lessons-learned.md`
— match on the **sub-title** (the title is the generic `Fußball: Bundesliga`), and require the
programme window to **contain the kickoff**, because Sky opens coverage an hour early and
because `Klassiker der Woche: … (2014/2015)` archive reruns otherwise match on name alone.

## 2. Channel number and affiliation in the UI

Owner request: show what channel a recording is on, in the terms Wholphin shows. Wholphin is a
Jellyfin client, so its channel number **is** Jellyfin's `ChannelNumber`. Both the recordings
list and the schedule rows now render `"<num> <name>"` — e.g. `1011 Sky Sport Bundesliga HD`,
`101 Madison: NBC 15 (WMTV)`. The network affiliation is already inside the channel name, so it
is surfaced as-is rather than parsed out and risked.

## 3. "other recording overlaps" was a label bug

A timer only carries the `"<Team>:"` name prefix when `sports-dvr-auto` created it, so any
hand-made timer displayed as a foreign overlap **on its own game**. Fixed generally — channel
comparison first, then the team's leading token for the ESPN-sourced US teams that carry no
resolved channel. See `lessons-learned.md` for why the per-team string-matching version that
was tried first is the wrong shape.

## 4. Post-recording quality report (`dvr-recording-report`)

Runs every 5 minutes; reports each completed recording **exactly once**, and only after
post-processing has actually finished (it gates on the comskip queue, the runner lock, and the
work dirs — not on a timer guess).

Reports verdict-first — GOOD / CHECK THIS / FAILED — then: title, subtitle, channel number and
name, scheduled vs actual runtime, comskip breaks and commercial minutes removed, whether a
stall/restore/stitch fired, and both file paths. **Both copies are always preserved**; the
report says so explicitly so it can never be read as "the original was replaced".

On anomaly only, it dispatches **agy in diagnose mode** to root-cause across the Jellyfin log,
tuner contention (this account has ONE concurrent stream), boot history, and provider health,
and includes the finding in the mail.

**Known characteristic, not a bug.** The runtime baseline is the timer's unpadded window. A
recording created from a programme grades correctly. A **manual** timer set far longer than the
actual broadcast will grade short and be flagged — that is what happened to the 2026-08-28
Bayern–Stuttgart recording (3h18m against a hand-set 4h28m window). Nothing was wrong with it;
agy's post-mortem said so correctly. Expect this on generously-padded manual timers, and read
the post-mortem before acting.

Strictly non-destructive: it only reads. A recursive diff of the recordings tree before and
after a full test run showed only the runner lock's timestamp changed.

## 5. Pre-flight digest and backup restorability

See `scripts/` for both. The pre-flight digest exists because of the 21-hour outage below — a
morning mail listing the next 48 hours, power-window violations, single-tuner conflicts, and
**tracked games with no timer set** would have caught both the outage's consequences and the
Bayern near-miss. The backup verifier exists because vzdump reporting "finished successfully"
is not evidence the archive is restorable, and nobody had ever opened one.

## 6. The hardware behaviour that outranks all of it

**The host only boots on an AC power transition.** From 2026-09-01 22:13 to 2026-09-02 19:18 it
was off for ~21 hours and every recording in that window was lost. `dvr-clean-shutdown` had
powered it down cleanly at 22:10 in anticipation of a mains cut that never came — and with no
cut, there is no restore, so nothing brought it back.

The monitoring stack cannot see this: everything it scrapes lives on the host that is off.

A multi-day hold is set through the override file, **not** `dvr-shutdown-hold`, which cannot
mask a unit that exists as a real file in `/etc/systemd/system`. The dashboard API clamps
overrides to the next cutoff, so write the file directly and verify with the script's own dry
run. Currently held to **2026-09-09 12:00** at the owner's request:

```bash
cat /var/lib/dvr-dashboard/override-until      # 2026-09-09T12:00:00+02:00
/usr/local/bin/dvr-clean-shutdown --dry-run    # override active until Wed 12:00 -- staying up
```

Restore normal behaviour by removing/expiring that file when the mains timer is back in use.

## Still open

* **The mains timer is a dumb mechanical one.** The Shelly smart plugs remain an unpurchased
  shopping item in `home-network-power-automation.md`. Until then, a missed power cycle is
  silent and costs every recording that day. This is the weakest link in the chain and no
  amount of scheduling software fixes it.
* **Detecting a missed boot has to come from outside the host.** Nothing on pve-01 can alert
  on pve-01 being off.
* Tonight's DFB-Pokal match (VfL Osnabrück v Bayern, 2026-09-02) is **not carried live** on
  this lineup — only a Sportschau highlights slot. `not in guide yet` was accurate.

## 7. Egress topology — three sources, three different exits

The Bayern English-PPV detector had been running every 15 minutes, exiting 0, and doing
nothing, because it got **both** of its network paths wrong. Nothing in the code made the
constraints explicit, so this is the table to check first when a data source "just stops".

| Source | Required egress | Why |
|---|---|---|
| OpenLigaDB (fixtures) | any | free, no auth, no geo restriction |
| Provider Xtream API | **CT 105 only** — Swiss, `User-Agent: MediaCoreSync/1.0` | the account expects that egress; anything else is unreachable/403 |
| ESPN (UCL only) | **host only** — residential German IP | ESPN 403s datacenter ranges |

Measured 2026-09-02:

| Path | IP | ESPN result |
|---|---|---|
| host | `<redacted: residential WAN IP>` (German **residential**, Telekom) | 200 OK |
| CT 108 scraper | `45.144.115.131` (Ashburn VA, Clouvider AS62240 — **datacenter**) | **403 Forbidden** |
| CT 105 | `156.146.62.42` (Swiss datacenter) | blocked/empty |

**Note the host is NOT on a US tunnel** — it egresses the bare German WAN. That assumption
cost real time; check `curl https://api.ipify.org` before reasoning about any egress.

### Routing ESPN through the scraper's US tunnel does not help

The idea is reasonable — CT 108 exists precisely because its target sites reject the Swiss IP
— but it fails twice over. ESPN 403s the Ashburn datacenter range, and more importantly
**ESPN is not geo-restricting us at all**: `ger.1/scoreboard` returns real current Bundesliga
fixtures from the German residential IP. The empty result is confined to
`/teams/<id>/schedule`, an endpoint ESPN does not populate for soccer teams. No exit IP can
fill an endpoint that has no data.

Tunnel 937 (`ovpnclient1`, United States - New York, UDP) also **cannot** be reused for this:
its `from` is `{"type": "interface", "interface_list": ["guest"]}` — bound to the GIOT SSID,
not MAC-based. Adding a MAC would mean changing its binding type and breaking GIOT. A dedicated
`ovpnclient2` with its own MAC-bound tunnel would be required, and given the 2026-08-27/28
cutover took the whole media stack offline through stacked VPN-firewall faults, that is a
change to make deliberately and verify against the scrapers — not as a side effect of chasing
an ESPN endpoint that is empty for everyone.

## 8. Self-correcting repair loops (report-only until armed)

The first two jobs permitted to change production state on their own. The guardrail is
deliberately **not** the agent's judgement — it is a metric measured before and after, with
automatic revert when it does not improve.

| Loop | Grader | Rollback | Timer |
|---|---|---|---|
| `epg-repair-loop` | `epg_real_channels` on :9105 | timestamped `config.json.bak-epgrepair-<stamp>` | daily 02:15 |
| `icon-repair-loop` | `icon-verify` custom-icon count | `icon-archive export` + extracted-dir backup | daily 03:15 |

Rules both enforce: rollback captured **before** acting; acting requires `--apply` **and** an
open maintenance window (01:00-05:00, via CT 105's `sync/maintenance_window.py`, which
auto-tightens around scheduled recordings); no tuner access; no changes to recordings or
timers. **Both ship report-only** — the `--apply` flag is not in the unit files. Arm them
deliberately.

The revert path was proven end-to-end against production rather than asserted: Loop A captured
a backup, dropped `epg_ripper_US_LOCALS1` from the live config, ran a real sync, watched
`epg_real_channels` fall **424 -> 380**, detected the non-improvement, restored the backup,
re-synced, and confirmed 424. A loop that can act but cannot revert is worse than no loop.

New artwork *generation* is deliberately excluded from Loop B — the runbook records that
batches of 12 produced cropped text and invented words, so generation stays batched at 6 and
stays a human call. Loop B proposes those instead (75 candidates at the time of writing,
mostly `BBC Stream N` placeholders).

**Do not "fix" a source reporting `matched 0/N`.** Sources merge in priority order and a later
one legitimately reports 0 when an earlier one already covered those channels; the README calls
several of them "structurally always near-zero". Only genuinely unreachable sources count.
That misreading produced two false findings in the 2026-08-31 audit.


## 9. CT 108 scraper moved from WireGuard to OpenVPN (2026-09-02)

Owner request, for scraper reliability — his recollection was that the scrapers behaved
better on OpenVPN before the 2026-08-27/28 router cutover. The measured exit IP supports
that: the new tunnel lands in `151.240.254.x`, the same range `lessons-learned.md` records
as "the Ashburn exit" (`151.240.254.18`) from before the cutover.

**Deliberately held location constant** and changed only the protocol, so the experiment has
one variable: `us-ash` (Ashburn) on both sides.

| | before | after |
|---|---|---|
| tunnel | 3742 `US-Ashburn (WG)`, wgclient3 | **4941 `US-Ashburn (OVPN) scraper`**, ovpnclient2 |
| exit IP | `45.144.115.131` (Clouvider AS62240) | `151.240.254.11` (Cyberzone AS209854) |
| CT 107 | shares 3742 | **unchanged, still on 3742** |

### Method

`gl-session call` over SSH, never raw `uci` — the runbook is explicit that raw edits to
`route_policy`/`openvpn` silently revert. **This was the first WRITE proven through that
path**; the runbook's own "Not yet verified" section listed `add_tunnel`/`set_tunnel` as
untested over SSH. They work, and behave as documented.

The undocumented piece — resolving a server to its `group_id`/`id_list` — is
`ovpn-client.get_config_list` with `{"group_id": 1792}`. It returns 282 Surfshark profiles
with `client_id` and `name`. East-coast UDP options: Boston 228, New York 230 (in use by
GIOT), Buffalo 232, **Ashburn 234**, Charlotte 240, Atlanta 246.

```bash
# 1. create (lands DISABLED and inert -- safe first write)
ubus call gl-session call '{"module":"vpn-client","func":"add_tunnel","params":{
  "via":{"type":"openvpn","configs":[{"group_id":1792,"id_list":[234]}]},
  "from":{"type":"mac","mac_list":["BC:24:11:28:55:77"]},
  "to":{"type":"default"},"name":"Tunnel 1"}}'      # -> {"tunnel_id": 4941}
# 2. drop CT 108 from the WG tunnel, KEEPING CT 107
ubus call gl-session call '{"module":"vpn-client","func":"set_tunnel","params":{
  "tunnel_id":3742,"from":{"type":"mac","mac_list":["BC:24:11:EF:79:09"]}}}'
# 3. name + enable
ubus call gl-session call '{"module":"vpn-client","func":"set_tunnel","params":{"tunnel_id":4941,"name":"US-Ashburn (OVPN) scraper"}}'
ubus call gl-session call '{"module":"vpn-client","func":"set_tunnel","params":{"tunnel_id":4941,"enabled":true}}'
```

New tunnels land **disabled**, which makes step 1 a safe way to prove the write path before
committing to anything.

### Verified after

* `Initialization Sequence Completed`; `resolv.conf.ovpnclient2` and `dhcp.ovpnclient2`
  written, and `network-buildout-persist` hotplug fired — the three things that were each
  broken during the August cutover.
* DNS resolves inside CT 108 (the zero-byte `resolv.conf` failure mode did not recur).
* **All 12 scrapers re-run on the new tunnel: 12 parse OK, 0 bad**, programme counts and file
  sizes within normal variation of the pre-change baseline
  (`/root/agy-reports/ct108-ovpn-baseline-20260902/`).
* CT 105 (`156.146.62.42`), CT 107 (`45.144.115.131`) and CT 112 unchanged; media stack
  healthy; the Saturday Bayern timer intact.

### ESPN is still 403 from CT 108 — as expected

The protocol change did not fix ESPN, which confirms the diagnosis in §7: it is
**datacenter-range blocking**, not protocol. Both exits are hosting ASNs. This does not
matter — ESPN is not used for Bundesliga any more, and UCL runs from the host's residential
line where it works.

### Back-out

```bash
ubus call gl-session call '{"module":"vpn-client","func":"set_tunnel","params":{
  "tunnel_id":3742,"from":{"type":"mac","mac_list":["BC:24:11:28:55:77","BC:24:11:EF:79:09"]}}}'
ubus call gl-session call '{"module":"vpn-client","func":"remove_tunnel","params":{"tunnel_id":4941}}'
```
Pre-change exports (tunnels, `route_policy`, VPN configs) are in
`/root/agy-reports/ct108-ovpn-baseline-20260902/`. **Those exports contain provider
credentials — they stay on the host and must never be committed.**
