#!/usr/bin/env python3
"""Recording-start watchdog (rewritten 2026-07-19).

**Previous design** (2026-07-16): unconditionally restarted Threadfin
~4 min before *every* scheduled recording to guarantee a clean tuner.
This fixed the 2026-07-14/07-15 failures (a zombie tuner session
silently refusing the DVR — see README), but had a real side effect:
restarting Threadfin drops any live stream currently flowing through
it. Confirmed 2026-07-19: `media-core-guard.service` log entries lined
up exactly with two reports of the Android app pausing/dropping for a
few seconds right at recording start.

**This design: detect-then-fix.** Do nothing preemptively. Once a
recording's scheduled start passes, take two samples of its output
file size ~15s apart. If it's genuinely growing, done — no disruption
in the common case where nothing was wrong. If it isn't (the
zombie-tuner failure mode), only then intervene, mirroring the manual
recovery used live on 2026-07-19: cancel the dead timer (so Jellyfin
stops believing something is "in progress"), a clean guarded Threadfin
restart, then a fresh timer on the same still-airing program with the
original padding preserved. Trades a ~1-2 min detection window in the
rare stuck case for zero live-viewing disruption in the common one.

Tuner=1 means at most one real recording can exist system-wide, so
this doesn't try to map a specific timer to a specific recording file
— it just asks "is *the* in-progress recording (if any) growing".

Runs every minute via media-core-guard.timer (unchanged schedule/unit).
"""
import json
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import threadfin_ctl as tfc

JF_URL = "http://127.0.0.1:8096"
JF_KEY_FILE = Path("/srv/media-core/.jellyfin_api_key")
UA = "MediaCoreGuard/2.0"
CHECK_WINDOW_MIN = 3       # watch a timer for this long after its start time
SAMPLE_GAP_SECONDS = 15    # gap between the two growth-check samples
MIN_HEALTHY_BYTES = 200_000  # well above the ~20KB stub size from a refused stream
STATE_FILE = Path("/srv/media-core/sync/cache/guard-watch-state.json")
RECORDINGS_ROOT_JF = "/media/recordings"
RECORDINGS_ROOT_FS = Path("/srv/media-core/media/recordings")


def jf_headers():
    tok = JF_KEY_FILE.read_text().strip()
    return {"Authorization": f'MediaBrowser Token="{tok}"', "User-Agent": UA}


def api_get(path):
    req = urllib.request.Request(f"{JF_URL}{path}", headers=jf_headers())
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.load(r)


def recent_timers():
    return api_get("/emby/LiveTv/Timers").get("Items", [])


def in_progress_recording_path():
    """Filesystem path of the current in-progress recording, or None.

    None means "cannot measure", NOT "zero bytes" -- the caller must treat
    those differently. A recording served by the NextPVR plugin lives under
    NextPVR's own root, so it never matches RECORDINGS_ROOT_JF and there is
    no local file for us to stat.
    """
    items = api_get("/emby/LiveTv/Recordings?IsInProgress=true&fields=Path").get("Items", [])
    if not items:
        return None
    path = items[0].get("Path") or ""
    if not path.startswith(RECORDINGS_ROOT_JF):
        return None
    rel = path[len(RECORDINGS_ROOT_JF):].lstrip("/")
    return RECORDINGS_ROOT_FS / rel


# Recorders whose output this guard cannot see. Threadfin writes under
# /media/recordings; NextPVR writes inside its own container. Restarting
# Threadfin cannot fix a NextPVR recording anyway, so those are not ours.
FOREIGN_RECORDING_ROOTS = ("/config/recordings", "/recordings")


def in_progress_is_foreign():
    """True if something else owns the current recording (e.g. NextPVR)."""
    items = api_get("/emby/LiveTv/Recordings?IsInProgress=true&fields=Path").get("Items", [])
    if not items:
        return False
    path = items[0].get("Path") or ""
    if not path:
        return False
    return (not path.startswith(RECORDINGS_ROOT_JF)) or path.startswith(FOREIGN_RECORDING_ROOTS)


def file_size():
    """Bytes, or None when the file cannot be measured.

    Previously this returned 0 in the unmeasurable case, which the caller
    could not distinguish from a genuinely stalled recording. With NextPVR
    also recording, that made the guard cancel and recreate healthy timers
    once a minute -- 18 fragments for one 30-minute programme on
    2026-07-31, and a Threadfin restart each time that also broke the
    unrelated Jellyfin DVR recording.
    """
    p = in_progress_recording_path()
    if p is None or not p.exists():
        return None
    return p.stat().st_size


def load_state():
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except Exception:
            return {}
    return {}


def save_state(state):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    if len(state) > 50:  # keep this from growing unbounded
        for k in list(state.keys())[:-50]:
            del state[k]
    STATE_FILE.write_text(json.dumps(state))


def recover(timer, reason):
    name = timer.get("Name", "?")
    tid = timer["Id"]
    program_id = (timer.get("ProgramInfo") or {}).get("Id") or timer.get("ProgramId")
    pre_pad = timer.get("PrePaddingSeconds", 0)
    post_pad = timer.get("PostPaddingSeconds", 0)
    print(f"guard: RECOVERING {name!r} — {reason}")

    # 1) cancel the dead timer so Jellyfin stops believing it's recording
    #    (also lets threadfin_ctl.recording_in_progress() go False)
    req = urllib.request.Request(f"{JF_URL}/emby/LiveTv/Timers/{tid}",
                                  headers=jf_headers(), method="DELETE")
    try:
        urllib.request.urlopen(req, timeout=15).close()
    except Exception as e:
        print(f"guard: WARNING — could not cancel dead timer {tid}: {e}")

    # 2) clean, verified Threadfin restart to clear whatever's stuck
    try:
        tfc.stop_threadfin_safe(f"recovering {name!r}")
        if not tfc.start_threadfin_verified():
            print("guard: threadfin restart did not come up cleanly, aborting recovery")
            return False
    except tfc.RecordingInProgress:
        print("guard: unexpected — something else is recording now, aborting recovery")
        return False

    # 3) start a fresh recording on the same still-airing program
    if not program_id:
        print("guard: no ProgramId on the dead timer, can't create a replacement")
        return False
    try:
        defaults = api_get(f"/emby/LiveTv/Timers/Defaults?programId={program_id}")
    except Exception as e:
        print(f"guard: WARNING — could not fetch timer defaults: {e}")
        return False
    defaults["PrePaddingSeconds"] = pre_pad
    defaults["PostPaddingSeconds"] = post_pad
    body = json.dumps(defaults).encode()
    req = urllib.request.Request(
        f"{JF_URL}/emby/LiveTv/Timers", data=body,
        headers={**jf_headers(), "Content-Type": "application/json"}, method="POST")
    try:
        urllib.request.urlopen(req, timeout=15).close()
        print(f"guard: recovery timer created for {name!r}")
        return True
    except Exception as e:
        print(f"guard: WARNING — could not create replacement timer: {e}")
        return False


def main():
    if not JF_KEY_FILE.exists():
        print("guard: no jellyfin api key, skipping")
        return 0
    try:
        timers = recent_timers()
    except Exception as e:
        print(f"guard: could not fetch timers: {e}")
        return 0

    now = datetime.now(timezone.utc)
    state = load_state()

    for t in timers:
        tid = t.get("Id")
        start = t.get("StartDate")
        if not tid or not start or state.get(tid, {}).get("resolved"):
            continue
        start_dt = datetime.fromisoformat(start.replace("Z", "+00:00"))
        mins_since_start = (now - start_dt).total_seconds() / 60
        if not (0 <= mins_since_start <= CHECK_WINDOW_MIN):
            continue  # not due for a check yet, or window passed unresolved (leave for next tick)

        name = t.get("Name", "?")

        # Never "recover" a recording we do not own -- restarting Threadfin
        # cannot help it, and cancelling its timer destroys a working
        # recording. Leave it alone and let it finish.
        if in_progress_is_foreign():
            print(f"guard: {name!r} is being recorded by another backend "
                  f"(not under {RECORDINGS_ROOT_JF}) — leaving it alone")
            state[tid] = {"resolved": True}
            continue

        s1 = file_size()
        time.sleep(SAMPLE_GAP_SECONDS)
        s2 = file_size()

        # Unmeasurable is not the same as stalled. Doing nothing costs one
        # missed detection; acting on a false positive costs the recording.
        if s1 is None or s2 is None:
            print(f"guard: {name!r} — cannot measure the recording file, "
                  f"skipping (no action taken)")
            continue

        if s2 > s1 and s2 > MIN_HEALTHY_BYTES:
            print(f"guard: {name!r} confirmed recording ({s2} bytes, growing) — no action needed")
            state[tid] = {"resolved": True}
            continue

        ok = recover(t, f"no growth {mins_since_start:.1f} min after scheduled start "
                        f"(sampled {s1} -> {s2} bytes)")
        state[tid] = {"resolved": True}
        if not ok:
            tfc._write_alert(f"recording {name!r} failed to start and auto-recovery failed — manual check needed")

    save_state(state)
    return 0


if __name__ == "__main__":
    sys.exit(main())
