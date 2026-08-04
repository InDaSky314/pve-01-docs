#!/bin/bash
# check-iptv-stream.sh — detect active IPTV streams and assess Threadfin restart safety
#
# Checks:
#   1. Router conntrack for active streams from the Chromecast (TiviMate)
#   2. Threadfin's own stream state (ffmpeg processes, stream dir)
#   3. Jellyfin recording status and upcoming timers
#
# Key insight: TiviMate connects directly to the IPTV provider, NOT through
# Threadfin — so TiviMate streams are unaffected by Threadfin restarts.
# Only Threadfin's own streams (Jellyfin Live TV) and recordings are at risk.
#
# Usage:
#   check-iptv-stream.sh              # full status report
#   check-iptv-stream.sh --restart-ok # exit 0 if safe to restart Threadfin, 1 if not
#   check-iptv-stream.sh --json       # JSON output for scripting

set -euo pipefail

ROUTER=192.168.9.1
CHROMECAST_IP=192.168.9.203
CHROMECAST_MAC="1c:53:f9:26:34:e9"
CT105_IP=192.168.9.50
JF_PORT=8096
JF_KEY_FILE="/srv/media-core/.jellyfin_api_key"
STREAM_SAMPLE_INTERVAL=2
STREAM_THRESHOLD_BYTES=250000  # 250KB in sample interval = ~1Mbps, clearly a stream

MODE="${1:-status}"

# --- helpers ---

jf_api_key() {
    pct exec 105 -- cat "$JF_KEY_FILE" 2>/dev/null || echo ""
}

log() { [[ "$MODE" != "--json" ]] && echo "$@"; }

# --- 1. Chromecast stream detection via conntrack byte-counter diffing ---

check_chromecast_stream() {
    local snap1 snap2

    # Test router reachability with a lightweight command first
    if ! ssh -o ConnectTimeout=5 -o BatchMode=yes root@"$ROUTER" "true" 2>/dev/null; then
        echo "no_data"
        return
    fi

    snap1=$(ssh -o ConnectTimeout=5 -o BatchMode=yes root@"$ROUTER" \
        "cat /proc/net/nf_conntrack 2>/dev/null | grep 'src=$CHROMECAST_IP '" 2>/dev/null || true)

    # Empty result with reachable router = no connections = idle
    if [[ -z "$snap1" ]]; then
        echo "idle"
        return
    fi

    sleep "$STREAM_SAMPLE_INTERVAL"

    snap2=$(ssh -o ConnectTimeout=5 -o BatchMode=yes root@"$ROUTER" \
        "cat /proc/net/nf_conntrack 2>/dev/null | grep 'src=$CHROMECAST_IP '" 2>/dev/null || true)

    if [[ -z "$snap2" ]]; then
        echo "idle"
        return
    fi

    # Each conntrack line has original + reply direction, each with bytes=N.
    # Use Python for reliable parsing: extract per-connection total bytes,
    # diff between snapshots, flag anything with high throughput as a stream.
    python3 -c "
import sys

def parse_conns(text):
    \"\"\"Parse conntrack lines into {(dst, dport): total_bytes}.\"\"\"
    conns = {}
    for line in text.strip().split('\n'):
        if not line.strip():
            continue
        parts = line.split()
        dst = dport = None
        total_bytes = 0
        seen_first_src = False
        for p in parts:
            if p.startswith('dst=') and dst is None:
                dst = p.split('=', 1)[1]
            if p.startswith('dport=') and dport is None:
                dport = p.split('=', 1)[1]
            if p.startswith('bytes='):
                total_bytes += int(p.split('=', 1)[1])
        if dst and dport:
            key = (dst, dport)
            conns[key] = conns.get(key, 0) + total_bytes
    return conns

snap1 = '''$snap1'''
snap2 = '''$snap2'''

c1 = parse_conns(snap1)
c2 = parse_conns(snap2)

threshold = $STREAM_THRESHOLD_BYTES
interval = $STREAM_SAMPLE_INTERVAL
best_diff = 0
best_dst = ''

for key, b2 in c2.items():
    b1 = c1.get(key, 0)
    diff = b2 - b1
    if diff > best_diff:
        best_diff = diff
        best_dst = key[0]

if best_diff > threshold:
    mbps = best_diff * 8 / interval / 1_000_000
    print(f'active:{best_dst}:{mbps:.1f}Mbps')
else:
    print('idle')
" 2>/dev/null || echo "idle"
}

# --- 2. Threadfin stream state ---

check_threadfin_streams() {
    local ffmpeg_count stream_dir_count
    ffmpeg_count=$(pct exec 105 -- pgrep -c -f 'ffmpeg.*threadfin' 2>/dev/null || true)
    ffmpeg_count=${ffmpeg_count:-0}
    stream_dir_count=$(pct exec 105 -- bash -c \
        'find /tmp/threadfin/streams -mindepth 1 -maxdepth 1 2>/dev/null | wc -l' 2>/dev/null || true)
    stream_dir_count=${stream_dir_count:-0}

    if (( ffmpeg_count > 0 )); then
        echo "active:ffmpeg=$ffmpeg_count"
    elif (( stream_dir_count > 0 )); then
        echo "stale:dirs=$stream_dir_count"
    else
        echo "idle"
    fi
}

# --- 3. Recording status + upcoming timers ---

check_recordings() {
    local api_key
    api_key=$(jf_api_key)
    if [[ -z "$api_key" ]]; then
        echo "no_key"
        return
    fi

    local auth="MediaBrowser Token=\"$api_key\""

    # Check active recordings
    local active
    active=$(curl -s --max-time 10 \
        -H "Authorization: $auth" \
        "http://$CT105_IP:$JF_PORT/emby/LiveTv/Recordings?IsInProgress=true&Limit=1" 2>/dev/null || echo "")

    local active_count=0
    if [[ -n "$active" ]]; then
        active_count=$(echo "$active" | python3 -c "import sys,json; print(json.load(sys.stdin).get('TotalRecordCount',0))" 2>/dev/null || echo "0")
    fi

    # Check upcoming timers (next 2 hours)
    local timers
    timers=$(curl -s --max-time 10 \
        -H "Authorization: $auth" \
        "http://$CT105_IP:$JF_PORT/LiveTv/Timers" 2>/dev/null || echo "")

    local timer_info=""
    if [[ -n "$timers" ]]; then
        timer_info=$(echo "$timers" | python3 -c "
import sys, json
from datetime import datetime, timezone, timedelta
try:
    data = json.load(sys.stdin)
    items = data.get('Items', [])
    now = datetime.now(timezone.utc)
    window = now + timedelta(hours=2)
    upcoming = []
    for t in items:
        start = t.get('StartDate', '')
        if start:
            try:
                dt = datetime.fromisoformat(start.rstrip('Z')).replace(tzinfo=timezone.utc)
                if now <= dt <= window:
                    mins = int((dt - now).total_seconds() / 60)
                    upcoming.append(mins)
            except (ValueError, TypeError):
                pass
    if upcoming:
        upcoming.sort()
        print(f'{len(upcoming)} {upcoming[0]}')
    else:
        print('0')
except Exception:
    print('0')
" 2>/dev/null || echo "0")
    fi

    local upcoming_count="${timer_info%% *}"
    local next_timer_mins="${timer_info#* }"
    [[ "$upcoming_count" == "$next_timer_mins" ]] && next_timer_mins=""

    if (( active_count > 0 )); then
        echo "recording:active=$active_count"
    elif (( upcoming_count > 0 )) && [[ -n "$next_timer_mins" ]]; then
        echo "scheduled:in_${next_timer_mins}m:count=$upcoming_count"
    else
        echo "none"
    fi
}

# --- 4. TiviMate recording check (filesystem-based) ---

check_tivimate_recording() {
    local active
    active=$(pct exec 105 -- bash -c '
        now=$(date +%s)
        for d in /srv/media-core/media/recordings/Tivimate /srv/media-core/media/recordings/Sports; do
            [ -d "$d" ] || continue
            find "$d" -name "*.ts" -mmin -1 2>/dev/null | head -1
        done
    ' 2>/dev/null || true)

    if [[ -n "$active" ]]; then
        echo "active"
    else
        echo "idle"
    fi
}

# --- main ---

chromecast_status=$(check_chromecast_stream)
threadfin_status=$(check_threadfin_streams)
recording_status=$(check_recordings)
tivimate_rec=$(check_tivimate_recording)

# Determine restart safety
restart_safe="true"
restart_reason=""

case "$threadfin_status" in
    active:*)
        restart_safe="false"
        restart_reason="Threadfin has active streams (${threadfin_status#active:})"
        ;;
esac

case "$recording_status" in
    recording:*)
        restart_safe="false"
        restart_reason="Jellyfin recording in progress"
        ;;
esac

if [[ "$tivimate_rec" == "active" ]]; then
    restart_safe="false"
    restart_reason="TiviMate recording in progress"
fi

case "$MODE" in
    --json)
        cat <<ENDJSON
{
  "chromecast_stream": "$chromecast_status",
  "threadfin_streams": "$threadfin_status",
  "recordings": "$recording_status",
  "tivimate_recording": "$tivimate_rec",
  "restart_safe": $restart_safe,
  "restart_reason": "$restart_reason"
}
ENDJSON
        ;;
    --restart-ok)
        if [[ "$restart_safe" == "true" ]]; then
            exit 0
        else
            echo "$restart_reason" >&2
            exit 1
        fi
        ;;
    *)
        echo "=== IPTV Stream Status ==="
        echo ""

        # Chromecast / TiviMate
        case "$chromecast_status" in
            active:*)
                IFS=: read -r _ dst rate <<< "$chromecast_status"
                echo "Chromecast (TiviMate):  STREAMING to $dst @ $rate"
                echo "  -> TiviMate connects directly to provider; Threadfin restart is safe."
                ;;
            idle)
                echo "Chromecast (TiviMate):  idle (no active stream)"
                ;;
            no_data)
                echo "Chromecast (TiviMate):  unknown (could not reach router conntrack)"
                ;;
        esac

        # Threadfin
        case "$threadfin_status" in
            active:*)
                echo "Threadfin streams:      ACTIVE (${threadfin_status#active:})"
                echo "  -> DO NOT restart Threadfin — active Jellyfin Live TV session."
                ;;
            stale:*)
                echo "Threadfin streams:      STALE state (${threadfin_status#stale:})"
                echo "  -> Tuner counter stuck; restart will clear it."
                ;;
            idle)
                echo "Threadfin streams:      idle"
                ;;
        esac

        # Recordings
        case "$recording_status" in
            recording:*)
                echo "Recordings:             IN PROGRESS — DO NOT restart."
                ;;
            scheduled:*)
                IFS=: read -r _ timing count <<< "$recording_status"
                echo "Recordings:             upcoming ${timing} (${count})"
                if [[ "$chromecast_status" == active:* ]]; then
                    echo "  -> WARNING: TiviMate stream may block this recording (tuner limit = 1)."
                fi
                ;;
            none)
                echo "Recordings:             none active or upcoming (2h window)"
                ;;
            no_key)
                echo "Recordings:             unknown (no Jellyfin API key)"
                ;;
        esac

        # TiviMate file recording
        if [[ "$tivimate_rec" == "active" ]]; then
            echo "TiviMate recording:     ACTIVE (file being written)"
            echo "  -> DO NOT restart Threadfin (TiviMate may be writing via Threadfin tuner)."
        fi

        echo ""
        if [[ "$restart_safe" == "true" ]]; then
            echo "Threadfin restart: SAFE"
        else
            echo "Threadfin restart: BLOCKED — $restart_reason"
        fi
        ;;
esac
