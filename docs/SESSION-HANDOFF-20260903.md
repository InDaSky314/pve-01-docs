# Session handoff — 2026-09-03

Live state, in-flight work, and what to pick up next. Written because the working session was
near its context limit.

## URGENT operational knowledge

**Streams return `HTTP 511` while `player_api.php` authenticates fine = the VPN exit IP is
blocked by the provider. It is NOT the account.**

Fix (~1 minute), and it WILL recur because Surfshark rotates addresses:

```bash
# bounce wgclient1 (tunnel_id 2430) to draw a new Zurich exit
ssh root@192.168.9.1 "ubus call gl-session call '{\"module\":\"vpn-client\",\"func\":\"set_tunnel\",\"params\":{\"tunnel_id\":2430,\"enabled\":false}}'"
sleep 8
ssh root@192.168.9.1 "ubus call gl-session call '{\"module\":\"vpn-client\",\"func\":\"set_tunnel\",\"params\":{\"tunnel_id\":2430,\"enabled\":true}}'"
# then confirm a NEW ip and that a stream returns 200
pct exec 105 -- wget -qO- https://api.ipify.org
```

2026-09-03: `89.37.173.42` was blocked (511 on everything, ~7h outage). Bounced -> got
`156.146.62.50` -> streams returned `HTTP 200` immediately.

Ruled out by measurement, do not re-chase: subscription (Active, auth=1, expires 2026-10-28);
VPN down (handshake live, counters moving, general internet 200); DNS (API and streams resolve
the same host, API worked over it); tuner contention (`active_cons 0`); a "datacenter ranges are
blocked" theory (**disproved** — owner's Surfshark *app* on Switzerland `185.212.170.126`
streamed fine while our Swiss tunnel IP got 511; same country, same VPN vendor, different IP).

## State as of handoff

* CT 105 / CT 112 egress `156.146.62.50` (Zurich) — **streaming verified working** (HTTP 200 + bytes)
* All timers running (6 that had been paused were restarted)
* **Saturday Bayern recording armed**: `2026-09-05T15:30Z`, ch 1011 Sky Sport Bundesliga HD,
  17:28-22:15 CEST. This is the thing that must not break.
* Shutdown override holds the host awake until 2026-09-09 12:00
* Jellyfin 10.11.11 (CT105+CT112), Grafana 12.4.10, Loki 3.7.7, Alloy v1.19.2, Prometheus v3.14.0
* `main` at commit `6ad636d` + later commits; both GitHub remotes in sync

## In flight when the session ended

1. **agy task `provider-healthcheck-20260903`** — adding `provider_api_up`,
   `provider_stream_up`, `provider_active_cons`, `provider_days_to_expiry` to
   `/root/bin/stack-monitor.py`, plus `docs/provider-ip-block-20260903.md`. Verify its work,
   then commit. Report lands in `/root/agy-reports/`.
2. **ffmpeg reconnect experiment — STILL UNRESOLVED after three attempts.** Each failed for a
   different reason, and each nearly produced a false conclusion:
   * attempt 1: provider was down (511) — tested nothing, but reported "reconnect FAILED"
   * attempt 2: added a flow gate, correctly aborted (stream never started)
   * attempt 3 (2026-09-03 11:47): stream DID flow, but ffmpeg reported `speed=509x` and
     finished the full `-t 300` in ~12s with **0 reconnect attempts**. That channel serves a
     cached/looped chunk, not a 1x live feed, so ffmpeg exited normally before the sever
     mattered. "did not recover" was a completed process misread as a stalled one.

   **For the next attempt: verify the channel streams at ~1x BEFORE severing.** Check ffmpeg's
   `speed=` field — it must be near 1.0x. Anything much above that is not a live feed and the
   test is void. Pick a genuine live channel (news/sport currently airing), confirm ~1x for
   30s, and only then sever.

## Next: the manual capture tool (owner wants this)

Build only if the reconnect experiment shows a clean in-file recovery.

Design agreed: **manual, opt-in, no scheduler, no lock broker.** Owner records mostly overnight
so watch-while-recording does not matter to him — that removed the strongest objection.
Post-processing carries over unchanged: `process-queue.py` / comskip operate on files, so an
ffmpeg-produced `.ts` enqueues exactly like a Jellyfin one, and if reconnect works there is
nothing to stitch.

Do NOT build a second scheduler. Reasons, all established: one tuner (`max_connections=1`) means
a lock bug loses BOTH recordings; precedent exists (`pre-recording-guard` produced 18 fragments
from one 30-minute programme); DTS corruption risk on streamcopy reconnect (2026-08-17 incident,
ffprobe reported 17.3h for a 3h game).

## Facts worth not re-deriving

* **Threadfin is a pure redirector** (`buffer: "-"`). Its `/stream/<id>` returns `302` to
  `http://cf.teltv.xyz/live/<user>/<pass>/<id>.ts`. It does no auth of its own — the
  credentials ARE in the URL path. There is no Threadfin hop in the data path.
* The provider URL is **not tokenised** — static path, no query string, identical across
  requests. So `-reconnect` can reuse it.
* **ONE tuner.** `TunerCount: 1`, `max_connections=1`. `active_cons` LAGS and reported 0 while
  connections were plainly in use — do not trust it as a mutex. Careless probing starves the
  tuner and produces spurious 511s; several hours were lost to self-inflicted contention.
  Coordinate explicitly with the owner before any stream test.
* Stall recovery is now ~122s (was 283s): `STALL_MIN_CHECK_GAP` 80s->30s and stallwatch cadence
  90s->30s. Jellyfin quantizes new timers to the next minute — a hard ~60s floor.
* ESPN has **no** Bundesliga team schedules; fixtures come from OpenLigaDB. ESPN 403s datacenter
  IPs but works from the host's residential line, where UCL and all US sports still work.

## Open items

* DirecTV channel group failed even on a working exit — likely a genuine provider-side issue,
  separate from the IP block. Worth raising with them.
* Pre-commit `gitleaks` hook + scheduled secret scan (proposed, not installed).
* Stale `GF_SECURITY_ADMIN_PASSWORD=changeme-initial-setup` in the compose file — confirmed
  dead, but it reads like a live credential.
* NextPVR: 657 Crit/High from an unpatched Debian 12 base; pinned tag is already current
  upstream. Accepted and documented.

## MCT built 2026-09-03 — status and what still needs doing

`/usr/local/bin/mct` exists and dry-runs correctly. **It has never captured anything live.**

Verified by Claude Code:
* exclusion works **both** ways — refuses against Saturday's real Bayern timer
  ("REFUSING CAPTURE: Overlapping Jellyfin timer"), passes on a clear window
* proven reconnect flags, pre-remux step, and comskip enqueue all present in the emitted commands
* dashboard still serves `/api/status` and `/api/schedule` with `problems: []`
* Saturday Bayern timer untouched

Fixed during inspection: MCT **logged the provider URL including credentials**, which would
have reached journald and Loki. Now redacted to `/live/<REDACTED>/<REDACTED>/<id>.ts`.
Backup at `mct.pre-redact-20260903`.

### Before trusting it
1. **Do a real capture on a throwaway game** — it has only ever dry-run.
2. Confirm the file lands where Jellyfin ingests it, that the `.nfo` is accepted, and that
   comskip actually picks it up end to end.
3. Confirm scheduling fires at kickoff (check what mechanism was wired; verify a booked game
   actually starts).
4. Only then use it for something wanted. **Do not use it for the 2026-09-05 Bayern match** —
   that stays on Jellyfin, which is proven.

