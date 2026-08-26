#!/bin/bash
# vpn-bench.sh — rank Surfshark endpoints from the UDR's native WAN path.
#
# Runs FROM pve-01 but measures ON the UDR (unifi-1.1), because the UDR is the
# only host that egresses natively: 9.1 tunnels every pve-01 container by
# source, so testing from here would be VPN-inside-VPN and meaningless.
#
# Measures per endpoint:
#   idle RTT (min/avg/max), jitter (mdev), packet loss
#   LOADED latency  -- RTT while the link is saturated (bufferbloat)
#   loaded delta    -- how much latency degrades under load; the number that
#                      actually predicts whether streaming/gaming feels bad
#
# Usage:  vpn-bench.sh [pattern]     e.g.  vpn-bench.sh 'us-'   (default: us-)
#         vpn-bench.sh 'de-|ch-'
set -u
PAT="${1:-us-}"
S="ssh -o BatchMode=yes -o ConnectTimeout=10 unifi-1.1"
W=/root/.wg-all.uci
OUT=/root/vpn-bench-$(date +%Y%m%d-%H%M).txt

EPS=$(grep -oP "(?<=end_point=')[a-z0-9-]+\.prod\.surfshark\.com" "$W" | sort -u | grep -E "$PAT")
[ -z "$EPS" ] && { echo "no endpoints match '$PAT'"; exit 1; }
echo "benchmarking $(echo "$EPS" | wc -l) endpoints matching '$PAT'" | tee "$OUT"
printf "%-34s %8s %8s %8s %7s %9s %9s\n" ENDPOINT IDLE_AVG JITTER LOSS% LOADED DELTA VERDICT | tee -a "$OUT"

# a big file on a fast, neutral CDN to create load
LOADURL="https://speed.cloudflare.com/__down?bytes=52428800"

for ep in $EPS; do
  read -r idle jit loss loaded <<<"$($S "
    ip=\$(getent hosts $ep 2>/dev/null | head -1 | awk '{print \$1}')
    [ -z \"\$ip\" ] && { echo '- - - -'; exit; }
    # idle
    o=\$(ping -c 8 -i 0.3 -W 2 -q \$ip 2>/dev/null)
    a=\$(echo \"\$o\" | grep -oE '= [0-9.]+/[0-9.]+/[0-9.]+/[0-9.]+' | cut -d/ -f2)
    m=\$(echo \"\$o\" | grep -oE '= [0-9.]+/[0-9.]+/[0-9.]+/[0-9.]+' | cut -d/ -f4)
    l=\$(echo \"\$o\" | grep -oE '[0-9]+% packet loss' | grep -oE '^[0-9]+')
    # loaded: saturate the WAN, ping during it
    curl -s -o /dev/null --max-time 12 '$LOADURL' &
    LP=\$!
    sleep 1
    lo=\$(ping -c 8 -i 0.3 -W 3 -q \$ip 2>/dev/null | grep -oE '= [0-9.]+/[0-9.]+/[0-9.]+/[0-9.]+' | cut -d/ -f2)
    wait \$LP 2>/dev/null
    echo \"\${a:--} \${m:--} \${l:--} \${lo:--}\"
  " 2>/dev/null)"
  if [ "$idle" = "-" ] || [ -z "${loaded:-}" ]; then
    printf "%-34s %8s %8s %8s %7s %9s %9s\n" "$ep" "$idle" "$jit" "$loss" "${loaded:--}" "-" "unreachable" | tee -a "$OUT"
    continue
  fi
  delta=$(awk -v a="$idle" -v b="$loaded" 'BEGIN{printf "%.1f", b-a}')
  verdict=$(awk -v d="$delta" 'BEGIN{ if(d<20) print "excellent"; else if(d<60) print "good"; else if(d<150) print "fair"; else print "bufferbloat" }')
  printf "%-34s %8s %8s %8s %7s %9s %9s\n" "$ep" "$idle" "$jit" "$loss" "$loaded" "$delta" "$verdict" | tee -a "$OUT"
done
echo | tee -a "$OUT"
echo "Ranked by loaded latency (what actually predicts felt performance):" | tee -a "$OUT"
grep -E "surfshark.com" "$OUT" | grep -vE "unreachable" | sort -k5 -n | head -5 | tee -a "$OUT"
echo "saved: $OUT"
