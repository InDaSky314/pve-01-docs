# Faster stall recovery — experiment design (2026-09-03)

**Goal:** less lost content when a recording stalls. Today the gap is ~4m43s, measured on the
2026-09-03 Brewers @ Cubs recording.

## Where the 4m43s actually goes

| Phase | Cost | Governed by |
|---|---|---|
| Detection | **221s** | `STALL_STRIKES_REQUIRED=2` x `STALL_MIN_CHECK_GAP=80s`, on a 90s watchdog tick |
| Cancel + create timer | 2s | Jellyfin API |
| **Jellyfin timer quantization** | **60s** | Jellyfin schedules to the next minute (`Timer will fire in 0.999971235 minutes`) |

Two things follow, and they matter more than any clever idea:

* **Detection is the dominant cost**, not reconnection. Optimise there first.
* **The 60s quantization is a hard floor.** Jellyfin's API exposes no "reconnect stream"
  primitive for an active recording — only delete and create. So ~60s is unavoidable while
  recovery means "make a new timer". No probe design removes it.

## Option A — tighten the sample gap (cheap, no tuner risk) — TEST FIRST

The 2-strike rule exists to avoid false restores. **The strikes are not the slow part — the
80s gap between samples is.** Keeping 2 strikes but sampling every 30s preserves the
false-positive protection while cutting detection from ~160s to ~60s.

The safety margin survives easily. Per `sports-dvr-auto`'s own notes this system records at
~600-750 KB/s, so:

| Sample gap | Healthy growth | vs `STALL_MIN_GROWTH_BYTES` (2 MB) |
|---|---|---|
| 80s (today) | ~48 MB | 4% of observed growth |
| 30s (proposed) | ~20 MB | 10% of observed growth |

Still an order of magnitude of headroom. Estimated total: ~60s detection + 2s + 60s = **~122s**,
down from 283s, with **no new mechanism, no extra provider connection, and no tuner risk.**

This is the option to try first. It is strictly cheaper and safer than Option B, and if it
delivers, Option B may be unnecessary.

## Option B — channel probe on first strike (needs the safety question answered)

Restore on ONE strike when a probe says the channel is alive. Would reach ~80s + 2s + 60s =
**~142s** — *worse than Option A*, and it carries risk Option A does not.

Two open disputes, both testable rather than arguable:

* agy claims probing the channel URL risks provider concurrency rejection or account lockout,
  because the subscription allows **one** concurrent stream.
* The owner counters that backing out and re-clicking in Jellyfin is exactly a fresh connection
  to the same URL and has never caused a lockout.

The distinction that likely matters: **a client re-click tears the old connection down first;
a probe during a stalled recording may not**, because Jellyfin is still retrying. That is the
difference between one connection and two — and it is measurable.

A further objection stands regardless of concurrency: the provider sits behind Cloudflare, so
an API/edge probe can return `200` while the specific channel's transcoder is dead. Any probe
must therefore hit the **channel stream URL**, not `player_api.php`.

## Test plan

Maintenance window only. Abort on any `4xx`/`429` from the provider. Never exceed two
concurrent connections. Use a channel nobody is watching and that has no timer.

**Phase 0 — is concurrency actually rejected? (no recording involved)**
The cheapest decisive test, and it settles the dispute without risking a recording.
1. One `curl` to the channel stream URL, read a few hundred KB, close. Expect `200`.
2. **Two concurrent** connections to the same URL. Record whether the second is refused, and
   with what status. This alone answers the lockout question.
3. Repeat once after a pause, to confirm no account penalty followed.

**Phase 1 — probe alongside a healthy recording**
Start a short test recording on that channel, and while it is writing, probe the same URL.
Does it succeed, or does the provider refuse the second connection?

**Phase 2 — probe during a real stall**
Hardest to stage, because stalls cannot be induced cleanly without breaking the stream. Prefer
to instrument and **wait for a natural stall** rather than manufacture one. Capture: whether
the stalled recording still holds its connection open, and whether a probe succeeds at that
moment.

**Phase 3 — confirm the floor**
Instrument the restore path with precise timestamps to confirm the 60s quantization empirically
rather than from one log line.

## Decision rule, set in advance

* Phase 0 shows concurrency is refused -> **Option B is dead.** Ship Option A alone.
* Phase 0 shows concurrency is fine, but Option A already reaches ~122s -> ship Option A, and
  treat Option B as unnecessary complexity unless Phase 2 shows stalls that Option A misses.
* Option A causes false restores in practice -> revert the gap and reconsider.

## Reversibility

Option A is a single constant. `STALL_MIN_CHECK_GAP` returns to `timedelta(seconds=80)` and
behaviour is exactly as before. The stallwatch timer cadence (90s) may need to drop to ~30s to
match, which is a one-line unit change. Both are recorded here before any edit.

## Prerequisite, now met

Faster restores mean more segment splits, so **stitching had to be trustworthy first.** The
2026-09-03 fixes (cycle-safe chain walk, per-component root resolution, and the writer guard
that stopped creating circular links) landed before any of this is attempted. Without them,
halving detection latency would simply have produced more unstitched recordings.

## Phase 0 RESULTS — run 2026-09-03, maintenance window, nothing recording

**Both predictions were wrong, and Option B is dead for a third reason neither of us raised.**

### Direct provider stream URLs are not reachable at all

A `GET` to the provider's own `\live\<user>\<pass>\<id>.ts` from CT 105 (the container whose
Swiss egress the account expects) returns **HTTP 511** — every time, under three different
User-Agents including `MediaCoreSync/1.0`, which is the one the provider's API requires:

```
UA=MediaCoreSync/1.0            -> status=HTTP 511 bytes=0
UA=Lavf/60.16.100               -> status=HTTP 511 bytes=0
UA=VLC/3.0.20 LibVLC/3.0.20     -> status=HTTP 511 bytes=0
```

`511 Network Authentication Required` is a hard rejection *before any stream is established* —
`active_cons` never rose above 0 during any attempt. The URL construction was verified correct
against the generated playlist, and the provider API answers normally from the same container
at the same moment, so this is not a credential, egress or URL-shape problem.

**Consequence:** the "probe the channel stream URL" design in Option B **cannot be implemented
as specified.** Not because of concurrency limits (agy's objection) and not safely-and-fine
(the owner's expectation) — the endpoint simply refuses out-of-band clients. Threadfin
evidently reaches the stream by some other path; establishing what it does differently is a
separate research question and was not chased here.

### A better signal exists, and it is free

The provider API reports connection state directly:

```
GET /player_api.php?username=...&password=...
  -> user_info: status=Active  active_cons=0  max_connections=1
```

`active_cons` is a **zero-risk, zero-cost** probe — an API call, not a stream. It answers the
question that actually matters during a stall: *is our one connection still held open?*

* `active_cons=1` while a recording is stalled -> the connection is still held; Jellyfin is
  retrying into a dead socket, and cancel-and-recreate is genuinely needed.
* `active_cons=0` while a recording is stalled -> the connection is already gone; a restore can
  proceed immediately with no risk of contending for the single slot.

That is strictly better than probing the stream: it needs no second connection, cannot trip a
concurrency limit, and unlike a Cloudflare-fronted API health check it reports **our own**
session state rather than the platform's.

It does **not** report whether the specific channel is healthy — agy's Cloudflare objection
still stands for that question, and nothing found here answers it.

### Decision

Per the decision rule set in advance:

* **Option B (channel-URL probe): DEAD.** Not implementable — the endpoint returns 511.
* **Option A (tighten `STALL_MIN_CHECK_GAP` 80s -> 30s, keep 2 strikes): the path forward.**
  ~122s vs 283s today, no new mechanism, no provider connection, no tuner risk.
* **`active_cons` is adopted as a supporting signal**, not a gate — useful for deciding whether
  a restore needs to wait for the slot to free, and worth logging on every stall regardless so
  we build up real data before relying on it.

Phases 1 and 2 as originally written are moot; there is nothing to probe. What replaces them:
instrument `active_cons` at stall-detection time and observe it across several real stalls
before changing any behaviour based on it.

