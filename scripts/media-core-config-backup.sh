#!/bin/bash
# Nightly Media-Core config backup (runs on the pve-01 HOST, root).
#
# Why this exists: CT 105's 1 TB mp0 mount is backup=0 by design
# (recordings), which also excludes ALL app state from vzdump — Jellyfin
# users/watch-history/library definitions, Threadfin conf, and the sync
# scripts/config. A CT restore without this would come back with a bare
# stack. See README "Backups".
#
# What it captures (~1.5 GB compressed): jellyfin/config minus the
# regenerable 105 GB metadata/ artwork cache (and log/transcodes),
# threadfin/conf minus its own backup zips, sync/ minus the regenerable
# series cache, docker-compose.yml and .env (secrets — archives are
# root-only, 0600). jellyfin.db is snapshotted with sqlite3 ".backup"
# first so the copy is consistent even while Jellyfin is running.
#
# Destination: /mnt/pve/SSD/media-core-backups on the SATA SSD — a
# different physical disk from the NVMe thin pool the CT lives on.
# Keeps the newest 7.
set -euo pipefail
umask 077

DEST=/mnt/pve/SSD/media-core-backups
KEEP=7
STAMP=$(date +%Y%m%d-%H%M)
PCT=/usr/sbin/pct
TMP_TAR="$DEST/media-core-config-$STAMP.tar.gz.tmp"
FINAL_TAR="$DEST/media-core-config-$STAMP.tar.gz"

# Real incident 2026-08-17 (see README.md History for the full writeup):
# this VACUUM step ran 2h16m and effectively stalled, degrading CT105
# badly enough to need a container reboot. Two real, verified causes
# found in review, both fixed below:
#   1. No timeout on the VACUUM step -- a stall had no way to self-abort.
#      Safe to kill mid-run: the source DB is opened mode=ro (SQLite
#      never takes a write lock on it), and `set -euo pipefail` means a
#      killed step aborts the whole script before tar/rename ever run --
#      zero partial output at the actual backup destination either way.
#   2. Three stale one-off jellyfin.db.bak-* files (7.85 GB total, from
#      earlier icon-realignment/batch-generation debugging sessions, not
#      part of the normal rotation) were being compressed into EVERY
#      nightly backup because the old exclude list only matched the
#      live db/db-wal/db-shm by exact name, not the .bak-* variants --
#      needless CPU/I/O load every single night. Widened to a wildcard,
#      plus Jellyfin's own SQLiteBackups dir (empty today, but the same
#      class of problem if it ever gets used).
# Also added: a cleanup trap so a killed/failed run never leaves a stray
# CT105-side snapshot or a stray host-side temp tar behind, and the tar
# now writes to a .tmp path first and only becomes the real named backup
# via `mv` on success -- so a genuinely partial/corrupt tar can never get
# mistaken for a real backup by the KEEP=7 rotation below.
cleanup() {
    $PCT exec 105 -- rm -f /tmp/jellyfin.db.snapshot 2>/dev/null || true
    rm -f "$TMP_TAR" 2>/dev/null || true
}
trap cleanup EXIT

mkdir -p "$DEST"

# Consistent sqlite snapshot next to the live db (inside the CT).
# VACUUM INTO, not ".backup": the backup API restarts from scratch every
# time another process writes the source db, and a running library scan
# writes constantly — .backup livelocked at 99% CPU for 89 min on the
# first test (2026-07-18). VACUUM INTO reads one WAL snapshot (the db is
# WAL mode), never restarts, and compacts the copy as a bonus.
$PCT exec 105 -- sh -c '
  rm -f /tmp/jellyfin.db.snapshot
  timeout 300 sqlite3 "file:/srv/media-core/jellyfin/config/data/jellyfin.db?mode=ro" \
    "VACUUM INTO '\''/tmp/jellyfin.db.snapshot'\''"
'

# stream the tar out of the CT onto the SSD; exclude the live db in
# favor of the snapshot, plus everything regenerable/heavy
$PCT exec 105 -- tar czf - \
    -C /srv/media-core \
    --exclude='jellyfin/config/metadata' \
    --exclude='jellyfin/config/log' \
    --exclude='jellyfin/config/transcodes' \
    --exclude='jellyfin/config/data/jellyfin.db*' \
    --exclude='jellyfin/config/data/SQLiteBackups' \
    --exclude='threadfin/conf/backup' \
    --exclude='sync/cache' \
    --exclude='sync/__pycache__' \
    jellyfin/config threadfin/conf sync docker-compose.yml .env \
    -C /tmp jellyfin.db.snapshot \
    > "$TMP_TAR"

mv "$TMP_TAR" "$FINAL_TAR"

# sanity: a suspiciously small archive means something broke — keep it
# for inspection but scream in the journal
SIZE=$(stat -c%s "$FINAL_TAR")
if [ "$SIZE" -lt 100000000 ]; then
    echo "WARNING: backup only $SIZE bytes — check contents" >&2
fi

# rotate: newest $KEEP stay
ls -1t "$DEST"/media-core-config-*.tar.gz | tail -n +$((KEEP + 1)) | xargs -r rm -f
echo "backup ok: media-core-config-$STAMP.tar.gz ($SIZE bytes), $(ls "$DEST" | wc -l) kept"
