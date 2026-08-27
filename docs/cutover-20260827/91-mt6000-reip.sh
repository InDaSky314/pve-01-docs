#!/bin/sh
# Run this ON THE MT6000 (old 9.1) to move it off 192.168.9.1 so 3.1 can take it.
# New role: TV / Chromecast segment on 192.168.5.0/24, uplink DHCP from 3.1.
set -e
TS=$(date +%Y%m%d-%H%M%S)
mkdir -p /root/cfg-backups
tar czf "/root/cfg-backups/etc-config-reip-$TS.tar.gz" -C /etc config
cp /etc/config/network "/root/cfg-backups/network.reip-$TS"
echo "backup: /root/cfg-backups/etc-config-reip-$TS.tar.gz"

echo "== LAN 192.168.9.1 -> 192.168.5.1 =="
uci set network.lan.ipaddr='192.168.5.1'
uci set network.lan.netmask='255.255.255.0'
# guest/iot are disabled but both collide with 3.1's ranges - re-address so they
# can never conflict if someone enables them later.
uci -q set network.guest.ipaddr='192.168.15.1'
uci -q set network.iot.ipaddr='192.168.16.1'
uci set network.wan.proto='dhcp'

echo "== re-scope the TV reservations onto 192.168.5.0/24 =="
i=0
while [ $i -lt 200 ]; do
  m=$(uci -q get "dhcp.@host[$i].mac" 2>/dev/null) || true
  [ -z "$m" ] && break
  ip=$(uci -q get "dhcp.@host[$i].ip" 2>/dev/null)
  case "$ip" in
    192.168.9.*) new="192.168.5.$(echo "$ip" | cut -d. -f4)"
                 uci set "dhcp.@host[$i].ip=$new"
                 echo "   $(uci -q get dhcp.@host[$i].name): $ip -> $new" ;;
  esac
  i=$((i+1))
done

uci commit network
uci commit dhcp
echo
echo "Committed. Rebooting - reconnect at ssh root@192.168.5.1"
sync
reboot
