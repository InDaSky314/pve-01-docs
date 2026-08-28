#!/bin/bash
# helper: login once, expose api() for reuse
#
# 2026-08-28: after the router cutover pve-01 sits on the far side of the UDR's
# WAN, so 192.168.1.1 is no longer reachable from here. Prefer the LAN address
# when it works (faster, no tailnet dependency) and fall back to the UDR's
# Tailscale address, which does work.
UNIFI_HOST=""
for h in 192.168.1.1 100.114.159.40; do
    if curl -sk -o /dev/null --max-time 6 "https://$h" 2>/dev/null; then UNIFI_HOST="$h"; break; fi
done
[ -z "$UNIFI_HOST" ] && { echo "uni.sh: no reachable UniFi controller (tried 192.168.1.1, 100.114.159.40)" >&2; return 1 2>/dev/null || exit 1; }
U=$(cut -d: -f1 /etc/unifi-automation.auth); P=$(cut -d: -f2- /etc/unifi-automation.auth)
CJ=/tmp/.unicj; HD=/tmp/.unihd
curl -sk -c "$CJ" -D "$HD" -o /dev/null -X POST "https://$UNIFI_HOST/api/auth/login" \
  -H "Content-Type: application/json" -d "{\"username\":\"$U\",\"password\":\"$P\"}" --max-time 25
export TOK=$(grep -i "^x-csrf-token:" "$HD" | tr -d "\r" | awk '{print $2}')
api() { # api METHOD PATH [BODY]
  local m="$1" p="$2" b="$3"
  if [ -n "$b" ]; then
    curl -sk -b "$CJ" -X "$m" -H "Content-Type: application/json" -H "X-CSRF-Token: $TOK" \
      -d "$b" "https://$UNIFI_HOST/proxy/network/api/s/default$p" --max-time 40
  else
    curl -sk -b "$CJ" -X "$m" -H "X-CSRF-Token: $TOK" \
      "https://$UNIFI_HOST/proxy/network/api/s/default$p" --max-time 40
  fi
}
