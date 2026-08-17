#!/usr/bin/env python3
"""Activate new Threadfin XEPG channels.

Threadfin (XEPG mode) adds channels that appear in the playlist as
x-active=false, which drops them from the HDHomeRun lineup Jellyfin
reads. This runs nightly at 04:25 — after Threadfin's 04:15 playlist
update, before Jellyfin's 04:30 guide refresh — and activates any
inactive entries. Threadfin must be stopped while xepg.json is edited
(it rewrites the file on shutdown), so we only cycle it when needed.

Threadfin auto-numbers channels from the playlist's tvg-chno, so no
number handling is needed here.

The restart goes through threadfin_ctl so it (a) never fires while a
recording is in progress — skips and waits for the next scheduled run
instead — and (b) verifies Threadfin actually comes back up on :34400
rather than assuming a fixed sleep was long enough (see threadfin_ctl
for why that assumption broke 4 times).

Real gap found + fixed 2026-08-17 (hit this manually the same day,
during Bayern Munich Phase 1's DAZN channel rollout): if this runs
outside its normal 04:25 slot -- e.g. triggered manually right after
xtream-sync.py adds new channels to playlist.m3u -- Threadfin hasn't
necessarily polled that new playlist yet (it normally does that on its
own 04:15 schedule), so `inactive` comes back empty and the run exits
as a no-op, "succeeding" at nothing. The new channels only show up as
x-active=false in xepg.json later, whenever Threadfin's own poll cycle
eventually runs, and then just sit there inactive until the NEXT
04:25 run. Worked around it manually that day by running this script
twice; trigger_threadfin_m3u_update() below does that same wait
mechanically, by asking Threadfin to reload its M3U immediately
instead of waiting on its own schedule.
"""
import json
import os
import sys
import urllib.request

import threadfin_ctl as tfc

XEPG = "/srv/media-core/threadfin/conf/xepg.json"
THREADFIN_API = "http://127.0.0.1:34400/api/"


def trigger_threadfin_m3u_update():
    """Ask Threadfin to reload its M3U playlist right now, rather than
    waiting for its own 04:15 schedule -- verified live (2026-08-17)
    against the real, running Threadfin instance: POST {"cmd":
    "update.m3u"} to /api/ returns {"status": true} and Threadfin stays
    fully responsive afterward. Best-effort: if Threadfin is already
    stopped or unreachable, the normal stop/reread flow below still
    works exactly as before, just without this head start."""
    try:
        req = urllib.request.Request(
            THREADFIN_API,
            data=json.dumps({"cmd": "update.m3u"}).encode(),
            headers={"Content-Type": "application/json", "User-Agent": "MediaCoreXEPG/1.0"},
            method="POST")
        with urllib.request.urlopen(req, timeout=15) as r:
            r.read()
    except Exception:                                          # noqa: BLE001
        pass  # best-effort only -- see docstring


def main():
    trigger_threadfin_m3u_update()
    try:
        with open(XEPG) as f:
            x = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        print(f"xepg: could not read {XEPG}: {e}")
        return 1
    inactive = [k for k, v in x.items() if not v.get("x-active")]
    if not inactive:
        print("xepg: all channels active, nothing to do")
        return 0
    try:
        tfc.stop_threadfin_safe("xepg activation")
    except tfc.RecordingInProgress:
        print("xepg: SKIPPED — a recording is in progress; will retry next scheduled run")
        return 0
    try:
        with open(XEPG) as f:
            x = json.load(f)  # re-read: threadfin saves on shutdown
        n = 0
        for v in x.values():
            if not v.get("x-active"):
                v["x-active"] = True
                n += 1
        # Atomic write (found in review, 2026-08-17): the old
        # json.dump(x, open(XEPG, "w")) truncates the file before writing
        # a single byte -- a crash or kill mid-write would leave Threadfin's
        # own config file empty/corrupt rather than just stale.
        tmp = XEPG + ".tmp"
        with open(tmp, "w") as f:
            json.dump(x, f)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, XEPG)
        print(f"xepg: activated {n} new channels")
    finally:
        if not tfc.start_threadfin_verified():
            print("xepg: Threadfin did NOT come back up verified -- see threadfin_ctl's ALERT_FILE")
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
