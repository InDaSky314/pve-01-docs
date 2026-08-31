#!/usr/bin/env python3
"""Hourly PPV guide refresh.

Event-slot channels (MLB 01, UEFA 05, Soccer PPV 113, …) carry their
schedule in the channel NAME on the panel, which changes all day; the
nightly sync only captures the 04:00 snapshot. This re-reads the live
stream list (one API call — far under the panel's ~10 req/s ban limit),
regenerates guide entries for exactly the channels recorded in
cache/ppv-xids.json by the last sync, and — only when something actually
changed — splices them into epg/epg.xml, tells Threadfin to re-read the
XMLTV, and triggers Jellyfin's Refresh Guide.

Runs via media-core-ppv.timer at :07 every hour except 04:xx (the
nightly cascade owns that window). Playlist and channel names are never
touched here — slot display names are stable by design.
"""
import importlib.util
import json
import re
import sys
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("xsync", HERE / "xtream-sync.py")
xs = importlib.util.module_from_spec(spec)
spec.loader.exec_module(xs)

THREADFIN_API = "http://127.0.0.1:34400/api/"


def threadfin_update_xmltv():
    req = urllib.request.Request(
        THREADFIN_API, data=json.dumps({"cmd": "update.xmltv"}).encode(),
        headers={"Content-Type": "application/json", "User-Agent": xs.UA},
        method="POST")
    urllib.request.urlopen(req, timeout=120).close()


def main():
    if not xs.PPV_STATE.exists():
        print("ppv-refresh: no ppv-xids state yet (run the sync first)")
        return 0
    state = json.loads(xs.PPV_STATE.read_text())  # {xid: stream_id}
    if not state:
        print("ppv-refresh: no ppv channels in state, nothing to do")
        return 0
    env = xs.read_env()
    base = env["XTREAM_BASE"].rstrip("/")
    user, pw = env["XTREAM_USER"], env["XTREAM_PASS"]
    streams = xs.api(base, user, pw, "get_live_streams")
    by_id = {s["stream_id"]: s for s in streams}

    fresh = {}   # xid -> serialized replacement programmes
    for xid, sid in state.items():
        s = by_id.get(sid) or by_id.get(str(sid)) or {}
        fresh[xid] = b"".join(xs.ppv_programmes(s.get("name") or "", xid))

    ppv_ids = set(fresh)
    old = {xid: [] for xid in ppv_ids}
    out_tmp = xs.EPG_OUT.with_suffix(".xml.tmp")
    with open(out_tmp, "wb") as out:
        out.write(b'<?xml version="1.0" encoding="utf-8"?>\n')
        out.write(b'<tv generator-info-name="media-core-sync">\n')
        root = None
        for event, elem in ET.iterparse(xs.EPG_OUT, events=("start", "end")):
            if event == "start":
                if root is None:
                    root = elem
                continue
            if elem.tag == "channel":
                out.write(ET.tostring(elem, encoding="utf-8"))
            elif elem.tag == "programme":
                ch = elem.get("channel")
                if ch in ppv_ids:   # replaced below
                    old[ch].append(ET.tostring(elem, encoding="utf-8"))
                else:
                    out.write(ET.tostring(elem, encoding="utf-8"))
            if elem.tag in ("channel", "programme"):
                elem.clear()
                if root is not None:
                    root.clear()
        for xid in sorted(ppv_ids):
            out.write(fresh[xid])
        out.write(b"</tv>\n")

    out_tmp.replace(xs.EPG_OUT)
    try:
        threadfin_update_xmltv()
        print("threadfin: update.xmltv triggered")
    except Exception as e:
        print(f"WARNING: threadfin update.xmltv failed: {e}")

    # placeholder windows roll forward hourly by construction — compare
    # with times stripped so only real event changes trigger the reload
    strip = re.compile(rb'(start|stop)="[^"]*"')
    clean = lambda b: strip.sub(b"", b).strip()
    unchanged = all(clean(b"".join(old[x])) == clean(fresh[x])
                    for x in ppv_ids)
    if unchanged:
        print("ppv-refresh: no event changes, guide untouched")
        return 0
    n = sum(1 for x in ppv_ids if clean(b"".join(old[x])) != clean(fresh[x]))
    print(f"ppv-refresh: updated {n}/{len(ppv_ids)} event slots")
    xs.jellyfin_refresh(names=("Refresh Guide",))
    return 0


if __name__ == "__main__":
    sys.exit(main())
