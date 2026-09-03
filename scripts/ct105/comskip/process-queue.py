#!/usr/bin/env python3
"""comskip-process-queue — DVR commercial-removal post-processing (CT105).

Consumes the queue written by Jellyfin's post-processing hook
(/media/recordings/.postprocess/queue, container path) and, for each
finished recording:

  1. safety-gates: file exists, size stable, no active Threadfin recording
  2. runs comskip (in the comskip:0.83-local docker image) to detect
     commercial breaks -> .edl file
  3. cuts the commercials out with ffmpeg stream-copy into a NEW file under
     "<Category> (No Commercials)/..." — the original recording is NEVER
     modified, moved, or deleted (owner requirement, 2026-08-15: keep both
     versions in different spots)
  4. sanity-checks the output (duration = original - cuts, within tolerance)
     before moving it into place

Design notes:
  - Never touches the jellyfin container. Detection + cutting run in a
    dedicated docker image; CPU is capped (--cpus) and niced so live
    playback/transcodes on the shared N5105 always win.
  - Defers (leaves queue intact) while a Threadfin recording is active —
    belt-and-braces protection for the watch-while-recording path.
  - If comskip finds no commercials, no cut file is produced (logged).
  - Idempotent: skips work if the output file already exists.

Runs via comskip-postprocess.timer every 5 minutes; flock prevents overlap.
"""

import fcntl
import json
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone

RECORDINGS = "/srv/media-core/media/recordings"          # host side
CONTAINER_PREFIX = "/media/recordings"                    # jellyfin side
PP_DIR = os.path.join(RECORDINGS, ".postprocess")
QUEUE = os.path.join(PP_DIR, "queue")
LOCK = os.path.join(PP_DIR, ".runner.lock")
WORK = os.path.join(PP_DIR, "work")
LOG_DIR = "/srv/media-core/comskip/logs"
# comskip.ini lives inside .postprocess so it's visible at the same relative
# path from within the docker -v mount of the recordings tree
IMAGE = "comskip:0.83-local"
NOCOM_SUFFIX = " (No Commercials)"     # appended to the top-level category dir
CPUS = "2"                              # of 4 — leave headroom for live streams
SIZE_STABLE_SECS = 20                   # recording must not be growing
MIN_COMMERCIAL_TOTAL = 60               # <60s total detected -> treat as none
DURATION_TOLERANCE = 45                 # seconds, output-duration sanity check

sys.path.insert(0, "/srv/media-core/sync")
try:
    import threadfin_ctl
except ImportError:
    threadfin_ctl = None


def log(msg):
    line = f"{datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')} {msg}"
    print(line, flush=True)
    os.makedirs(LOG_DIR, exist_ok=True)
    with open(os.path.join(LOG_DIR, "process-queue.log"), "a") as f:
        f.write(line + "\n")


def run(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


def ffprobe_duration(host_path):
    rel = os.path.relpath(host_path, RECORDINGS)
    r = run(["docker", "run", "--rm",
             "-v", f"{RECORDINGS}:/recordings:ro", IMAGE,
             "ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "json", f"/recordings/{rel}"])
    if r.returncode != 0:
        return None
    try:
        return float(json.loads(r.stdout)["format"]["duration"])
    except (KeyError, ValueError, json.JSONDecodeError):
        return None


def size_stable(path):
    s1 = os.path.getsize(path)
    time.sleep(SIZE_STABLE_SECS)
    return os.path.getsize(path) == s1


def parse_edl(edl_path):
    """Return list of (start, end) commercial windows, seconds."""
    cuts = []
    with open(edl_path) as f:
        for line in f:
            parts = line.split()
            if len(parts) >= 3 and parts[2] == "0":
                try:
                    s, e = float(parts[0]), float(parts[1])
                except ValueError:
                    continue
                if e > s:
                    cuts.append((s, e))
    return sorted(cuts)


def keep_segments(cuts, total):
    """Invert commercial windows into keep windows."""
    keeps, pos = [], 0.0
    for s, e in cuts:
        if s > pos + 1.0:
            keeps.append((pos, s))
        pos = max(pos, e)
    if total and total > pos + 1.0:
        keeps.append((pos, total))
    return keeps


def output_path_for(host_path):
    """.../recordings/Sports/Game/x.ts -> .../recordings/Sports (No Commercials)/Game/x.mkv

    Output is MKV, not TS: concatenating .ts keep-segments leaves timestamp
    discontinuities at every splice, and probes that read stream timing (as
    Jellyfin's does) report only the first segment's duration (~4 min on the
    2026-08-15 Packers test). The MKV mux rebuilds a continuous timeline —
    still pure stream copy, no re-encode."""
    rel = os.path.relpath(host_path, RECORDINGS)
    parts = rel.split(os.sep)
    parts[0] = parts[0] + NOCOM_SUFFIX
    parts[-1] = os.path.splitext(parts[-1])[0] + ".mkv"
    return os.path.join(RECORDINGS, *parts)


def process_one(container_path):
    if not container_path.startswith(CONTAINER_PREFIX + "/"):
        log(f"SKIP (not a recordings path): {container_path}")
        return True
    host_path = RECORDINGS + container_path[len(CONTAINER_PREFIX):]
    if "/.postprocess/" in host_path or NOCOM_SUFFIX + "/" in host_path + "/":
        log(f"SKIP (internal/output path): {container_path}")
        return True
    if not os.path.isfile(host_path):
        log(f"SKIP (file gone): {host_path}")
        return True
    if os.path.splitext(host_path)[1].lower() not in (".ts", ".mkv", ".mp4"):
        log(f"SKIP (not video): {host_path}")
        return True

    out_path = output_path_for(host_path)
    if os.path.exists(out_path):
        log(f"SKIP (output already exists): {out_path}")
        return True

    if not size_stable(host_path):
        log(f"DEFER (file still growing): {host_path}")
        return False

    orig_dur = ffprobe_duration(host_path)
    if not orig_dur:
        log(f"ERROR (ffprobe failed, will not retry): {host_path}")
        return True

    rel = os.path.relpath(host_path, RECORDINGS)
    base = os.path.splitext(os.path.basename(host_path))[0]
    job_work = os.path.join(WORK, re.sub(r"[^A-Za-z0-9._-]", "_", base)[:80])
    os.makedirs(job_work, exist_ok=True)

    log(f"COMSKIP start ({orig_dur:.0f}s): {rel}")
    t0 = time.time()
    r = run(["nice", "-n", "15", "docker", "run", "--rm", f"--cpus={CPUS}",
             "-v", f"{RECORDINGS}:/recordings", IMAGE,
             "comskip", "--ini=/recordings/.postprocess/comskip.ini",
             f"--output=/recordings/.postprocess/work/{os.path.basename(job_work)}",
             f"/recordings/{rel}"],
            timeout=4 * 3600)
    log(f"COMSKIP done in {time.time()-t0:.0f}s rc={r.returncode}")

    edl = os.path.join(job_work, base + ".edl")
    if not os.path.isfile(edl):
        log(f"ERROR (no EDL produced; comskip stderr tail: {r.stderr[-300:]})")
        return True

    cuts = parse_edl(edl)
    total_cut = sum(e - s for s, e in cuts)
    if total_cut < MIN_COMMERCIAL_TOTAL:
        log(f"NO COMMERCIALS detected ({total_cut:.0f}s) — no cut version made: {rel}")
        return True
    keeps = keep_segments(cuts, orig_dur)
    log(f"EDL: {len(cuts)} commercial breaks, {total_cut/60:.1f} min to remove, "
        f"{len(keeps)} keep-segments")

    # cut each keep segment with stream copy, then concat
    seg_files = []
    for i, (s, e) in enumerate(keeps):
        seg = f"seg{i:03d}.ts"
        r = run(["nice", "-n", "15", "docker", "run", "--rm", f"--cpus={CPUS}",
                 "-v", f"{RECORDINGS}:/recordings", IMAGE,
                 "ffmpeg", "-y", "-v", "error",
                 "-ss", f"{s:.3f}", "-i", f"/recordings/{rel}",
                 "-t", f"{e - s:.3f}", "-c", "copy",
                 "-avoid_negative_ts", "make_zero",
                 f"/recordings/.postprocess/work/{os.path.basename(job_work)}/{seg}"],
                timeout=1800)
        if r.returncode != 0:
            log(f"ERROR (segment {i} cut failed): {r.stderr[-300:]}")
            shutil.rmtree(job_work, ignore_errors=True)
            return True
        seg_files.append(seg)

    concat_list = os.path.join(job_work, "concat.txt")
    with open(concat_list, "w") as f:
        for seg in seg_files:
            f.write(f"file '{seg}'\n")

    tmp_out = os.path.join(job_work, "output.mkv")
    r = run(["nice", "-n", "15", "docker", "run", "--rm", f"--cpus={CPUS}",
             "-v", f"{RECORDINGS}:/recordings", IMAGE,
             "ffmpeg", "-y", "-v", "error", "-fflags", "+genpts",
             "-f", "concat", "-safe", "0",
             "-i", f"/recordings/.postprocess/work/{os.path.basename(job_work)}/concat.txt",
             "-c", "copy",
             f"/recordings/.postprocess/work/{os.path.basename(job_work)}/{os.path.basename(tmp_out)}"],
            timeout=1800)
    if r.returncode != 0 or not os.path.isfile(tmp_out):
        log(f"ERROR (concat failed): {r.stderr[-300:]}")
        shutil.rmtree(job_work, ignore_errors=True)
        return True

    out_dur = ffprobe_duration(tmp_out)
    expected = orig_dur - total_cut
    if not out_dur or abs(out_dur - expected) > DURATION_TOLERANCE:
        log(f"ERROR (sanity check failed: output {out_dur}, expected ~{expected:.0f}) "
            f"— output discarded, original untouched")
        shutil.rmtree(job_work, ignore_errors=True)
        return True

    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    shutil.move(tmp_out, out_path)
    # keep the EDL next to the log for future tuning
    shutil.copy(edl, os.path.join(LOG_DIR, os.path.basename(edl)))
    shutil.rmtree(job_work, ignore_errors=True)
    log(f"DONE: {out_path} ({out_dur:.0f}s, removed {total_cut/60:.1f} min of commercials)")
    return True


def main():
    os.makedirs(PP_DIR, exist_ok=True)
    os.makedirs(WORK, exist_ok=True)
    lock_f = open(LOCK, "w")
    try:
        fcntl.flock(lock_f, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return 0  # previous run still going

    if not os.path.isfile(QUEUE):
        return 0
    with open(QUEUE) as f:
        entries = [ln.strip() for ln in f if ln.strip()]
    if not entries:
        return 0

    # belt-and-braces: never compete with an active recording
    if threadfin_ctl is not None:
        try:
            if threadfin_ctl.recording_in_progress():
                log(f"DEFER ({len(entries)} queued; recording in progress)")
                return 0
        except Exception as e:
            log(f"WARN recording_in_progress check failed ({e}); deferring to be safe")
            return 0

    remaining = []
    for entry in entries:
        try:
            done = process_one(entry)
        except subprocess.TimeoutExpired:
            log(f"ERROR (timeout) processing {entry} — dropping from queue")
            done = True
        except Exception as e:
            log(f"ERROR ({e}) processing {entry} — dropping from queue")
            done = True
        if not done:
            remaining.append(entry)

    tmp = QUEUE + ".tmp"
    with open(tmp, "w") as f:
        for entry in remaining:
            f.write(entry + "\n")
    os.replace(tmp, QUEUE)
    return 0


if __name__ == "__main__":
    sys.exit(main())
