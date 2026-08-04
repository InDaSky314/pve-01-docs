#!/bin/bash
# Probe channels [OFFSET, OFFSET+COUNT) from /root/recheck-names.txt.
# Deliberately a slice, not the whole list: the stream-safety guard lives on
# the host (it queries the router) and cannot be called from in here, so the
# host re-checks between slices and simply stops calling us if a real stream
# starts. The account allows ONE concurrent stream.
set -u
OFFSET=${1:-0}; COUNT=${2:-5}
PL=/srv/media-core/threadfin/conf/playlist.m3u
FF=/usr/lib/jellyfin-ffmpeg/ffmpeg
OUT=/tmp/recheck; mkdir -p "$OUT"
python3 - "$OFFSET" "$COUNT" <<'PY' > /tmp/slice.tsv
import sys
sys.path.insert(0, "/srv/media-core/sync")
import channel_naming as cn
off, cnt = int(sys.argv[1]), int(sys.argv[2])
names = [l.strip() for l in open("/root/recheck-names.txt") if l.strip()]
for n in names[off:off+cnt]:
    print("%s\t%s" % (n, cn.modernise(n)))
PY
while IFS=$'\t' read -r old new; do
    url=$(grep -A1 -F "tvg-name=\"$new\"" "$PL" | tail -1)
    [ -z "$url" ] && url=$(grep -A1 -F "tvg-name=\"$old\"" "$PL" | tail -1)
    if [ -z "$url" ]; then printf '%s\tNOT_IN_LINEUP\t0\n' "$new" >> "$OUT/result.tsv"; continue; fi
    slug=$(echo "$new" | md5sum | cut -c1-12)
    docker exec jellyfin timeout 70 $FF -y -loglevel quiet -rw_timeout 30000000 \
        -i "$url" -t 12 -ss 9 -frames:v 1 -q:v 5 "/tmp/rc_$slug.jpg" >/dev/null 2>&1
    sz=$(docker exec jellyfin stat -c%s "/tmp/rc_$slug.jpg" 2>/dev/null || echo 0)
    docker exec jellyfin rm -f "/tmp/rc_$slug.jpg" >/dev/null 2>&1
    case "$sz" in 0) v=NO_VIDEO;; 12461) v=BLACK;; *) v=LIVE;; esac
    printf '%s\t%s\t%s\n' "$new" "$v" "$sz" >> "$OUT/result.tsv"
    sleep 2
done < /tmp/slice.tsv
