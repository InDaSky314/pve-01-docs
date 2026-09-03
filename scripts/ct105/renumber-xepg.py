#!/usr/bin/env python3
"""Force Threadfin XEPG channel numbers to match the playlist's tvg-chno.

Threadfin (XEPG mode) adopts tvg-chno only for NEW channels; entries
that already exist keep their old x-channelID. After a lineup
renumbering (e.g. v5 -> v6 block scheme) run this once: it matches
every xepg.json entry to the generated playlist by stream URL and
rewrites x-channelID + x-group-title. Threadfin must be stopped while
xepg.json is edited (it rewrites the file on shutdown).

The restart goes through threadfin_ctl so it (a) never fires while a
recording is in progress — skips and waits for the next scheduled run
instead — and (b) verifies Threadfin actually comes back up on :34400
rather than assuming a fixed sleep was long enough (see threadfin_ctl
for why that assumption broke 4 times, most recently 2026-07-16).
"""
import json
import re
import sys

import threadfin_ctl as tfc

XEPG = "/srv/media-core/threadfin/conf/xepg.json"
# Curated artwork served by icon-host.service on the Proxmox host.
ICON_HOST = "http://192.168.9.11:8100"
PLAYLIST = "/srv/media-core/threadfin/conf/playlist.m3u"


def playlist_map():
    """tvg-name -> (chno, group, url) from the sync-generated playlist.

    Matched by tvg-name because that is Threadfin's channel identity
    (_uuid.key): when a lineup change moves a channel to a different
    provider stream, Threadfin keeps the entry — and its stale URL —
    so URL matching would miss exactly the entries that need fixing.
    """
    out = {}
    extinf = None
    for line in open(PLAYLIST):
        line = line.strip()
        if line.startswith("#EXTINF"):
            extinf = line
        elif line and not line.startswith("#") and extinf:
            chno = re.search(r'tvg-chno="([^"]+)"', extinf)
            group = re.search(r'group-title="([^"]+)"', extinf)
            name = re.search(r'tvg-name="([^"]+)"', extinf)
            logo = re.search(r'tvg-logo="([^"]+)"', extinf)
            if chno and name and name.group(1) not in out:
                out[name.group(1)] = (chno.group(1),
                                      group.group(1) if group else "",
                                      line,
                                      logo.group(1) if logo else "")
            extinf = None
    return out


def main():
    plmap = playlist_map()
    try:
        tfc.stop_threadfin_safe("xepg renumbering")
    except tfc.RecordingInProgress:
        print("xepg: SKIPPED — a recording is in progress; will retry next scheduled run")
        return 0
    try:
        x = json.load(open(XEPG))  # re-read: threadfin saves on shutdown
        fixed = missing = relogo = 0
        for v in x.values():
            hit = plmap.get(v.get("tvg-name") or v.get("_uuid.value", ""))
            if not hit:
                missing += 1
                continue
            chno, group, url, logo = hit
            # Only ever adopt a curated icon-host URL. Writing a provider URL
            # back would undo artwork that Threadfin is deliberately pinning.
            want_logo = logo if logo.startswith(ICON_HOST) else None
            if (v.get("x-channelID") != chno or v.get("x-group-title") != group
                    or v.get("url") != url
                    or (want_logo and v.get("tvg-logo") != want_logo)):
                v["x-channelID"] = chno
                v["tvg-chno"] = chno
                v["x-group-title"] = group
                v["group-title"] = group
                v["url"] = url
                if want_logo:
                    v["tvg-logo"] = want_logo
                    relogo += 1
                fixed += 1
        json.dump(x, open(XEPG, "w"))
        print(f"xepg: renumbered/repaired {fixed} channels "
              f"({missing} not in current playlist)")
        if relogo:
            print(f"xepg: {relogo} channels repointed at the icon host")
    finally:
        tfc.start_threadfin_verified()
    return 0


if __name__ == "__main__":
    sys.exit(main())
