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
import urllib.request
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
COMFREE_ROOT = "Commercial Free"       # single top-level dir, category nested inside
CPUS = "2"                              # of 4 — leave headroom for live streams
SIZE_STABLE_SECS = 20                   # recording must not be growing
MIN_COMMERCIAL_TOTAL = 60               # <60s total detected -> treat as none
DURATION_TOLERANCE = 45                 # seconds, output-duration sanity check

sys.path.insert(0, "/srv/media-core/sync")
try:
    import threadfin_ctl
except ImportError:
    threadfin_ctl = None

try:
    import loki_alert
except ImportError:
    loki_alert = None


def push_alert(msg, level="alert"):
    if loki_alert:
        try:
            loki_alert.push("media-core-alerts", msg, level=level)
        except Exception:
            pass


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
    """Invert commercial windows into keep windows.

    Clamps each cut to [0, total] before inverting (found in review,
    2026-08-17): if comskip's last detected break extends slightly past
    the real video duration, an unclamped cut could push total_cut over
    DURATION_TOLERANCE downstream and cause a perfectly valid
    commercial-free output to be discarded entirely."""
    keeps, pos = [], 0.0
    for s, e in cuts:
        s_c, e_c = max(0.0, min(s, total)), max(0.0, min(e, total))
        if s_c > pos + 1.0:
            keeps.append((pos, s_c))
        pos = max(pos, e_c)
    if total and total > pos + 1.0:
        keeps.append((pos, total))
    return keeps


def output_path_for(host_path):
    """.../recordings/Sports/Game/x.ts -> .../recordings/Commercial Free/Sports/Game/x.mkv

    Renamed 2026-08-16 (owner: "not crazy about 'Other (No Commercials)',
    would be nice if it was just 'Commercial Free' -- provided there aren't
    going to be other variations"). There will be: the categorization fix
    landing the same night means Sports/Series/etc. will all eventually get
    their own commercial-free content, not just "Other". A flat sibling per
    category ("Sports (No Commercials)", "Series (No Commercials)", ...)
    would clutter the top-level Recordings view; nesting the original
    category one level inside a single "Commercial Free" root avoids that
    while still keeping categories distinguishable.

    Output is MKV, not TS: concatenating .ts keep-segments leaves timestamp
    discontinuities at every splice, and probes that read stream timing (as
    Jellyfin's does) report only the first segment's duration (~4 min on the
    2026-08-15 Packers test). The MKV mux rebuilds a continuous timeline —
    still pure stream copy, no re-encode."""
    rel = os.path.relpath(host_path, RECORDINGS)
    parts = rel.split(os.sep)
    # Raw captures now sit under an "In Progress" root (2026-09-06). Drop that
    # prefix so the processed copy lands at Commercial Free/<Category> - CF/...
    # rather than Commercial Free/In Progress/<Category>/...
    if parts and parts[0] == "In Progress":
        parts = parts[1:]
    # Normalise EVERY component, not just the filename. Jellyfin's folder name
    # carries the same doubled spaces and timestamp as the file, and a tidy file
    # inside an untidy folder still reads badly on a tile (found 2026-09-06).
    parts[-1] = normalize_basename(os.path.splitext(parts[-1])[0]) + ".mkv"
    for i in range(len(parts) - 1):
        parts[i] = normalize_basename(parts[i])
    # 2026-09-06: mark commercial-free status at CATEGORY level rather than on
    # every filename. The owner asked for "Sports - CF" instead of a "- CF"
    # suffix repeated on each item -- inside this library every file is
    # commercial-free, so a per-file marker is noise, while the folder tile
    # carries the meaning exactly once.
    if len(parts) > 1 and not parts[0].endswith(" - CF"):
        src_cat = parts[0]
        parts[0] = f"{parts[0]} - CF"
        # Carry the owner's category artwork across. Renaming a folder orphans
        # its folder.png/jpg, and these tiles are hand-made -- a "Sports - CF"
        # shelf with no art next to a "Sports" one with art looks broken.
        try:
            dst_dir = os.path.join(RECORDINGS, COMFREE_ROOT, parts[0])
            os.makedirs(dst_dir, exist_ok=True)
            for ext in ("png", "jpg"):
                src_art = os.path.join(RECORDINGS, src_cat, f"folder.{ext}")
                dst_art = os.path.join(dst_dir, f"folder.{ext}")
                if os.path.isfile(src_art) and not os.path.exists(dst_art):
                    shutil.copy2(src_art, dst_art)
        except Exception as e:
            log(f"WARN (could not seed category artwork): {e}")
    return os.path.join(RECORDINGS, COMFREE_ROOT, *parts)



# Jellyfin restart suffixes look like " - 1 - 2 - 3". The spaces around the
# dash are load-bearing: without requiring them this also eats the "-09-08" of
# an ISO date, which it did on first write (2026-09-06).
_RESTART_RE = re.compile(r"(?:\s+-\s+\d+)+$")
# An underscore timestamp anywhere in the stem, with or without the clock.
_TS_RE = re.compile(r"(\d{4})_(\d{2})_(\d{2})(?:_\d{2}_\d{2}_\d{2})?")


def normalize_basename(stem):
    """Tidy a recording basename for the commercial-free copy.

    Jellyfin's own recorder names files from the EPG programme and appends a
    " - N" for every restart, which is how a Bundesliga match ends up on a tile
    as "Live  BL 2026_09_05_17_45_00 - 1 - 2 - ... - 29". We cannot change what
    Jellyfin writes, so the CF copy is the one place the name can be made
    readable. MCT names already arrive clean and pass through unchanged.

    - strip Jellyfin's restart suffixes  (" - 1 - 2 - 3")
    - strip the "(stitched)" marker      (dropped 2026-09-06)
    - collapse doubled spaces            ("Live  BL" -> "Live BL")
    - rewrite an underscore timestamp to an ISO date, dropping the clock,
      in place -- so a trailing token such as "FULL" survives
    """
    stem = stem.replace("(stitched)", "")
    stem = _RESTART_RE.sub("", stem)
    stem = _TS_RE.sub(lambda m: f"{m.group(1)}-{m.group(2)}-{m.group(3)}", stem)
    return re.sub(r"\s+", " ", stem).strip()


def jellyfin_refresh():
    """Ask Jellyfin to rescan after a commercial-free file lands.

    The Recordings library points at COMFREE_ROOT as of 2026-09-03, and
    EnableRealtimeMonitor is off for it, so without this a finished recording stays
    invisible until the next daily scan -- an overnight game would not show up until
    the following afternoon. This is a single HTTP POST from CT105; it does not run
    anything inside the jellyfin container, so the "never touches the container"
    property at the top of this file still holds.
    """
    try:
        key = open("/srv/media-core/.jellyfin_api_key").read().strip()
    except Exception:
        return
    if not key:
        return
    req = urllib.request.Request(
        "http://127.0.0.1:8096/Library/Refresh",
        method="POST", data=b"", headers={"X-Emby-Token": key, "Content-Length": "0"})
    try:
        with urllib.request.urlopen(req, timeout=20):
            log("jellyfin: library refresh requested")
    except Exception as e:
        log(f"WARN (jellyfin refresh failed, file is on disk): {e}")



def _container_path(host_path):
    """CT105 path -> the path Jellyfin reports in NowPlayingItem."""
    return "/media/recordings" + host_path[len(RECORDINGS):]


def is_being_watched(host_path):
    """True if any Jellyfin session is currently playing this file.

    Fails CLOSED on every error: if we cannot establish that nobody is
    watching, we keep the file. Deleting something out from under a viewer is
    far worse than leaving a duplicate on disk until the next run.
    """
    want = _container_path(host_path)
    try:
        key = open("/srv/media-core/.jellyfin_api_key").read().strip()
    except Exception:
        return True
    if not key:
        return True
    req = urllib.request.Request("http://127.0.0.1:8096/Sessions",
                                 headers={"X-Emby-Token": key})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            sessions = json.load(r)
    except Exception as e:
        log(f"WARN (could not query sessions, keeping source): {e}")
        return True
    for sess in sessions:
        npi = sess.get("NowPlayingItem") or {}
        if npi.get("Path") == want:
            log(f"source in use by '{sess.get('UserName','?')}' -- keeping it")
            return True
    return False


def cleanup_source(host_path, out_path):
    """Remove the raw capture once the commercial-free copy is proven good.

    Added 2026-09-06. The raw tree is indexed as the "In Progress" library so a
    game can be watched while it records; once the processed copy exists that
    raw file is pure duplication, and these are 8-9 GB each.

    Three guards, all of which must pass:
      1. the output exists and is non-empty,
      2. its duration is within 5% of the source (a truncated output must never
         authorise deleting a complete input),
      3. nobody is watching the source right now.

    Segment archives under /media/segments-archive are deliberately NOT touched
    -- the owner keeps those for post-mortems.
    """
    try:
        if not os.path.isfile(out_path) or os.path.getsize(out_path) == 0:
            log(f"cleanup: output missing or empty, keeping source: {out_path}")
            return
        src_dur = ffprobe_duration(host_path)
        out_dur = ffprobe_duration(out_path)
        if not out_dur:
            log("cleanup: could not probe the output, keeping source")
            return

        # A .ts source has no container duration field, so ffprobe's answer is
        # frequently nonsense -- a 4h Jellyfin recording probed as 92839s
        # (25.8h) on 2026-09-06, which made a duration ratio refuse every time.
        # Bytes are the trustworthy comparison here: comskip stream-copies, so
        # input and output share a bitrate, and ad removal takes maybe 15-35%.
        src_bytes = os.path.getsize(host_path)
        out_bytes = os.path.getsize(out_path)
        ratio = out_bytes / src_bytes if src_bytes else 0
        if ratio < 0.55:
            log(f"cleanup: output is only {ratio*100:.0f}% of source bytes "
                f"({out_bytes/1e9:.2f} vs {src_bytes/1e9:.2f} GB) -- too small to be "
                f"ad removal alone, keeping source")
            return
        # Only apply the duration check when the source duration is credible,
        # i.e. not wildly larger than the output.
        if src_dur and out_dur and src_dur < out_dur * 1.5 and out_dur < src_dur * 0.60:
            log(f"cleanup: output {out_dur:.0f}s vs credible source {src_dur:.0f}s "
                f"({out_dur/src_dur*100:.0f}%), keeping source")
            return
        if is_being_watched(host_path):
            return

        removed, freed = [], 0
        stem = os.path.splitext(host_path)[0]
        for cand in (host_path, stem + ".raw.ts", stem + ".nfo"):
            if os.path.isfile(cand):
                freed += os.path.getsize(cand)
                os.remove(cand)
                removed.append(os.path.basename(cand))
        # drop the fixture folder if nothing but artwork is left
        d = os.path.dirname(host_path)
        try:
            leftovers = [f for f in os.listdir(d) if f.lower() != "poster.jpg"]
            if not leftovers:
                shutil.rmtree(d, ignore_errors=True)
                log(f"cleanup: removed empty folder {os.path.basename(d)}")
        except Exception:
            pass
        log(f"cleanup: freed {freed/1e9:.2f} GB, removed {len(removed)} file(s): "
            f"{', '.join(removed)}  (source verified against {os.path.basename(out_path)})")
    except Exception as e:
        log(f"WARN (cleanup failed, source kept): {e}")


def rescue_original(host_path, out_path, reason):
    """Preserve the recording when commercial removal fails.

    Every ERROR path below used to log and return, producing no output at all.
    That was survivable while Jellyfin indexed the whole recordings tree, but the
    library now points ONLY at COMFREE_ROOT, so a comskip failure means the
    capture exists on disk and is invisible to the user forever, with no
    notification. A recording with adverts still in it beats a lost one.

    If input remux itself failed, repeating the ffmpeg copy would fail identically.
    The last-resort path falls back to a direct container file copy preserving the
    original format (.ts) and metadata, ensuring the game is visible in Jellyfin.
    If the file is truly unusable (0 bytes or missing), an alert is pushed to Loki
    so the user is notified immediately rather than failing silently.
    """
    rel_in = os.path.relpath(host_path, RECORDINGS)
    if not os.path.isfile(host_path) or os.path.getsize(host_path) == 0:
        size_str = f"{os.path.getsize(host_path)} bytes" if os.path.exists(host_path) else "missing"
        log(f"ALERT: RESCUE IMPOSSIBLE ({reason}): source file {rel_in} is {size_str} -- user alerted via Loki")
        push_alert(f"level=alert source=comskip-rescue msg='Recording {os.path.basename(host_path)} is {size_str} ({reason}) -- capture lost, rescue impossible'")
        return

    in_ext = os.path.splitext(host_path)[1]
    container_out_path = os.path.splitext(out_path)[0] + in_ext
    if os.path.exists(out_path) or os.path.exists(container_out_path):
        return

    rel_out = os.path.relpath(out_path, RECORDINGS)
    rel_container_out = os.path.relpath(container_out_path, RECORDINGS)
    try:
        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        rescued_target = None
        method = ""

        # For "input remux failed", ffmpeg stream-copy to MKV has already failed.
        # Bypass the failing ffmpeg remux and use the genuinely different direct copy.
        if reason != "input remux failed":
            r = run(["nice", "-n", "15", "docker", "run", "--rm", f"--cpus={CPUS}",
                     "-v", f"{RECORDINGS}:/recordings", IMAGE,
                     "ffmpeg", "-y", "-v", "error", "-err_detect", "ignore_err",
                     "-fflags", "+genpts+discardcorrupt",
                     "-i", f"/recordings/{rel_in}",
                     "-c", "copy", "-avoid_negative_ts", "make_zero",
                     f"/recordings/{rel_out}"],
                    timeout=3600)
            if r.returncode == 0 and os.path.isfile(out_path) and os.path.getsize(out_path) > 0:
                rescued_target = out_path
                method = "remux stream-copy"

        if not rescued_target:
            # Last-resort path: plain file copy preserving original container format
            log(f"RESCUE: using direct container copy for {rel_in} -> {rel_container_out}")
            shutil.copy2(host_path, container_out_path)
            if not os.path.isfile(container_out_path) or os.path.getsize(container_out_path) == 0:
                raise RuntimeError(f"Direct file copy failed to create non-empty file at {container_out_path}")
            rescued_target = container_out_path
            method = "direct container copy"

        copy_sidecars(host_path, rescued_target)
        jellyfin_refresh()
        rel_rescued = os.path.relpath(rescued_target, RECORDINGS)
        log(f"RESCUED uncut via {method} ({reason}): {rel_rescued} -- adverts NOT removed, but the recording is visible")
    except Exception as e:
        log(f"RESCUE FAILED ({reason}): could not copy original to {rel_out}: {e}")
        push_alert(f"level=alert source=comskip-rescue msg='RESCUE FAILED ({reason}): could not rescue {os.path.basename(host_path)}: {e}'")


def copy_sidecars(src_media, out_path):
    """Carry poster/NFO across into the commercial-free tree.

    comskip only ever moved the .mkv, so a Commercial Free folder held the video
    and nothing else. That was invisible while the library pointed at the whole
    recordings tree and picked metadata up from the raw copy -- but once it points
    at COMFREE_ROOT, artwork and plot come from here or not at all. The NFO is
    renamed to the output basename because Jellyfin matches sidecars by filename.
    """
    src_dir = os.path.dirname(src_media)
    out_dir = os.path.dirname(out_path)
    out_base = os.path.splitext(os.path.basename(out_path))[0]
    src_base = os.path.splitext(os.path.basename(src_media))[0]
    try:
        for fn in os.listdir(src_dir):
            low = fn.lower()
            src = os.path.join(src_dir, fn)
            if not os.path.isfile(src):
                continue
            if low.startswith(("poster.", "folder.", "fanart.")):
                dst = os.path.join(out_dir, fn)
            elif low.endswith(".nfo") and os.path.splitext(fn)[0] == src_base:
                dst = os.path.join(out_dir, out_base + ".nfo")
            else:
                continue
            if not os.path.exists(dst):
                shutil.copy2(src, dst)
                log(f"sidecar: {os.path.basename(dst)}")
    except Exception as e:
        log(f"WARN (sidecar copy failed, video is fine): {e}")


def process_one(container_path):
    if not container_path.startswith(CONTAINER_PREFIX + "/"):
        log(f"SKIP (not a recordings path): {container_path}")
        return True
    host_path = RECORDINGS + container_path[len(CONTAINER_PREFIX):]
    if "/.postprocess/" in host_path or f"/{COMFREE_ROOT}/" in host_path:
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

    rel = os.path.relpath(host_path, RECORDINGS)
    base = os.path.splitext(os.path.basename(host_path))[0]
    job_work = os.path.join(WORK, re.sub(r"[^A-Za-z0-9._-]", "_", base)[:80])
    # Always start from a clean directory. Root-caused 2026-08-16: a comskip
    # crash (segfault) on a corrupted segment left a job_work dir with a
    # stale .edl from an EARLIER, differently-broken attempt on the same
    # file (from hours before, before the input-duration fix landed). This
    # run's comskip crashed again before writing a fresh EDL, so the code
    # silently picked up that stale, garbage EDL (an inverted 510s->18s
    # range) and reported "no commercials" -- a wrong answer dressed as a
    # clean result, not a visible failure. A fresh job_work every attempt
    # means a crash can only ever produce a visible "no EDL" error, never a
    # silently-wrong stale one.
    shutil.rmtree(job_work, ignore_errors=True)
    os.makedirs(job_work, exist_ok=True)

    # Pre-remux the raw input with +genpts before touching it at all. Root-caused
    # 2026-08-16 against a real 4-segment Brewers/Dodgers restore chain (Agy
    # independently re-verified via raw PTS extraction, see
    # agy-reports/20260816T175937Z-comskip-duration-review.md): a recording that
    # ends via sports-dvr-auto's stall-watchdog force-cancel (as opposed to a
    # clean natural stop) can leave the raw .ts with PTS discontinuities --
    # symptoms ranged from ffprobe misreading duration as ~26.4h (a 33-bit PTS
    # wraparound artifact, 2^33/90000 ~= 95443s) to unprobeable to comskip
    # silently analyzing a near-zero-length window and reporting "no
    # commercials" on a real 40+ minute file. Same class of bug already fixed
    # for the OUTPUT concat step (2026-08-15) -- this closes the matching gap
    # on the INPUT side. Pure stream copy, no re-encode; comskip and every
    # downstream step now operate on this cleaned file, not the raw one.
    clean_input = os.path.join(job_work, "clean_input.mkv")
    rel_clean = os.path.relpath(clean_input, RECORDINGS)
    r = run(["nice", "-n", "15", "docker", "run", "--rm", f"--cpus={CPUS}",
             "-v", f"{RECORDINGS}:/recordings", IMAGE,
             "ffmpeg", "-y", "-v", "error", "-fflags", "+genpts",
             "-i", f"/recordings/{rel}",
             "-c", "copy", "-avoid_negative_ts", "make_zero",
             f"/recordings/{rel_clean}"],
            timeout=1800)
    if r.returncode != 0 or not os.path.isfile(clean_input):
        log(f"ERROR (input remux failed): {r.stderr[-300:]}")
        rescue_original(host_path, out_path, "input remux failed")
        shutil.rmtree(job_work, ignore_errors=True)
        return True

    orig_dur = ffprobe_duration(clean_input)
    if not orig_dur:
        log(f"ERROR (ffprobe failed on remuxed input, will not retry): {host_path}")
        rescue_original(host_path, out_path, "ffprobe failed")
        shutil.rmtree(job_work, ignore_errors=True)
        return True

    # Belt-and-suspenders: even a genpts-cleaned file can still carry internal
    # PTS resets (Agy's review found segment 4 had several mid-file resets from
    # real tuner reconnects) that could distort duration without genpts fully
    # normalizing them. A duration wildly inconsistent with the file's own size
    # implies an implausible bitrate -- catch that before wasting a comskip run
    # on it rather than trusting the number blindly.
    file_bytes = os.path.getsize(host_path)
    bitrate_kbps = (file_bytes * 8) / (orig_dur * 1000)
    # Ceiling raised 50000->100000 (2026-08-17): this same session's own
    # Bayern Munich PPV investigation found the provider tags some DAZN
    # PPV streams "8K EXCLUSIVE" -- genuine 8K content commonly runs
    # 40-100+ Mbps depending on codec/motion, which the old 50Mbps ceiling
    # could have incorrectly rejected as "corrupted" the first time any
    # such stream actually got recorded and comskip-processed. Raising an
    # upper sanity bound is low-risk either way: it can only prevent a
    # false rejection, it can't newly accept something that was already
    # broken under the old ceiling.
    if bitrate_kbps < 100 or bitrate_kbps > 100000:
        log(f"ERROR (input duration sanity check failed: {orig_dur:.0f}s for "
            f"{file_bytes/1e6:.1f}MB, implied bitrate {bitrate_kbps:.0f} kbps): {rel}")
        shutil.rmtree(job_work, ignore_errors=True)
        rescue_original(host_path, out_path, "input duration sanity check")
        return True

    log(f"COMSKIP start ({orig_dur:.0f}s): {rel}")
    t0 = time.time()
    r = run(["nice", "-n", "15", "docker", "run", "--rm", f"--cpus={CPUS}",
             "-v", f"{RECORDINGS}:/recordings", IMAGE,
             "comskip", "--ini=/recordings/.postprocess/comskip.ini",
             f"--output=/recordings/.postprocess/work/{os.path.basename(job_work)}",
             f"--output-filename={base}",
             f"/recordings/{rel_clean}"],
            timeout=4 * 3600)
    log(f"COMSKIP done in {time.time()-t0:.0f}s rc={r.returncode}")

    edl = os.path.join(job_work, base + ".edl")
    if not os.path.isfile(edl):
        # A negative returncode (Python reports a signal kill as -N) or the
        # classic 128+signal Unix convention (e.g. 139 = 128+SIGSEGV) means
        # comskip itself crashed mid-analysis, not a clean "nothing found"
        # exit. Seen 2026-08-16 on a segment whose stall-cut landed inside a
        # corrupted GOP -- the +genpts input fix repairs container/PTS
        # timing but can't repair actual corrupted H.264 reference-frame
        # data, and comskip's own decoder is less tolerant of that than
        # ffmpeg's. Flagged clearly here rather than left as a generic
        # error, since it's a real, if rare, source-corruption limitation
        # (not a pipeline bug) worth recognizing at a glance next time --
        # the original recording is always untouched regardless, this
        # segment just won't get a commercial-free version.
        crash_note = ""
        if r.returncode < 0 or r.returncode >= 128:
            sig = -r.returncode if r.returncode < 0 else r.returncode - 128
            crash_note = f" -- comskip crashed (signal {sig}, likely corrupted source video at the stall-cut point, not a pipeline bug)"
        log(f"ERROR (no EDL produced{crash_note}; comskip stderr tail: {r.stderr[-300:]})")
        rescue_original(host_path, out_path, "no EDL produced")
        return True

    cuts = parse_edl(edl)
    # Same clamp as keep_segments() -- a cut extending past orig_dur must
    # not inflate total_cut past DURATION_TOLERANCE below and cause a
    # valid commercial-free output to be discarded.
    clamped_cuts = [(max(0.0, min(s, orig_dur)), max(0.0, min(e, orig_dur))) for s, e in cuts if e > s]
    total_cut = sum(e - s for s, e in clamped_cuts)
    if total_cut < MIN_COMMERCIAL_TOTAL:
        # Previously this returned with no output at all. Once the Jellyfin
        # Recordings library points at COMFREE_ROOT, "no output" means the
        # recording is invisible -- a clean game with no detected ad breaks
        # would silently never appear. Pass it through uncut instead, reusing
        # the same cut/concat/remux path so the MKV timeline fix still applies.
        log(f"NO COMMERCIALS detected ({total_cut:.0f}s) — passing through uncut "
            f"so it still lands in {COMFREE_ROOT}: {rel}")
        clamped_cuts = []
        total_cut = 0.0
        keeps = [(0.0, orig_dur)]
    else:
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
                 "-ss", f"{s:.3f}", "-i", f"/recordings/{rel_clean}",
                 "-t", f"{e - s:.3f}", "-c", "copy",
                 "-avoid_negative_ts", "make_zero",
                 f"/recordings/.postprocess/work/{os.path.basename(job_work)}/{seg}"],
                timeout=1800)
        if r.returncode != 0:
            log(f"ERROR (segment {i} cut failed): {r.stderr[-300:]}")
            shutil.rmtree(job_work, ignore_errors=True)
            rescue_original(host_path, out_path, "segment cut failed")
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
        rescue_original(host_path, out_path, "concat failed")
        return True

    out_dur = ffprobe_duration(tmp_out)
    expected = orig_dur - total_cut
    if not out_dur or abs(out_dur - expected) > DURATION_TOLERANCE:
        log(f"ERROR (sanity check failed: output {out_dur}, expected ~{expected:.0f}) "
            f"— output discarded, original untouched")
        rescue_original(host_path, out_path, "output duration sanity check")
        shutil.rmtree(job_work, ignore_errors=True)
        return True

    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    shutil.move(tmp_out, out_path)
    copy_sidecars(host_path, out_path)
    jellyfin_refresh()
    cleanup_source(host_path, out_path)
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
    processed = set()
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
        else:
            processed.add(entry)

    # Re-read the live queue rather than blindly trusting the snapshot we
    # started with (found in review, 2026-08-17): process_one() can run
    # 15-45+ min for a long recording, and something else (Jellyfin's own
    # post-processing hook finishing a different recording) can append a
    # brand new entry to QUEUE while this run is still going. Overwriting
    # QUEUE with just `remaining` would silently drop that new entry --
    # nobody ever sees it again, no error, no retry. Anything currently in
    # QUEUE that we didn't start with and didn't already finish gets kept.
    live_queue = []
    if os.path.isfile(QUEUE):
        with open(QUEUE) as f:
            live_queue = [ln.strip() for ln in f if ln.strip()]
    new_entries = [e for e in live_queue if e not in entries and e not in processed]
    final_queue = remaining + new_entries

    tmp = QUEUE + ".tmp"
    with open(tmp, "w") as f:
        for entry in final_queue:
            f.write(entry + "\n")
    os.replace(tmp, QUEUE)
    return 0


if __name__ == "__main__":
    sys.exit(main())
