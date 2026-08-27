#!/bin/sh
# Phase 1 - SAFE. Changes nothing about WAN or LAN addressing.
# Adds DHCP reservations and VPN MAC rules so they are already in place
# when the LAN flips to 192.168.9.1. Idempotent - safe to run twice.
set -e
TS=$(date +%Y%m%d-%H%M%S)
B=/root/cfg-backups; mkdir -p "$B"
echo "== backing up =="
tar czf "$B/etc-config-precutover-$TS.tar.gz" -C /etc config
for f in network wireless dhcp firewall route_policy gl-client; do
  cp "/etc/config/$f" "$B/$f.precutover-$TS" 2>/dev/null || true
done
echo "   $B/etc-config-precutover-$TS.tar.gz"
echo "$TS" > /root/cutover/.last-backup-stamp

echo "== importing DHCP reservations from the MT6000 =="
# Reservations are re-created on the 192.168.9.0/24 scope, which 3.1 will own.
add_host() { # mac ip name
  for i in $(seq 0 200); do
    m=$(uci -q get "dhcp.@host[$i].mac") || true
    [ -z "$m" ] && break
    if [ "$(echo "$m" | tr a-z A-Z)" = "$(echo "$1" | tr a-z A-Z)" ]; then
      uci set "dhcp.@host[$i].ip=$2"; uci set "dhcp.@host[$i].name=$3"; return 0
    fi
  done
  uci add dhcp host >/dev/null
  uci set dhcp.@host[-1].mac="$1"; uci set dhcp.@host[-1].ip="$2"; uci set dhcp.@host[-1].name="$3"
}
while IFS='|' read -r mac ip name; do
  [ -z "$mac" ] && continue
  add_host "$mac" "$ip" "$name"; echo "   $name  $ip  $mac"
done < /root/cutover/dhcp-reservations.txt
uci commit dhcp

echo "== VPN MAC rules: preserve today's egress =="
# Today on the MT6000: media stack -> Zurich, log-server + pve-01 host -> Ashburn.
# 3.1's Swiss rule already carries the media MACs. Add the Ashburn members.
ASHBURN_TID=""
i=0
while [ $i -lt 12 ]; do
  n=$(uci -q get "route_policy.@rule[$i].name" 2>/dev/null)
  [ -z "$n" ] && break
  case "$n" in
    *Ashburn*|*ashburn*) ASHBURN_TID=$(uci -q get "route_policy.@rule[$i].tunnel_id"); break ;;
  esac
  i=$((i+1))
done
if [ -z "$ASHBURN_TID" ]; then
  echo "   !! could not find the US-Ashburn tunnel - add its MACs by hand in the GUI"
else
  echo "   US-Ashburn tunnel_id=$ASHBURN_TID"
  MACS='BC:24:11:28:55:77 BC:24:11:EF:79:09 7C:2B:E1:13:DE:30'
  JSON=$(for m in $MACS; do printf '"%s",' "$m"; done | sed 's/,$//')
  ubus call gl-session call "{\"module\":\"vpn-client\",\"func\":\"set_tunnel\",\"params\":{\"tunnel_id\":$ASHBURN_TID,\"from\":{\"type\":\"mac\",\"mac_list\":[$JSON]}}}" \
    && echo "   scraper + log-server + pve-01 host -> Ashburn"
fi

echo
echo "PRESTAGE COMPLETE. Nothing disruptive has happened yet."
echo "Next, at the rack: sh /root/cutover/20-cutover.sh"
