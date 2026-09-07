#!/bin/bash
# wg-snapshot.sh — periodic WireGuard tunnel health snapshot, pushed to
# the log-server's Loki (CT 107). `wg show` only exposes CURRENT state
# (no history), and the router's own log buffer is tiny (~64KB) — this
# closes the gap that made "was the tunnel actually stalled" an
# unresolvable question during the 2026-07-19 channel-117 diagnosis.
# Runs on the HOST (needs pve-01's existing non-interactive root SSH
# key to the Flint 2 — that key doesn't exist inside CT 107).
#
# 2026-07-25: covers ALL WireGuard interfaces, not just wgclient1.
# Interfaces are discovered from `wg show all dump` rather than
# hardcoded, so a future tunnel is picked up with no code change. This
# closed a real blind spot: when Tunnel 1 moved from OpenVPN to
# WireGuard (wgclient2, scraper), its multi-hour 2026-07-24 outage had
# no history to reconstruct from.
#
# Also 2026-07-25: the interface line of a wg dump contains the tunnel's
# PRIVATE KEY, and this script used to ship it to Loki verbatim. It is
# now redacted before the push — private keys do not belong in a log
# aggregator. (Key material is preserved properly by the router backups
# in /root/router-backups, 600, which is the right place for it.)
set -euo pipefail

LOKI_URL="http://192.168.9.164:3100/loki/api/v1/push"
ROUTER="root@192.168.9.1"   # BE9300 (was the MT6000 at this address before the 2026-08-27 cutover)
TS_NS=$(date +%s%N)
NOW=$(date +%s)

json_escape() { python3 -c 'import json,sys; print(json.dumps(sys.stdin.read())[1:-1])'; }

# Push one log line to Loki, labelled with the interface it describes.
push() {
    local iface="$1" line="$2"
    local escaped
    escaped=$(printf '%s' "$line" | json_escape)
    curl -s -X POST "$LOKI_URL" \
        -H "Content-Type: application/json" \
        -d '{
          "streams": [
            {
              "stream": { "job": "wg-snapshot", "host": "be9300", "iface": "'"$iface"'" },
              "values": [ [ "'"$TS_NS"'", "'"$escaped"'" ] ]
            }
          ]
        }' -o /dev/null -w "" \
        || echo "wg-snapshot: push to Loki failed ($iface)" >&2
}

ALL=$(ssh -o ConnectTimeout=5 -o BatchMode=yes "$ROUTER" "wg show all dump" 2>&1) || {
    # Router unreachable or wg broken — record it against a sentinel
    # iface so the gap is visible in Loki rather than simply absent.
    push "unknown" "level=alert event=wg_health handshake_age_s=-1 msg=\"SSH_OR_WG_FAILED: $(printf '%s' "$ALL" | head -1)\""
    exit 0
}

IFACES=$(printf '%s\n' "$ALL" | awk -F'\t' 'NF==5 {print $1}')

if [[ -z "$IFACES" ]]; then
    push "unknown" "level=alert event=wg_health handshake_age_s=-1 msg=\"no WireGuard interfaces present\""
    exit 0
fi

for IFACE in $IFACES; do
    # Raw dump for this interface, private key redacted. In `wg show all
    # dump` an interface line has 5 fields ($2 = private key) and a peer
    # line has 9.
    DUMP=$(printf '%s\n' "$ALL" | awk -F'\t' -v i="$IFACE" '
        $1 == i {
            if (NF == 5) { $2 = "(redacted)" }
            out = $2
            for (n = 3; n <= NF; n++) out = out "\t" $n
            print out
        }')
    push "$IFACE" "$DUMP"

    # Derived health metric (separate line, same stream) so a dashboard
    # can graph handshake age without parsing the raw dump above —
    # WireGuard peers go silent (no re-handshake) rather than erroring,
    # so staleness here is the actual failure signal, not a log line to
    # grep for. Peer line: $6 = latest_handshake, $4 = endpoint.
    read -r HANDSHAKE ENDPOINT <<<"$(printf '%s\n' "$ALL" \
        | awk -F'\t' -v i="$IFACE" '$1 == i && NF == 9 { print $6, $4; exit }')"

    if [[ "${HANDSHAKE:-}" =~ ^[0-9]+$ ]] && [[ "$HANDSHAKE" -gt 0 ]]; then
        AGE=$(( NOW - HANDSHAKE ))
        push "$IFACE" "level=info event=wg_health handshake_age_s=${AGE} endpoint=\"${ENDPOINT}\""
    else
        # handshake of 0 means the peer has never completed one.
        push "$IFACE" "level=alert event=wg_health handshake_age_s=-1 msg=\"no valid handshake for ${IFACE}\""
    fi
done
