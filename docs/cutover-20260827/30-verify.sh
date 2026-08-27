#!/bin/sh
# Phase 3 - verify. Run on 3.1 after it comes back.
echo "== identity =="
echo "  uptime : $(cut -d. -f1 /proc/uptime)s   (small number = it really rebooted)"
echo "  LAN    : $(uci -q get network.lan.ipaddr)"
echo "  WAN    : $(uci -q get network.wan.proto)"
echo
echo "== PPPoE session =="
ifstatus wan 2>/dev/null | grep -E '"up"|"uptime"|"l3_device"' | sed 's/^/  /'
echo "  public IP: $(ip -4 addr show pppoe-wan 2>/dev/null | sed -n 's/.*inet \([0-9.]*\).*/\1/p')"
echo "  internet : $(ping -c2 -W3 1.1.1.1 >/dev/null 2>&1 && echo OK || echo FAIL)"
echo "  dns      : $(nslookup github.com >/dev/null 2>&1 && echo OK || echo FAIL)"
echo
echo "== wifi (all three bands + MLO) =="
for i in $(ls -d /sys/class/net/wlan* /sys/class/net/mld[0-9] 2>/dev/null | xargs -n1 basename); do
  s=$(iwinfo "$i" info 2>/dev/null | sed -n 's/.*ESSID: "\(.*\)"/\1/p')
  m=$(basename "$(readlink /sys/class/net/$i/master 2>/dev/null)" 2>/dev/null)
  f=$(iwinfo "$i" info 2>/dev/null | sed -n 's/.*Channel: [0-9]* (\([0-9.]* GHz\)).*/\1/p' | head -1)
  printf "  %-7s %-14s %-9s %s\n" "$i" "${s:-<none>}" "${m:-NOBRIDGE}" "$f"
done
echo
echo "== VPN tunnels - verify by EXIT IP, never by config =="
for t in wgclient1 wgclient2 wgclient3 ovpnclient1; do
  [ -e "/sys/class/net/$t" ] || { printf "  %-12s DOWN\n" "$t"; continue; }
  printf "  %-12s -> %s\n" "$t" "$(curl -s --max-time 20 --interface $t https://api.ipify.org 2>/dev/null || echo '<no response>')"
done
echo "  expected: wgclient1=Zurich  wgclient2=Frankfurt  wgclient3=Ashburn  ovpnclient1=New York"
echo
echo "== policy rules =="
i=0; while [ $i -lt 12 ]; do
  n=$(uci -q get "route_policy.@rule[$i].name" 2>/dev/null); [ -z "$n" ] && break
  printf "  [%d] %-18s via=%-12s from=%-10s en=%s\n" "$i" "$n" \
    "$(uci -q get route_policy.@rule[$i].via)" "$(uci -q get route_policy.@rule[$i].from_type)" \
    "$(uci -q get route_policy.@rule[$i].enabled)"
  i=$((i+1))
done
echo
echo "== DHCP reservations =="
echo "  $(uci show dhcp | grep -c '@host\[') static hosts configured"
echo
echo "NEXT: power on the Proxmox server. Then from pve-01 confirm each container's exit:"
echo "  media-core / jellyfin-vod / jellyfin-npvr  -> Zurich"
echo "  scraper / log-server / pve-01 host         -> Ashburn"
