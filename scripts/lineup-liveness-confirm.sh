#!/bin/bash
# Second pass over everything the fast sweep called BLACK or NO_VIDEO.
#
# The fast sweep ran 3 streams at a time and its NO_VIDEO count jumped from 3
# to 82 partway through -- that is the shape of a provider connection limit,
# not 79 channels dying at once. Never take destructive action on a
# measurement you could not make: this pass runs ONE at a time with a longer
# timeout, and only a channel that fails twice is treated as dead.
set -u
PL=/srv/media-core/threadfin/conf/playlist.m3u
FF=/usr/lib/jellyfin-ffmpeg/ffmpeg
IN=/tmp/live/result.tsv
OUT=/tmp/live/confirm.tsv
: > "$OUT"

awk -F'\t' '$2=="BLACK" || $2=="NO_VIDEO" {print $1}' "$IN" > /tmp/live/suspects.txt
total=$(wc -l < /tmp/live/suspects.txt)
echo "confirm pass: $total suspects, serial, 20s each"
n=0
while IFS= read -r name; do
  slug=$(echo "$name" | md5sum | cut -c1-16)
  url=$(grep -A1 -F "tvg-name=\"$name\"" "$PL" | tail -1)
  if [ -z "$url" ]; then printf '%s\tNO_URL\t0\n' "$name" >> "$OUT"; continue; fi
  docker exec jellyfin timeout 70 $FF -y -loglevel quiet -rw_timeout 30000000 \
      -i "$url" -t 14 -ss 11 -frames:v 1 -q:v 5 "/tmp/cf_$slug.jpg" >/dev/null 2>&1
  sz=$(docker exec jellyfin stat -c%s "/tmp/cf_$slug.jpg" 2>/dev/null || echo 0)
  docker exec jellyfin rm -f "/tmp/cf_$slug.jpg" >/dev/null 2>&1
  case "$sz" in 0) v=NO_VIDEO;; 12461) v=BLACK;; *) v=LIVE;; esac
  printf '%s\t%s\t%s\n' "$name" "$v" "$sz" >> "$OUT"
  n=$((n+1)); [ $((n % 25)) -eq 0 ] && echo "... $n/$total"
  sleep 1
done < /tmp/live/suspects.txt
echo "confirm done"
cut -f2 "$OUT" | sort | uniq -c
