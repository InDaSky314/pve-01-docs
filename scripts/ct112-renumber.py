#!/usr/bin/env python3
"""Apply the lineup's channel numbers to CT 112's NextPVR, matched by name.

NextPVR takes channel numbers from the m3u's tvg-chno **at import** and does
not renumber existing channels when the playlist changes -- the same identity
behaviour Threadfin has. Replacing playlist.m3u on 2026-08-02 left every
channel on its old number. There is no rescan API (`channel.scan` returns
nothing); NextPVR only rescans through its web UI, and a full re-import would
recreate channels, orphaning the recording that references channel_oid 7441.

So the number column is updated in place, matched on channel name. OIDs are
untouched, which means recordings, EPG mappings and the name-keyed icon files
all stay valid. Only the displayed number changes.

NextPVR must be stopped: it holds the database open and writes on shutdown.
"""
import json
import re
import shutil
import sqlite3
import sys

DB = "/srv/jellyfin-npvr/nextpvr/config/npvr.db3"
BAK = DB + ".bak-renumber-20260802"
PLAYLIST = "/srv/jellyfin-npvr/nextpvr/config/playlist.m3u"


def main():
    apply = "--apply" in sys.argv

    # NextPVR stored names without the provider's country prefix ("CNN 4K"
    # where the playlist says "US: CNN 4K"), so 574 of 997 failed a literal
    # match and the collision guard refused the run. Normalise both sides by
    # stripping that prefix and the characters NextPVR drops from filenames.
    PREFIX = re.compile(r"^(US|UK|DE|CA|IT|FR|ES|NL|PT|PL|TR|AR|BR|MX)\s*[:|]\s*", re.I)
    norm = lambda n: PREFIX.sub("", n).strip()

    want = {}
    for line in open(PLAYLIST, errors="ignore"):
        m = re.search(r'tvg-chno="(\d+)".*?tvg-name="([^"]+)"', line)
        if m:
            want[norm(m.group(2))] = int(m.group(1))
    print(f"playlist: {len(want)} channels")

    conn = sqlite3.connect(DB, timeout=30)
    rows = conn.execute("select oid, name, number from CHANNEL").fetchall()
    print(f"nextpvr : {len(rows)} channels")

    changes, unmatched = [], []
    for oid, name, number in rows:
        target = want.get(norm(name))
        if target is None:
            unmatched.append(name)
        elif target != number:
            changes.append((oid, name, number, target))

    print(f"  to renumber : {len(changes)}")
    print(f"  unmatched   : {len(unmatched)}")
    for n in unmatched[:5]:
        print(f"      no playlist entry: {n}")

    # A collision would make two channels share a number; refuse rather than
    # produce the duplicate-channel ambiguity that has bitten this project.
    final = {}
    for oid, name, number in rows:
        final[oid] = want.get(norm(name), number)
    dupes = [n for n in set(final.values()) if list(final.values()).count(n) > 1]
    if dupes:
        print(f"ABORT: {len(dupes)} channel numbers would collide, e.g. {dupes[:5]}")
        return 1

    for oid, name, old, new in changes[:8]:
        print(f"      {old:>6} -> {new:<6} {name[:44]}")
    if len(changes) > 8:
        print(f"      ... and {len(changes)-8} more")

    if not apply:
        print("\n(dry run -- pass --apply to write)")
        return 0

    shutil.copy2(DB, BAK)
    for oid, _name, _old, new in changes:
        conn.execute("update CHANNEL set number = ? where oid = ?", (new, oid))
    conn.commit()
    check = conn.execute("select count(*) from CHANNEL").fetchone()[0]
    conn.close()
    print(f"\nrenumbered {len(changes)} channels; {check} channels total")
    print(f"backup: {BAK}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
