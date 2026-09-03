# Evaluating an external ffmpeg recorder for sport — 2026-09-03

**Verdict: do not build it into the automated stack.** A manual capture tool is worth having;
a second scheduler is not. Reasoning below, including the parts where the experiment failed to
answer the question.

## What prompted it

The 2026-09-03 Brewers @ Cubs stall lost 4m43s and produced two segments whose stitch silently
failed. The idea: keep Jellyfin DVR for routine recording, and **add** an opt-in ffmpeg path for
high-value sport, so a stall becomes a sub-second reconnect inside one file rather than a gap
plus a stitch.

## The architectural fact that reframes it

**Threadfin is already out of the data path.** It is configured `buffer: "-"`; a request to its
stream endpoint returns `HTTP 302` with `Location:` pointing at the provider, and Jellyfin
follows that and pulls bytes directly. There is no Threadfin hop to remove — the only component
in the data path is Jellyfin's HTTP client. Any gain would have to come from a **more robust
client**, not from removing a component.

## What the experiment established

**The redirect URL is not tokenised.** Tested first, because a short-lived token would have
killed the idea outright:

```
shape        : /live/<user>/<pass>/<stream_id>.ts
query string : (none)
stability    : IDENTICAL across repeated requests
```

Static credentialed path, no expiry parameter, stable. So `-reconnect` could legitimately reuse
it across a multi-hour recording. agy had called this a **fatal** flaw ("short-lived CDN
tokens") without testing it; the measurement says otherwise.

**What the experiment could NOT establish — stated plainly.** We never reproduced an independent
fetch of a stream at all. Every attempt from CT 105 returned `HTTP 511 Network Authentication
Required`:

* through Threadfin's endpoint with ffmpeg — 511
* directly at the provider URL — 511
* with `MediaCoreSync/1.0`, `Lavf/*`, `VLC/*`, `Jellyfin/10.11.11`, and `curl/*` user-agents — 511 in every case

Meanwhile plain `curl` to the *Threadfin* endpoint returns `302` normally at the same moment,
and `active_cons` stays at 0 on the provider side throughout. **Whatever Jellyfin does to
succeed, we did not replicate it, and this write-up does not claim to know what it is.** That
is an open question, not a conclusion.

Consequently the reconnect hypothesis — does `-reconnect` + `-rw_timeout` heal a severed stream
inside one file — **remains untested**. A first run reported "reconnect FAILED", but the file was
0 bytes from the start, so it tested nothing; the retry added a flow gate that correctly
aborted rather than producing a second false result.

## The unplanned finding, which is the useful one

**Single-tuner contention is easy to trip, and I tripped it repeatedly.** Ordinary careful
probing with `curl` and ffmpeg produced `HTTP 511` three separate times, because Threadfin
advertises `TunerCount: 1` and a probe that leaves a slot occupied starves the next request.

That is direct evidence for the strongest objection to the whole proposal: if deliberate,
careful testing starves the tuner this easily, a **second automated scheduler competing with
Jellyfin would do it far more destructively.** There is precedent in this project —
`pre-recording-guard` once "cancelled and recreated healthy recordings once a minute — 18
fragments from one 30-minute programme, plus a Threadfin restart each time that broke an
unrelated recording on the other DVR."

## The dissent that stands regardless

From agy's counter-analysis, the objections that survive scrutiny:

* **`-reconnect` does not fire on a silent stall.** Our real failure mode was bytes stopping
  while the socket stayed open; `read()` blocks in the kernel and ffmpeg hangs forever.
  `-rw_timeout` is what makes reconnect reachable at all.
* **DTS/PTS corruption on reconnect.** With `-c copy`, a reconnect after an encoder timeline
  reset can produce non-monotonous DTS. Precedent in `lessons-learned.md`: the 2026-08-17
  incident where PTS corruption made `ffprobe` report **17.3 hours for a 3-hour game**. A
  corrupted 4-hour container is worse than a clean split, because segments at least stitch.
* **Loss of watch-while-recording.** Jellyfin manages live file growth so an in-progress
  recording is playable. A raw `.ts` being appended by ffmpeg is not recognised as an active
  Live TV recording — mid-game playback fails near the live edge. Also lost: the red dot on the
  EPG grid, and extend/cancel from the TV remote.
* **The gain is now small.** Today's work already cut detection ~221s -> ~60s and repaired
  stitching. The remaining Jellyfin-inherent overhead is the ~60s timer quantization floor, so
  total recovery is ~120s. A bespoke recorder competes for ~90-120s, against ~800 lines of
  process supervision, lock brokering, NFO generation and scheduling that would need
  maintaining forever.

## Recommendation

**Do not add a second scheduler.** The single-tuner race, the loss of in-progress playback, and
the DTS-corruption risk are not worth ~90 seconds.

**Do consider a manual capture tool** — foreground, explicitly invoked when Jellyfin is idle,
run inside CT 105 for the Swiss egress, with `-rw_timeout` set. No scheduler, no lock broker, no
automation. That provides a belt-and-braces option for a specific match without a second control
plane.

**Prerequisite before any of that is worth writing:** resolve why an independent client cannot
fetch the stream at all. Until that is understood, a manual tool cannot be built either.

## RESOLVED 2026-09-03 — ffmpeg in-file reconnect WORKS

Fourth attempt, first valid one. Live channel confirmed streaming at ~1x before severing
(the gate that voided attempt three), then a **60s** iptables DROP — comfortably exceeding the
15s `-rw_timeout`.

```
-reconnect 1 -reconnect_streamed 1 -reconnect_on_network_error 1
-reconnect_delay_max 10 -rw_timeout 15000000 -c copy
```

**Result: reconnected inside the same file.** Growth resumed ~3s after the network returned;
the file went 64.7 MB -> 111.1 MB with no split. Independently verified: 116,523,528 bytes,
`duration=240.047678` against `-t 240` — the requested length exactly.

That overturns three earlier conclusions, all of which were mine or agy's:

* my "reconnect FAILED" (attempt 1) — the provider was down, nothing was tested
* my near-miss on attempt 3 — ffmpeg had *completed* at `speed=509x`, not stalled
* agy's dissent that DTS corruption made this unsafe — the catastrophic case did not occur

### One correction to the pass, and it matters

agy reported "**0** DTS/PTS discontinuities". That is **not** right. Re-muxing the capture
produces **4,556** `Application provided invalid, non monotonically increasing dts to muxer`
warnings. My own follow-up grep reported "0 hard timeline errors", which was also wrong — it
searched for `non-monotonous` while ffmpeg emits `non monotonically increasing`. Two opposite
errors, both from pattern-matching instead of reading the output.

The substantive risk nevertheless did **not** materialise. The 2026-08-17 precedent was a
*catastrophic* timeline break — `ffprobe` reporting 17.3h for a 3h game. Here the duration is
exactly correct, so the DTS irregularity is the ordinary kind seen in live MPEG-TS captures,
not corruption.

**Implication:** pre-remux before comskip, which `perform_ffmpeg_concat` already does. Do not
feed a raw reconnected capture straight into post-processing without it.

### HLS is worse, not better

The provider advertises `m3u8`, and an HLS client re-fetching a playlist *sounds* more
drop-tolerant. Measured, it is not: streamcopying HLS produces missing SPS/PPS slice headers
across segment transitions. **Direct TS with `-reconnect` is cleaner.** A reasonable hypothesis,
disproved by testing.

### What this changes

Recovery for an in-file reconnect is **~3s**, against ~122s for Jellyfin's cancel-and-recreate
path — because it skips detection latency, the 60s timer quantization, and the segment split
entirely. For overnight sport, where watch-while-recording does not apply, a manual capture
tool is now genuinely worth building.
