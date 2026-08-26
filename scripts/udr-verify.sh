#!/bin/bash
# UDR persistence check. Usage: udr-verify.sh [baseline|check]
# Captures the state that must survive a reboot, and diffs against it.
OUT=/root/udr-state-$1.txt
S="ssh -o BatchMode=yes -o ConnectTimeout=10 unifi-1.1"
{
  echo "== $(date -Is) mode=$1 =="
  echo "-- wireguard tunnels (interface + handshake age) --"
  $S 'wg show 2>/dev/null | grep -E "^interface|latest handshake"' 2>/dev/null
  echo "-- LAN bypass routes in tunnel tables (RUNTIME - the fragile bit) --"
  for t in 178 179; do
    echo "   table $t:"; $S "ip route show table $t 2>/dev/null | grep -E '192\.168\.(9|2|3|8)\.'" 2>/dev/null | sed 's/^/     /'
  done
  echo "-- firewall zones --"
  $S 'iptables -S 2>/dev/null | grep -c "192.168.20"' 2>/dev/null | sed 's/^/   iptables rules mentioning VLAN20: /'
  echo "-- SSIDs --"
  $S 'mongo --port 27117 ace --quiet --eval "db.wlanconf.find({},{name:1,enabled:1,networkconf_id:1}).forEach(function(w){print(\"   \"+w.name+\" enabled=\"+w.enabled+\" net=\"+w.networkconf_id)})"' 2>/dev/null
  echo "-- networks --"
  $S 'mongo --port 27117 ace --quiet --eval "db.networkconf.find({},{name:1,vlan:1,purpose:1}).forEach(function(n){print(\"   \"+n.name+\" vlan=\"+(n.vlan||\"-\")+\" \"+n.purpose)})"' 2>/dev/null
  echo "-- tailscale on the UDR --"
  $S '/data/tailscale/manage.sh status 2>&1 | head -2' 2>/dev/null
  echo "-- clients on VLAN20 --"
  $S 'mongo --port 27117 ace --quiet --eval "print(db.user.count())"' 2>/dev/null | sed 's/^/   known clients: /'
} > "$OUT" 2>&1
echo "wrote $OUT"
