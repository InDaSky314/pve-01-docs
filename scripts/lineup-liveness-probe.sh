#!/bin/bash
# Liveness probe for the whole lineup.
#
# A dead IPTV feed does not usually fail to connect -- it serves a black
# slate. So the test is: decode 8 seconds and take a frame. A dead feed
# produces a byte-identical black JPEG (12,461 bytes on this provider);
# a live one produces something else.
#
# Concurrency is deliberately low: saturating the provider's connection
# limit would break live TV for the household.
set -u
PL=/srv/media-core/threadfin/conf/playlist.m3u
OUT=/tmp/live; mkdir -p "$OUT"
RES=/tmp/live/result.tsv
FF=/usr/lib/jellyfin-ffmpeg/ffmpeg
CONCURRENCY=3

probe() {
  local name="$1"
  local slug; slug=$(echo "$name" | md5sum | cut -c1-16)
  local url; url=$(grep -A1 -F "tvg-name=\"$name\"" "$PL" | tail -1)
  if [ -z "$url" ]; then printf '%s\tNO_URL\t0\n' "$name" >> "$RES"; return; fi
  docker exec jellyfin timeout 40 $FF -y -loglevel quiet -rw_timeout 15000000 \
      -i "$url" -t 8 -ss 6 -frames:v 1 -q:v 5 "/tmp/lv_$slug.jpg" >/dev/null 2>&1
  local sz
  sz=$(docker exec jellyfin stat -c%s "/tmp/lv_$slug.jpg" 2>/dev/null || echo 0)
  docker exec jellyfin rm -f "/tmp/lv_$slug.jpg" >/dev/null 2>&1
  if [ "$sz" = "0" ]; then printf '%s\tNO_VIDEO\t0\n' "$name" >> "$RES"
  elif [ "$sz" = "12461" ]; then printf '%s\tBLACK\t%s\n' "$name" "$sz" >> "$RES"
  else printf '%s\tLIVE\t%s\n' "$name" "$sz" >> "$RES"; fi
}

: > "$RES"
grep -oE 'tvg-name="[^"]+"' "$PL" | sed 's/tvg-name="//;s/"$//' | sort -u > /tmp/live/names.txt
total=$(wc -l < /tmp/live/names.txt)
echo "probing $total channels, concurrency $CONCURRENCY"
n=0
while IFS= read -r name; do
  probe "$name" &
  n=$((n+1))
  while [ "$(jobs -rp | wc -l)" -ge "$CONCURRENCY" ]; do sleep 0.4; done
  [ $((n % 50)) -eq 0 ] && echo "... $n/$total"
done < /tmp/live/names.txt
wait
echo "done"
cut -f2 "$RES" | sort | uniq -c
