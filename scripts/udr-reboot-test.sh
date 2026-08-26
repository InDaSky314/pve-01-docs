#!/bin/bash
# One-shot UDR reboot-persistence test. Durable: runs from systemd, not a chat session.
# Refuses to reboot if a DVR recording is in progress.
LOG=/root/udr-reboot-test.log
exec >>"$LOG" 2>&1
echo "===== $(date -Is) UDR reboot test starting ====="

# --- pre-flight: never reboot during a recording ---
KEY=$(pct exec 105 -- cat /srv/media-core/.jellyfin_api_key 2>/dev/null)
INPROG=$(pct exec 105 -- curl -s "http://127.0.0.1:8096/LiveTv/Timers?api_key=$KEY" 2>/dev/null \
  | python3 -c 'import json,sys
try: print(sum(1 for t in json.load(sys.stdin).get("Items",[]) if t.get("Status")=="InProgress"))
except Exception: print("ERR")' 2>/dev/null)
echo "pre-flight: InProgress recordings = ${INPROG:-unknown}"
if [ "$INPROG" != "0" ]; then
  echo "ABORT: recording in progress (or state unknown). Not rebooting."; exit 0
fi

/root/udr-verify.sh baseline2
echo "--- rebooting UDR ---"
ssh -o BatchMode=yes -o ConnectTimeout=10 unifi-1.1 reboot 2>&1 | head -3

for i in $(seq 1 40); do
  sleep 15
  if ssh -o BatchMode=yes -o ConnectTimeout=8 unifi-1.1 true 2>/dev/null; then
    echo "UDR back via LAN after ~$((i*15))s"; BACK=lan; break
  fi
  if ssh -o BatchMode=yes -o ConnectTimeout=8 -i /root/.ssh/id_ed25519_routers root@100.114.159.40 true 2>/dev/null; then
    echo "UDR back via TAILSCALE after ~$((i*15))s (LAN path still down)"; BACK=ts; break
  fi
done
[ -z "$BACK" ] && { echo "!!! UDR DID NOT COME BACK after 10 minutes !!!"; exit 1; }

sleep 45
/root/udr-verify.sh check
echo "--- DIFF baseline2 vs check ---"
diff /root/udr-state-baseline2.txt /root/udr-state-check.txt || true
echo "--- client proxy check: IoT devices back on VLAN20? ---"
ssh -o BatchMode=yes unifi-1.1 'mongo --port 27117 ace --quiet --eval "print(db.user.count())"' 2>/dev/null
echo "--- IPTV still Swiss (should be unaffected by UDR) ---"
pct exec 105 -- curl -s --max-time 15 https://ipinfo.io/json 2>/dev/null | head -c 200
echo; echo "===== $(date -Is) test complete ====="
