#!/usr/bin/env python3
"""Shared Threadfin control: verified restart + recording-in-progress guard.

Two separate bugs this exists to fix:

1. Ephemeral-port race (recurred 2026-07-11, 07-13, 07-14, 07-16 despite a
   fixed 5s sleep in activate-xepg.py/renumber-xepg.py): a rapid `docker
   stop` -> `start` of Threadfin can leave its process bound to a random
   port instead of :34400 (old listener hadn't fully released). A fixed
   sleep is a probabilistic band-aid, not a fix — start_threadfin_verified()
   instead polls the actual port and retries (bounded) until it answers,
   and leaves a marker file (ALERT_FILE) if it never comes up so a health
   check can surface it.

2. Zombie tuner slot vs. real recordings: Threadfin's single-connection
   tuner can be left marked busy by a stream that ended abnormally, or
   held by another client, and will then silently refuse a scheduled DVR
   recording ("No new connections available. Tuner = 1" — the actual
   cause of two failed recordings, 2026-07-14 and 2026-07-15, both
   confirmed in docker logs). Any script that wants to restart Threadfin
   should go through stop_threadfin_safe() so a restart never kills a
   recording that is actually in progress — deferring/skipping is always
   safer than an unnecessary restart.

recording_in_progress() covers Jellyfin's own DVR via its API, plus
TiviMate (added 2026-07-18): TiviMate connects straight to the IPTV
provider, bypassing Threadfin/Jellyfin entirely, so there's no API to
ask — instead we treat a recently-modified .ts file under TiviMate's
known write folders (Tivimate/, Sports/) as "a recording is running".
This can't guarantee the tuner is free when a TiviMate recording
*starts* (we have no way to know its schedule in advance), but it does
guarantee nothing here interrupts one already in progress.
"""
import json
import subprocess
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import loki_alert

JF_URL = "http://127.0.0.1:8096"
JF_KEY_FILE = Path("/srv/media-core/.jellyfin_api_key")
THREADFIN_URL = "http://127.0.0.1:34400/web/"
UA = "MediaCoreThreadfinCtl/1.0"
ALERT_FILE = Path("/srv/media-core/.threadfin_alert")
TIVIMATE_DIRS = [
    Path("/srv/media-core/media/recordings/Tivimate"),
    Path("/srv/media-core/media/recordings/Sports"),
]
TIVIMATE_STALE_AFTER = 60  # seconds since last write before a .ts is "idle"


class RecordingInProgress(Exception):
    """Raised by stop_threadfin_safe() instead of touching Threadfin."""


def _tivimate_recording_active():
    """True if a .ts file under TiviMate's write folders was modified
    in the last TIVIMATE_STALE_AFTER seconds — an actively-growing
    recording, since TiviMate writes continuously while recording and
    goes quiet the moment it stops."""
    now = time.time()
    for d in TIVIMATE_DIRS:
        if not d.is_dir():
            continue
        try:
            for f in d.rglob("*.ts"):
                # Real bug found + fixed 2026-08-17: this try/except used
                # to wrap the WHOLE inner loop, so a single file racing
                # (deleted/renamed between rglob() yielding it and stat()
                # running on it -- plausible, TiviMate actively rewrites
                # files in these exact directories) aborted the scan for
                # the REST of that directory too, not just that one file.
                # Since this function gates whether it's safe to restart
                # Threadfin, that could produce a false "nothing active"
                # and restart Threadfin mid-recording. Scoped narrower so
                # one bad stat() only skips that one file.
                try:
                    if now - f.stat().st_mtime < TIVIMATE_STALE_AFTER:
                        return True
                except OSError:
                    continue
        except OSError:
            continue
    return False


def recording_in_progress():
    """True if Jellyfin reports an active recording, or a TiviMate
    recording looks active (see module docstring).

    Fails safe: if the Jellyfin check itself fails for any reason, treat
    that as "yes" — an unnecessary skipped restart is harmless, a
    restart that kills a real recording is not.
    """
    if _tivimate_recording_active():
        return True
    if not JF_KEY_FILE.exists():
        return True
    try:
        tok = JF_KEY_FILE.read_text().strip()
        hdr = {"Authorization": f'MediaBrowser Token="{tok}"', "User-Agent": UA}
        req = urllib.request.Request(
            f"{JF_URL}/emby/LiveTv/Recordings?IsInProgress=true&Limit=1",
            headers=hdr)
        with urllib.request.urlopen(req, timeout=15) as r:
            data = json.load(r)
        return data.get("TotalRecordCount", 0) > 0
    except Exception as e:
        print(f"threadfin_ctl: could not check recording status ({e}) — assuming yes")
        return True


def stop_threadfin_safe(reason, force=False):
    """Stop Threadfin, unless a recording is in progress (raises instead)."""
    if not force and recording_in_progress():
        raise RecordingInProgress(reason)
    subprocess.run(["docker", "stop", "threadfin"], check=True, capture_output=True)


def _port_ready(timeout_s):
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(THREADFIN_URL, timeout=3) as r:
                if r.status == 200:
                    return True
        except Exception:
            pass
        time.sleep(1)
    return False


def start_threadfin_verified(verify_timeout=20, max_retries=3):
    """Start Threadfin and confirm it actually answers on :34400.

    Replaces the old fixed `time.sleep(5)` — that only reduced the odds
    of the ephemeral-port race, it didn't close it. Retries a bounded
    number of times before giving up and leaving ALERT_FILE for the
    health check to pick up.
    """
    for attempt in range(1, max_retries + 1):
        time.sleep(5)  # let the old socket fully release before rebinding
        subprocess.run(["docker", "start", "threadfin"], check=True, capture_output=True)
        if _port_ready(verify_timeout):
            print(f"threadfin_ctl: threadfin up on :34400 (attempt {attempt}/{max_retries})")
            if ALERT_FILE.exists():
                ALERT_FILE.unlink()
            return True
        print(f"threadfin_ctl: WARNING — not answering on :34400 after start "
              f"(attempt {attempt}/{max_retries}), retrying")
        # check=False (found + fixed 2026-08-17): this used to be
        # check=True, uncaught -- a failed `docker stop` mid-retry (e.g.
        # the container never really came up this attempt) would raise
        # straight out of this function, skipping the REMAINING retries
        # and skipping the _write_alert() call below entirely, exactly
        # the alert this function exists to guarantee gets written.
        subprocess.run(["docker", "stop", "threadfin"], check=False, capture_output=True)
    _write_alert(f"threadfin failed to bind :34400 after {max_retries} restart attempts")
    print(f"threadfin_ctl: ERROR — threadfin still unreachable on :34400 after "
          f"{max_retries} attempts; check `docker logs threadfin` for the "
          f"'Web Interface: ...' line — an empty port means the ephemeral-port "
          f"bug hit again")
    return False


def _write_alert(message):
    ALERT_FILE.write_text(f"{datetime.now(timezone.utc).isoformat()}  {message}\n")
    loki_alert.push("media-core-alerts", f'level=alert source=threadfin_ctl msg="{message}"')
