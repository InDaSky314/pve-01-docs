#!/usr/bin/env python3
"""Force NextPVR to re-import channels from playlist.m3u.

NextPVR takes tvg-chno at import and never renumbers existing channels when
the playlist changes -- the same identity behaviour Threadfin has -- and
exposes no rescan API (`channel.scan` returns nothing). The only way to adopt a
renumbered lineup without the web UI is to remove the channels and let it
import fresh.

Safe to do now, and only now, because tvg-logo points at the local icon host
for 302 channels: a re-import pulls the curated artwork rather than the
provider placeholders it would have pulled this morning. That inversion is the
whole reason the icon host was built.

EPG_EVENT rows are removed too -- they reference channel_oid and would be
orphaned. Guide data is re-ingested from epg.xml, which is refreshed daily by
epg-sync-ct112.timer.

NextPVR must be stopped: it holds the database open and writes on shutdown.
Backups taken by the caller.
"""
import sqlite3
import sys

DB = "/srv/jellyfin-npvr/nextpvr/config/npvr.db3"


def main():
    apply = "--apply" in sys.argv
    conn = sqlite3.connect(DB, timeout=30)

    counts = {t: conn.execute(f"select count(*) from {t}").fetchone()[0]
              for t in ("CHANNEL", "EPG_EVENT", "SCHEDULED_RECORDING")}
    print("before:", counts)

    if not apply:
        print("(dry run -- pass --apply)")
        return 0

    # Order matters: EPG_EVENT references channel_oid.
    n_epg = conn.execute("delete from EPG_EVENT").rowcount
    n_ch = conn.execute("delete from CHANNEL").rowcount
    conn.commit()

    after = {t: conn.execute(f"select count(*) from {t}").fetchone()[0]
             for t in ("CHANNEL", "EPG_EVENT")}
    conn.close()
    print(f"deleted {n_ch} channels, {n_epg} epg events")
    print("after :", after)
    return 0


if __name__ == "__main__":
    sys.exit(main())
