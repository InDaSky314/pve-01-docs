#!/usr/bin/env python3
"""Delete Jellyfin Live TV metadata directories that no database row references.

Every guide refresh deletes and recreates programme items under fresh item
GUIDs. Their metadata directories are not cleaned up, so `metadata/livetv/`
grows by roughly the size of one guide's artwork on every refresh and never
shrinks. On CT 112, 2026-08-03: 33,867 directories on disk, 17,394 referenced,
**9.8 GB orphaned** after four refreshes in a day.

This is the inverse of the trap in lessons-learned.md. There, files were
deleted while their `BaseItemImageInfos` rows survived, and Jellyfin concluded
it already had the images and never re-fetched — 997 channels blank
permanently. Here the rows are already gone and only the files remain, so
removing them is safe *provided nothing else references the path*. Verified on
CT 112: `BaseItemImageInfos.Path` is the only column in any table that
mentions these paths.

Dry run by default. Pass --apply to delete.

Usage:
    prune-jellyfin-orphan-metadata.py <jellyfin-config-root> [--apply]
e.g. prune-jellyfin-orphan-metadata.py /srv/jellyfin-npvr/jellyfin --apply
"""
import os
import shutil
import sqlite3
import sys


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not args:
        print(__doc__)
        return 2
    root = args[0].rstrip("/")
    apply = "--apply" in sys.argv

    db = os.path.join(root, "config/data/jellyfin.db")
    live = os.path.join(root, "config/metadata/livetv")
    if not os.path.isdir(live):
        print("no livetv metadata directory at", live)
        return 1

    conn = sqlite3.connect("file:%s?mode=ro" % db, uri=True)

    # Refuse to act on a measurement that cannot be made. An empty referenced
    # set would mean "delete everything", and the most likely cause of that is
    # a query or path change, not an actually-empty database.
    referenced = set()
    for (p,) in conn.execute(
            "select Path from BaseItemImageInfos "
            "where Path like '/config/metadata/livetv/%'"):
        parts = p.split("/")
        if len(parts) > 4:
            referenced.add(parts[4])
    if not referenced:
        print("REFUSING: no referenced directories found. Either the guide is "
              "genuinely empty or this query is wrong — either way, do not "
              "delete 100% of the cache on it.")
        return 1

    on_disk = set(os.listdir(live))
    orphans = on_disk - referenced

    total = 0
    for d in orphans:
        for dirpath, _, files in os.walk(os.path.join(live, d)):
            for f in files:
                try:
                    total += os.path.getsize(os.path.join(dirpath, f))
                except OSError:
                    pass

    print("directories on disk : %d" % len(on_disk))
    print("referenced by the db: %d" % len(referenced & on_disk))
    print("orphaned            : %d  (%.1f GB)" % (len(orphans), total / 1e9))

    if not apply:
        print("\n(dry run — pass --apply to delete)")
        return 0

    removed = failed = 0
    for d in orphans:
        try:
            shutil.rmtree(os.path.join(live, d))
            removed += 1
        except OSError as exc:
            failed += 1
            if failed < 5:
                print("could not remove %s: %s" % (d, exc))
    print("removed %d directories, %d failed, freed ~%.1f GB"
          % (removed, failed, total / 1e9))
    return 0


if __name__ == "__main__":
    sys.exit(main())
