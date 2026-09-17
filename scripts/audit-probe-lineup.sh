#!/bin/bash
# WS1a: does each curated non-event channel carry footage? Sequential on the
# single tuner; yields if anyone starts watching live TV. Output: TSV.
OUT=/root/audit-20260917/lineup-probe.tsv
SLATE_FP=3cdbaf640cc0     # the provider's "unavailable" clip, first 1 MB
echo -e "chno\tname\tgroup\thttp\tbytes\tfp\tfreeze\tverdict" > "$OUT"
# channel list from the live playlist, excluding event/PPV blocks (they are slots, not channels)
pct exec 105 -- python3 - <<'PY' > /root/audit-20260917/channels.tsv
import re
lines=open("/srv/media-core/threadfin/conf/playlist.m3u",encoding="utf-8").read().splitlines()
skip=("Soccer PPV","DAZN PPV","DAZN PPV GB","Sky Sports+ PPV","Peacock PPV")
for i,l in enumerate(lines):
    if not l.startswith("#EXTINF"): continue
    g=re.search(r'group-title="([^"]*)"',l); n=re.search(r'tvg-name="([^"]*)"',l); c=re.search(r'tvg-chno="([^"]*)"',l)
    if not (g and n and c) or g.group(1) in skip: continue
    url=lines[i+1].strip() if i+1<len(lines) else ""
    print(f"{c.group(1)}\t{n.group(1)}\t{g.group(1)}\t{url}")
PY
total=$(wc -l < /root/audit-20260917/channels.tsv); echo "probing $total channels" >&2
n=0
while IFS=$'\t' read -r chno name group url; do
  n=$((n+1))
  # yield to a live viewer
  while pct exec 105 -- bash -c 'K=$(python3 -c "
import sqlite3
c=sqlite3.connect(\"file:/srv/media-core/jellyfin/config/data/jellyfin.db?mode=ro\",uri=True)
r=list(c.execute(\"select AccessToken from ApiKeys limit 1\")); print(r[0][0] if r else \"\")"); curl -s --max-time 10 -H "X-Emby-Token: $K" http://127.0.0.1:8096/Sessions | python3 -c "import json,sys; sys.exit(0 if any((s.get(\"NowPlayingItem\") or {}).get(\"Type\")==\"TvChannel\" for s in json.load(sys.stdin)) else 1)"' 2>/dev/null; do echo "$(date +%H:%M) live viewer present, waiting" >&2; sleep 120; done
  # do not probe while a recording runs
  while systemctl list-units 'mct-*' --state=running --no-pager 2>/dev/null | grep -q "MCT capture"; do sleep 300; done
  res=$(pct exec 105 -- bash -c "out=\$(timeout 12 curl -sL --max-time 8 -r 0-1200000 '$url' -o /tmp/lp.ts -w '%{http_code}'); sz=\$(stat -c %s /tmp/lp.ts 2>/dev/null||echo 0); fp=\$(head -c 1000000 /tmp/lp.ts 2>/dev/null | md5sum | cut -c1-12); fz=n/a; if [ \"\$sz\" -gt 300000 ]; then e=\$(docker exec jellyfin /usr/lib/jellyfin-ffmpeg/ffmpeg -v info -t 6 -i /tmp/lp.ts -an -vf freezedetect=n=-50dB:d=2 -f null - 2>&1); s=\$(echo \"\$e\" | grep -c freeze_start); en=\$(echo \"\$e\" | grep -c freeze_end); [ \"\$s\" -gt 0 ] && [ \"\$en\" -eq 0 ] && fz=frozen || fz=moving; fi; rm -f /tmp/lp.ts; echo \"\$out \$sz \$fp \$fz\"" 2>/dev/null)
  set -- $res; http=${1:-000}; bytes=${2:-0}; fp=${3:-}; fz=${4:-n/a}
  if [ "$fp" = "$SLATE_FP" ]; then v=SLATE
  elif [ "$bytes" -lt 300000 ]; then v=DEAD
  elif [ "$fz" = frozen ]; then v=FROZEN
  else v=LIVE; fi
  echo -e "$chno\t$name\t$group\t$http\t$bytes\t$fp\t$fz\t$v" >> "$OUT"
  [ $((n % 25)) -eq 0 ] && echo "$(date +%H:%M) $n/$total" >&2
  sleep 1
done < /root/audit-20260917/channels.tsv
echo "done: $(date +%H:%M)" >&2; cut -f8 "$OUT" | tail -n +2 | sort | uniq -c
