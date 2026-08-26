#!/bin/sh
# Re-add LAN-to-LAN bypass routes into the WireGuard policy tables after boot.
#
# Why: UniFi traffic routes mark client traffic whenever the destination is not
# in UBIOS_local_network -- and that set holds ONLY this controller's own
# subnets. Other private networks reached via the WAN (192.168.9.0/24 = pve-01
# + media stack; 192.168.2/3/8.0/24 = the GL routers) therefore get marked into
# the VPN table and dropped by the provider, silently severing LAN-to-LAN.
# Reboot test 2026-08-26 confirmed these routes do NOT survive a reboot.
#
# A table only exists while its traffic route is ENABLED, so a missing table is
# normal, not an error -- we simply skip it. Bounded wait, never blocks boot.
GW=192.168.2.1
NETS="192.168.9.0/24 192.168.2.0/24 192.168.3.0/24 192.168.8.0/24"
TABLES="178 179"

i=0
while [ $i -lt 12 ]; do
  for t in $TABLES; do
    if ip route show table $t 2>/dev/null | grep -q .; then
      for n in $NETS; do ip route replace $n via $GW dev eth4 table $t 2>/dev/null; done
    fi
  done
  i=$((i+1)); sleep 5
done
logger -t lan-bypass "LAN bypass routes applied to any active wg tables ($TABLES)"
