# Reducing footage lost to network blips — experiment list

**Status: none of these are done.** Written 2026-09-09 after root-causing the
2026-09-06 Brewers restart. Ordered by value-per-risk, highest first.

## The finding that drives all of this

The 2026-09-06 mid-capture restart was **not** a stream failure:

```
[2026-09-06 05:37:29] WARNING: ffmpeg capture exited prematurely on segment 1
                      with code 0: cf.teltv.xyz: Temporary failure in name resolution
```

DNS. The same night, ESPN API calls timed out between 01:07 and 01:13 — the same
window. The provider stream was fine; the box briefly could not *find* it.

This matters because **ffmpeg's `-reconnect` family does not cover name
resolution**. It retries the socket, not the lookup. MCT passes six reconnect
flags and every one of them was useless against this failure class. Note also
that ffmpeg exited **code 0** while failing, which is why the restart guard has
to detect death by content growth rather than by exit status.

Owner's read — "the IPTV stream is there, it just needs to be kick-started" — is
correct, and the kick-start machinery already works (restart took 5.0s and
preserved a 9.03 GB segment). The remaining win is in **not losing resolution in
the first place**, not in recovering faster.

---

## 1. Local caching resolver inside CT 105  — RECOMMENDED, do first

CT 105 runs `nameserver 192.168.9.1` with **no local cache**, so every ffmpeg
reconnect re-resolves against the BE9300, whose per-tunnel dnsmasq dies with the
tunnel. A tunnel blip therefore breaks name resolution for an in-flight capture.

**Change:** a local dnsmasq or unbound in CT 105, forwarding to `192.168.9.1`,
with a long `min-cache-ttl` (~3600s) so a brief upstream outage cannot break
resolution for a host that was resolving seconds earlier.

* **Helps:** both engines, every capture, every reconnect.
* **Risk:** low. Additive; `resolv.conf` reverts in one line.
* **Does not change:** which tunnel DNS goes down — CT 105 keeps using the
  router, so the Zürich-tunnel DNS policy is untouched. Verify that explicitly
  after the change; a resolver that bypasses the tunnel would be a leak.
* **Acceptance:** with the Zürich tunnel bounced mid-capture, `getent hosts
  cf.teltv.xyz` still answers from cache and the capture does not segment.
* **Back-out:** restore `/etc/resolv.conf`, stop the resolver.

## 2. Lower MCT's `reconnect_delay_max` from 120s — trivial

`/usr/local/bin/mct` uses `-reconnect_delay_max 120`, so exponential backoff can
leave a late retry idle for two minutes. Threadfin's own (unused) ffmpeg options
use `5`. Suggest ~15s: still polite to the provider, far tighter worst case.

* **Risk:** very low. One constant, two call sites (lines ~1255 and ~1371).
* **Acceptance:** a capture still survives a deliberate tunnel bounce.

## 3. Threadfin `buffer = ffmpeg` — CONSIDERED AND DEPRIORITISED

The idea: Threadfin is currently `buffer = "-"`, a pure redirector — its
`/stream/<id>` returns `302` to the provider and it leaves the data path
entirely ("Threadfin is no longer involved, the client connects directly").
It nonetheless carries a fully configured, completely unused ffmpeg profile:

```
-reconnect 1 -reconnect_streamed 1 -reconnect_on_network_error 1
-reconnect_on_http_error 4xx,5xx -reconnect_delay_max 5 -rw_timeout 10000000
-err_detect ignore_err -fflags +genpts+discardcorrupt
```

Switching to `buffer = ffmpeg` would put that reconnect layer in front of every
client — the resilience Jellyfin's recorder structurally lacks, since it does a
raw byte copy with no reconnect at all.

**Why it is deprioritised anyway** (owner raised the concern 2026-09-09; prior
sessions already documented the mechanism):

* **`TunerCount: 1`.** In redirect mode Threadfin is not gatekeeping playback,
  so a channel change simply drops one direct provider connection and opens the
  next. In buffer mode Threadfin *holds* the connection and must tear its ffmpeg
  down before granting the next — and **"Threadfin returns HTTP 404 when its
  single tuner is busy — looks exactly like a missing stream"**
  (`livetv-audio-rootcause-20260726.md` §5). Channel surfing would race that
  release window, and the failure presents as a dead channel.
* **`active_cons` cannot be used to arbitrate.** It lags and has reported 0
  while connections were plainly in use (`SESSION-HANDOFF-20260903.md`).
* **The beneficiary is the engine we are moving away from.** MCT already has its
  own reconnect and restart-and-stitch; the gain would land almost entirely on
  Jellyfin recordings, which are no longer the recommended engine for games.

So the cost falls on everyday viewing and the benefit falls on a path being
retired. **Not scheduled.** Revisit only if Jellyfin becomes the primary
recording engine again.

If it is ever revisited, test in this order, and **coordinate with the owner
before any of it** — careless tuner probing has cost hours before:
1. Confirm nothing is recording and the tuner is idle.
2. Set `buffer = ffmpeg`, restart Threadfin.
3. Surf 10 channels back-to-back in Wholphin, timing each change and counting
   404s. Compare against a redirect-mode baseline captured the same way.
4. Only if surfing is unaffected, trial a throwaway 30-minute recording.
5. Back out by restoring `settings.json` (note the existing stock backup,
   `settings.json.bak-20260726-084846`) and restarting Threadfin.

Prior art: `buffer: ffmpeg` was set temporarily on 2026-07-26 during audio
normalisation testing and fully reverted. The revert was because that testing
ended, **not** because buffering misbehaved — there is no recorded evidence
either way on the surfing question. That is exactly what step 3 above is for.

---

## Already tuned — leave alone

* **Stall detection**: 30s cadence x 2 strikes, ~60s floor, tightened from ~160s
  on 2026-09-03. Near its sensible floor already.
* **Restart backoff**: 5.0s, measured on the 2026-09-06 restart.
* **The provider URL is not tokenised** — static path, no query string, so
  `-reconnect` can legitimately reuse it. Do not re-derive this.
