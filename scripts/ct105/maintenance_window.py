#!/usr/bin/env python3
"""Overnight maintenance window: when it's safe for an automated agent
(Claude Code, agy) to use the IPTV provider's single tuner connection for
troubleshooting, verification, or scraper testing WITHOUT asking the
owner first — as opposed to any daytime/evening use, which still needs
explicit go-ahead per session.

Owner-approved policy (2026-07-20, after a troubleshooting session's own
API/tuner use collided with live viewing): base window is
[MAINT_START_HOUR, MAINT_END_HOUR) local time (Europe/Berlin) — default
01:00-06:00, adjust the two constants below if the owner wants a
different cutoff. The window tightens automatically around any
scheduled Jellyfin recording that overlaps it (including its pre/post
padding) and reopens once that recording's padded interval ends, so a
maintenance task never competes with a real recording for the tuner.

This is a read-only check — it does not touch Threadfin, the provider,
or any config. Callers should treat `False` as "do not touch the tuner
right now", the same way threadfin_ctl.recording_in_progress() is
already treated as a hard stop before any restart.
"""
import json
from pathlib import Path
import sys
import urllib.request
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

sys.path.insert(0, "/srv/media-core/sync")
import threadfin_ctl as tc  # reuse JF_URL / JF_KEY_FILE / UA / recording_in_progress

TZ = ZoneInfo("Europe/Berlin")
MAINT_START_HOUR = 1   # 01:00 local
MAINT_END_HOUR = 5    # 05:00 local -- adjust here if the owner wants a different cutoff


def _in_base_window(now_local):
    h = now_local.hour + now_local.minute / 60
    return MAINT_START_HOUR <= h < MAINT_END_HOUR


MCT_WINDOWS_FILE = Path("/srv/media-core/sync/mct-windows.json")
MCT_WINDOWS_MAX_AGE = timedelta(minutes=10)


def _mct_intervals():
    """Busy intervals for MCT captures, pushed in by the host scheduler.

    This module tightened around Jellyfin timers but knew nothing about MCT, so
    it answered (True, 'ok') straight through a booked MCT capture. Since agents
    are allowed to use the tuner unasked inside the window, that would have let
    one take the single tuner mid-recording.

    Missing file: treat as no MCT windows - that is the pre-MCT state and must
    not block ordinary maintenance. Present but stale: the host scheduler writes
    it every 60s, so staleness means the scheduler is unhealthy; fail closed by
    returning a window covering now, matching how this module already handles a
    failed timer fetch.
    """
    if not MCT_WINDOWS_FILE.exists():
        return []
    try:
        data = json.loads(MCT_WINDOWS_FILE.read_text())
        gen = datetime.fromisoformat(data.get("generated_at"))
        if datetime.now(gen.tzinfo) - gen > MCT_WINDOWS_MAX_AGE:
            print("maintenance_window: mct-windows.json is stale — assuming busy")
            now = datetime.now(timezone.utc)
            return [(now, now)]
        out = []
        for w in data.get("windows", []):
            out.append((datetime.fromisoformat(w["start"]),
                        datetime.fromisoformat(w["end"])))
        return out
    except Exception as e:
        print(f"maintenance_window: could not read MCT windows ({e}) — assuming busy")
        now = datetime.now(timezone.utc)
        return [(now, now)]


def _scheduled_padded_intervals():
    """[(start_utc, end_utc), ...] for every Jellyfin timer, padding applied.
    Fails safe: any error fetching timers is treated as "assume something
    might be scheduled" by returning an interval covering right now, so a
    caller sees the window as closed rather than open on a fluke.
    """
    if not tc.JF_KEY_FILE.exists():
        return [(datetime.now(timezone.utc), datetime.now(timezone.utc))]
    try:
        tok = tc.JF_KEY_FILE.read_text().strip()
        req = urllib.request.Request(
            f"{tc.JF_URL}/emby/LiveTv/Timers",
            headers={"Authorization": f'MediaBrowser Token="{tok}"', "User-Agent": tc.UA})
        with urllib.request.urlopen(req, timeout=15) as r:
            timers = json.load(r).get("Items", [])
    except Exception as e:
        print(f"maintenance_window: could not fetch timers ({e}) — assuming closed")
        return [(datetime.now(timezone.utc), datetime.now(timezone.utc))]

    intervals = []
    for t in timers:
        try:
            start = datetime.fromisoformat(t["StartDate"].replace("Z", "+00:00"))
            end = datetime.fromisoformat(t["EndDate"].replace("Z", "+00:00"))
            pre = timedelta(seconds=t.get("PrePaddingSeconds", 0) or 0)
            post = timedelta(seconds=t.get("PostPaddingSeconds", 0) or 0)
            intervals.append((start - pre, end + post))
        except Exception:
            continue
    return intervals + _mct_intervals()


def is_open(at=None):
    """True if the maintenance window is open right now (or at the given
    aware datetime, for testing). False means: a scheduled/active
    recording is using or about to use the tuner, or it's outside the
    base overnight hours — do not touch the tuner without asking first.
    """
    now_utc = at or datetime.now(timezone.utc)
    now_local = now_utc.astimezone(TZ)

    if not _in_base_window(now_local):
        return False, f"outside base window ({MAINT_START_HOUR:02d}:00-{MAINT_END_HOUR:02d}:00 local)"

    for start, end in _scheduled_padded_intervals():
        if start <= now_utc <= end:
            return False, f"scheduled recording overlaps now ({start.isoformat()} - {end.isoformat()})"

    if tc.recording_in_progress():
        return False, "a recording is currently in progress"

    return True, "ok"


if __name__ == "__main__":
    ok, reason = is_open()
    print(("OPEN" if ok else "CLOSED") + f": {reason}")
    sys.exit(0 if ok else 1)
