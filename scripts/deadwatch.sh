#!/bin/bash
# Observe the "dead but uncited" channels across the day, without ever taking
# the household's only IPTV stream.
#
# The account allows ONE concurrent stream. A probe consumes it, so probing
# while someone is watching does not just risk a bad reading -- it breaks the
# real thing. check-iptv-stream.sh asks the router for active streams from the
# Chromecast/TiviMate, plus Threadfin's own stream state and Jellyfin's timers.
#
# Rules, both from lessons-learned:
#   * re-checked between every slice, not once at the start -- a viewer can
#     begin at any point in a 28-channel run
#   * if the check cannot be made, that counts as NOT safe. "Unmeasurable"
#     must never read as "go ahead". Doing nothing costs one missed sample.
set -u
GUARD=/root/bin/check-iptv-stream.sh
BATCH=4
TOTAL=$(/usr/sbin/pct exec 105 -- sh -c 'wc -l < /root/recheck-names.txt' 2>/dev/null | tr -d '\r ')
[ -z "$TOTAL" ] && { echo "cannot read the channel list — skipping"; exit 0; }

# check-iptv-stream.sh only knows about CT 105: router conntrack for the
# Chromecast, Threadfin's streams, Jellyfin's timers. It is blind to CT 112,
# where NextPVR serves Live TV -- verified 2026-08-04, the guard reported
# "idle" while the owner was watching through NextPVR. That is the same class
# of bug lessons-learned already records: automation written for one backend
# breaks when a second appears.
#
# NextPVR writes a growing live-*.ts timeshift buffer while anything is
# watching, so growth over a few seconds is the signal.
nextpvr_busy() {
    local a b
    a=$(/usr/sbin/pct exec 112 -- sh -c \
        'cat /srv/shared-recordings/nextpvr/live-*.ts 2>/dev/null | wc -c' 2>/dev/null)
    [ -z "$a" ] && return 0        # cannot measure -> treat as busy
    sleep 6
    b=$(/usr/sbin/pct exec 112 -- sh -c \
        'cat /srv/shared-recordings/nextpvr/live-*.ts 2>/dev/null | wc -c' 2>/dev/null)
    [ -z "$b" ] && return 0
    [ "$a" != "$b" ]               # grew -> busy
}

safe() {
    [ -x "$GUARD" ] || return 1
    "$GUARD" --restart-ok >/dev/null 2>&1 || return 1
    ! nextpvr_busy
}

if ! safe; then
    echo "$(date -Is) SKIPPED — a stream is active, or the check could not be made"
    "$GUARD" --json 2>/dev/null | head -8
    exit 0
fi

/usr/sbin/pct exec 105 -- sh -c ': > /tmp/recheck/result.tsv' 2>/dev/null
off=0; done_n=0
while [ "$off" -lt "$TOTAL" ]; do
    if ! safe; then
        echo "$(date -Is) STOPPED after $done_n channels — a stream started"
        break
    fi
    /usr/sbin/pct exec 105 -- bash /root/recheck-slice.sh "$off" "$BATCH" >/dev/null 2>&1
    off=$((off + BATCH)); done_n=$off
done

ts=$(date -Is)
/usr/sbin/pct pull 105 /tmp/recheck/result.tsv /tmp/rc.tsv 2>/dev/null
[ -s /tmp/rc.tsv ] && awk -v t="$ts" -F'\t' '{print t"\t"$1"\t"$2}' /tmp/rc.tsv >> /root/deadwatch.tsv
echo "$ts sampled $(wc -l < /tmp/rc.tsv 2>/dev/null || echo 0) channels"
