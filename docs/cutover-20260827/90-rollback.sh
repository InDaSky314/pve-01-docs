#!/bin/sh
# EMERGENCY ROLLBACK - puts 3.1 back to LAN 192.168.3.1 / WAN dhcp and reboots.
# After this, re-cable the MT2500 to the ONT to restore internet.
set -e
S=$(cat /root/cutover/.cutover-stamp 2>/dev/null || true)
[ -n "$S" ] || { echo "no cutover stamp - restore manually from /root/cfg-backups/"; ls -t /root/cfg-backups/ | head; exit 1; }
F="/root/cfg-backups/network.cutover-$S"
[ -f "$F" ] || { echo "missing $F"; exit 1; }
echo "Restoring $F"
cp /etc/config/network /root/cfg-backups/network.failed-$(date +%Y%m%d-%H%M%S)
cp "$F" /etc/config/network
sync
echo "Restored. Rebooting - reconnect at ssh root@192.168.3.1"
echo "REMEMBER: plug the MT2500 back into the ONT."
reboot
