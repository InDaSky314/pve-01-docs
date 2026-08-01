# Splitting the DVR backends: Threadfin vs NextPVR on an even playing field

Written 2026-08-01, prompted by the recording-fragmentation incident of
2026-07-31 (see `livetv-custom-build-and-updates-20260726.md` §17).

## Why this is worth doing

The argument is no longer theoretical. Both faults this week came from
sharing one Jellyfin between two tuner backends:

- **Indistinguishable duplicate channels** (§14). Two `Madison: ABC 27
  (WKOW)` entries, same name, same `SortName`. Telling them apart required
  a database query against `ExternalId`.
- **The guard incident** (§17.13). `pre-recording-guard` could not measure
  a NextPVR recording, read that as a stalled tuner, and cancelled and
  recreated the timer once a minute — 18 fragments for a 30-minute
  programme, and a Threadfin restart each time that also broke the
  unrelated Jellyfin DVR recording.

Both are the same root problem: **one Jellyfin cannot tell you which
backend produced a given result.** That is exactly what you need for an
A/B comparison, and exactly what the current setup destroys.

The guard bug is fixed, but the class of bug is not. Every piece of
automation on this box was written when Threadfin was the only recorder.

## Recommended shape

Two Jellyfin instances, one backend each, sharing nothing that can carry a
fault between them.

| | Instance A (existing) | Instance B (new) |
|---|---|---|
| Container | `jellyfin` | `jellyfin-npvr` |
| Port | 8096 | 8097 |
| Config | `/srv/media-core/jellyfin/config` | `/srv/media-core/jellyfin-npvr/config` |
| Tuner | Threadfin only | NextPVR plugin only |
| VOD libraries | all of them | **none** |
| Recordings | `/srv/media-core/media/recordings` | `/srv/media-core/media/recordings-npvr` |
| Guard automation | applies | **excluded** |

### The instance B decision that keeps this cheap

**Instance B gets no VOD libraries at all — Live TV and recordings only.**

Instance A's database is 2.5 GB across ~566k items, and a full metadata
scan is the expensive part of running Jellyfin here. Duplicating that to
compare *tuners* would be pure waste: the VOD library has nothing to do
with either backend. Without it, instance B is a small process with a
database in the low tens of MB.

This is the difference between "second Jellyfin" being a weekend project
and being a 20-minute job.

### What must not be shared

- **Recording directories.** Separate roots, so a fragmented recording is
  unambiguously attributable. This also keeps `pre-recording-guard`'s
  `/media/recordings` assumption true for instance A.
- **Config directories.** Separate `jellyfin.db`, separate scheduled-task
  triggers, separate plugin state.
- **Automation scope.** `media-core-guard` and `threadfin-tuner-watchdog`
  must only ever see instance A. Both restart Threadfin, which is
  meaningless for NextPVR and actively harmful, as we now know.

### What can safely be shared

- **Media files**, mounted read-only into B if you ever want them.
- **The EPG file.** `epg.xml` is regenerated once daily; both can read it.
- **The provider.** But see the concurrency caveat below.

## Client side

Wholphin supports multiple servers, so instance B is a second server entry
in the app and you switch between them. That gives a genuine A/B: same
client build, same TV, same network, one variable changed.

Worth doing: name them unmistakably (`media-core (threadfin)` and
`media-core (nextpvr)`). Half of this week's confusion came from two
things that looked identical.

## Caveats worth knowing before starting

**Provider connection limits are the real constraint.** Both instances
pulling the same channel simultaneously means two upstream connections.
`max_connections` on the NextPVR IPTV source is currently **1**. Whether
the provider itself caps concurrency is untested — the 10-minute pull on
2026-08-01 used a single connection and proves nothing about parallel use.
**Run the comparisons sequentially, not simultaneously**, until that is
measured. This matters more than anything else here.

**Only one should be the daily driver.** Two instances scanning, updating
guides and holding tuner connections is more load and more chances to
collide. Instance B is a test rig, not a second production system.

**Guide duplication disappears** — each instance sees one source, so the
twin-channel problem in §14 simply stops existing.

**Recordings do not migrate.** Anything recorded in B lives in B. Fine for
a bake-off, worth knowing before recording something you want to keep.

## Suggested sequence

1. Stand up `jellyfin-npvr` on 8097, Live TV only, no libraries.
2. Move the NextPVR plugin to it; remove NextPVR from instance A.
3. Confirm instance A is Threadfin-only and its duplicate channels vanish.
4. Add both servers to Wholphin with distinct names.
5. Record the same programme on each **on different nights**, compare.

Step 3 is the one with real value even if the comparison never happens: it
returns instance A to the single-backend world its automation assumes.

## The honest alternative

If the goal is only to answer "which backend is better", you do not need
two instances. Disabling one tuner in Jellyfin's Live TV settings and
running a week on each gets the same answer with no new infrastructure.

The two-instance build is worth it if you want to **switch between them
quickly and repeatedly**, or keep a known-good backend running while
breaking the other. If you would rather run each for a week and decide,
say so — it is a fraction of the work and I would not talk you out of it.
