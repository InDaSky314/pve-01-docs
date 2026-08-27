#!/bin/sh
# OPTIONAL CLEANUP - do NOT run on cutover day.
# Fully removes the unused guest and iot networks from the MT6000, including the
# firewall objects that reference them. They are already disabled and inert, so
# this buys tidiness, not function. Run it on a calm day when you can reboot and
# check wifi afterwards.
set -e
TS=$(date +%Y%m%d-%H%M%S)
mkdir -p /root/cfg-backups
tar czf "/root/cfg-backups/etc-config-rmguestiot-$TS.tar.gz" -C /etc config
echo "backup: /root/cfg-backups/etc-config-rmguestiot-$TS.tar.gz"
echo "rollback: tar xzf /root/cfg-backups/etc-config-rmguestiot-$TS.tar.gz -C / && reboot"
echo

printf "This removes guest+iot and their firewall objects. Type YES: "; read a
[ "$a" = "YES" ] || { echo "aborted"; exit 1; }

echo "== 1/5 wifi VAPs =="
for s in guest2g guest5g iot2g iot5g; do
  uci -q delete "wireless.$s" && echo "   removed wireless.$s"
done

echo "== 2/5 dhcp sections =="
for n in guest iot; do uci -q delete "dhcp.$n" && echo "   removed dhcp.$n"; done

echo "== 3/5 firewall rules / forwardings (highest index first) =="
for t in rule forwarding redirect; do
  i=$(uci show firewall 2>/dev/null | grep -c "@$t\[")
  while [ "$i" -ge 0 ]; do
    src=$(uci -q get "firewall.@$t[$i].src" 2>/dev/null)
    dst=$(uci -q get "firewall.@$t[$i].dest" 2>/dev/null)
    case "$src|$dst" in
      guest\|*|iot\|*|*\|guest|*\|iot)
        uci -q delete "firewall.@$t[$i]" && echo "   removed firewall.@$t[$i] ($src -> $dst)" ;;
    esac
    i=$((i-1))
  done
done

echo "== 4/5 named firewall sections =="
for s in $(uci show firewall 2>/dev/null | sed -n "s/^firewall\.\([a-z0-9_]*\)=.*/\1/p" | sort -u); do
  case "$s" in *guest*|*iot*)
    uci -q delete "firewall.$s" && echo "   removed firewall.$s" ;;
  esac
done
# zones last - they are what everything else pointed at
for z in 3 2; do
  n=$(uci -q get "firewall.@zone[$z].name" 2>/dev/null)
  case "$n" in guest|iot) uci -q delete "firewall.@zone[$z]" && echo "   removed zone $n" ;; esac
done

echo "== 5/5 network interfaces + route_policy reference =="
uci -q delete network.guest && echo "   removed network.guest"
uci -q delete network.iot   && echo "   removed network.iot"
cur=$(uci -q get route_policy.global.append_source_if 2>/dev/null)
if [ -n "$cur" ]; then
  new=$(echo "$cur" | sed "s/\biot\b//g; s/\bguest\b//g; s/  */ /g; s/^ //; s/ $//")
  uci set route_policy.global.append_source_if="$new"
  echo "   append_source_if: '$cur' -> '$new'"
fi

uci commit
echo
echo "Committed. REBOOT now and verify wifi still works:"
echo "  reboot"
echo "  # then: iwinfo ; ip -br addr show ; ping -c2 1.1.1.1"
