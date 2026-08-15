# DVR commercial removal (comskip post-processing)

Built 2026-08-15 on owner request: after a recording finishes, automatically
produce a **second** copy with the commercials cut out, while **never
touching the original**. Both versions stay visible in Jellyfin:

- Original (with commercials): where it always was, e.g.
  `/media/recordings/Sports/<game>/<game>.ts`
- Commercial-free copy: `/media/recordings/Sports (No Commercials)/<game>/<game>.ts`

## Design constraints (owner requirements, 2026-08-15)

1. Two separate versions in different spots — the cut is **never**
   destructive/in-place.
2. Must not interfere with the watch-while-recording path that took real
   debugging to fix (NoCompatibleStream, AV-desync). Guaranteed by
   architecture: nothing here runs in, or modifies, the jellyfin container
   or its compose definition, and processing only starts after Jellyfin
   has finalized the file (plus a size-stability re-check and a
   `threadfin_ctl.recording_in_progress()` gate).

## Architecture

```
Jellyfin (container)                          CT 105 host
────────────────────                          ───────────
recording finalized
  └─► post-processing hook (native Jellyfin
      DVR feature) runs INSIDE the container:
      /media/recordings/.postprocess/
        on-recording-finished.sh
      — appends "{path}" to .postprocess/queue,
        nothing else. Zero dependencies.
                                              comskip-postprocess.timer (5 min)
                                                └─► /srv/media-core/comskip/process-queue.py
                                                    1. gate: no active Threadfin recording
                                                    2. gate: file size stable 20 s
                                                    3. comskip (docker image comskip:0.83-local,
                                                       --cpus=2, niced) → .edl breakpoints
                                                    4. ffmpeg stream-copy the keep-segments,
                                                       concat → temp file
                                                    5. sanity check: output duration ==
                                                       original − cuts (±45 s) or DISCARD
                                                    6. move into "<Category> (No Commercials)/"
```

The output lands under the same `/media/recordings` tree Jellyfin already
watches, so it appears in the Recordings library with no compose change,
no new library, and no container restart.

## Components

| What | Where (CT 105) |
|---|---|
| Hook (runs in jellyfin container) | `/srv/media-core/media/recordings/.postprocess/on-recording-finished.sh` |
| Queue + comskip.ini | `/srv/media-core/media/recordings/.postprocess/` |
| Queue runner | `/srv/media-core/comskip/process-queue.py` |
| comskip binary + Dockerfile | `/srv/media-core/comskip/` (image `comskip:0.83-local`) |
| systemd | `comskip-postprocess.service` + `.timer` (5 min) |
| Logs + kept EDLs | `/srv/media-core/comskip/logs/` |

Jellyfin wiring (Dashboard → Live TV → DVR, or `livetv.xml`):
`RecordingPostProcessor = /media/recordings/.postprocess/on-recording-finished.sh`,
arguments `"{path}"` (the arguments element was already present).

## comskip image

The official `jellyfin/jellyfin` image doesn't bundle comskip and Debian
trixie has no package, so it's compiled from source
(erikkaashoek/Comskip, v0.83.001) in a disposable `debian:trixie` builder
and baked into a slim runtime image via `/srv/media-core/comskip/Dockerfile`
(runtime libav* + ffmpeg + the binary). Rebuild if ever lost:

```
docker run -it --rm debian:trixie bash
apt-get update && apt-get install -y gcc make git autoconf automake libtool \
  pkg-config libavcodec-dev libavformat-dev libavutil-dev libswscale-dev \
  libargtable2-dev
git clone https://github.com/erikkaashoek/Comskip && cd Comskip
./autogen.sh && ./configure && make
# copy ./comskip out, then: cd /srv/media-core/comskip && docker build -t comskip:0.83-local .
```

## Detection tuning — validated 2026-08-15

Tuned against a 40-min clip from the real Packers@Steelers NFL Network
recording, verified frame-by-frame (extracted JPEGs from every claimed
cut window and keep segment): **3/3 commercial breaks found (Liberty
Mutual / Collars & Co. / Wendy's, textbook 2:00-2:30 break lengths), 0
false cuts on game footage.**

The critical finding: **this provider's re-encode contains no true black
frames at all** (ffmpeg `blackdetect` at pix_th 0.15 across the whole
clip: zero periods). A first, conservative config (black+logo only,
`max_avg_brightness=25`) therefore detected nothing. The working config
is multi-signal — `detect_method=111` (black + logo + scene + resolution
+ aspect + silence) with `max_avg_brightness=35` — so no single signal
has to be decisive. If a future channel misbehaves, re-run the same
verification: comskip on a clip, then extract frames at the EDL midpoints
and look at them; do not trust the EDL numbers alone.

Safety still leans conservative: breaks only between 20 s and 10 min are
cut, and if under 60 s of commercials is detected in total, no cut version
is produced. Halftime shows and commentary are continuous broadcast (no
break signature), so they survive — matching the owner's "keep halftime
and commentary" preference. Each processed recording's `.edl` is kept in
the logs dir for tuning.

## Failure behavior

Every failure path (no EDL, segment cut error, concat error, duration
sanity-check miss) discards the work product and leaves the original
untouched; errors are logged and the queue entry dropped so a poison file
can't wedge the queue. A still-growing file defers (stays queued). The
runner is flock-guarded against overlapping timer fires.

## Post-deploy fix: output must be MKV (2026-08-15, same day)

The first full-game run surfaced it: concatenating `.ts` keep-segments
stream-copy leaves timestamp discontinuities at every splice. `ffprobe`'s
container-level duration is right, but probes that walk stream timing —
Jellyfin's included — report only the first keep-segment (the Packers game
showed as "3.9 min"). Fix: the final concat now muxes to `.mkv` (with
`-fflags +genpts`), which rebuilds a continuous timeline — still pure
stream copy, no re-encode, ~2 min for a 9 GB game. Verified: the same
game re-wrapped as MKV probes at 14117.8 s and shows the full 3h55m
runtime in the Jellyfin library.
