#!/bin/sh
# Phase 2 - THE SWITCH. Run at the rack, after the Proxmox server is shut down
# and the cables are moved. This changes WAN to PPPoE and LAN to 192.168.9.1,
# then reboots. You WILL lose this SSH session.
set -e
[ -f /root/cutover/.pppoe-creds ] || { echo "FATAL: /root/cutover/.pppoe-creds missing"; exit 1; }
. /root/cutover/.pppoe-creds
[ -n "$PPPOE_USER" ] && [ -n "$PPPOE_PASS" ] || { echo "FATAL: credentials empty"; exit 1; }

TS=$(date +%Y%m%d-%H%M%S)
B=/root/cfg-backups; mkdir -p "$B"
tar czf "$B/etc-config-cutover-$TS.tar.gz" -C /etc config
cp /etc/config/network "$B/network.cutover-$TS"
echo "$TS" > /root/cutover/.cutover-stamp
echo "backup: $B/etc-config-cutover-$TS.tar.gz"
echo

cat <<'WARN'
About to:
  WAN  eth0 : dhcp  ->  PPPoE (Telekom)
  LAN       : 192.168.3.1  ->  192.168.9.1
  then REBOOT.

Confirm BEFORE continuing:
  [ ] Proxmox server is shut down
  [ ] 3.1's WAN port is cabled to the Telekom ONT (where the MT2500's WAN was)
  [ ] The MT2500 (2.1) is unplugged from the ONT - two PPPoE sessions will fight
  [ ] You are physically at the rack

WARN
printf "Type YES to proceed: "; read ans
[ "$ans" = "YES" ] || { echo "aborted, nothing changed"; exit 1; }

echo "== WAN -> PPPoE =="
uci set network.wan.proto='pppoe'
uci set network.wan.username="$PPPOE_USER"
uci set network.wan.password="$PPPOE_PASS"
uci set network.wan.ipv6='auto'
uci -q delete network.wan.ipaddr
uci -q delete network.wan.netmask
uci -q delete network.wan.gateway

echo "== LAN -> 192.168.9.1 =="
uci set network.lan.ipaddr='192.168.9.1'
uci set network.lan.netmask='255.255.255.0'
uci set dhcp.lan.start='100'
uci set dhcp.lan.limit='150'

uci commit network
uci commit dhcp

echo
echo "Committed. Rebooting now."
echo "Reconnect at:  ssh root@192.168.9.1     (renew your DHCP lease first)"
echo "Then run:      sh /root/cutover/30-verify.sh"
sync
reboot
