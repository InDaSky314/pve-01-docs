#!/bin/sh
# Jellyfin DVR post-processing hook — runs INSIDE the jellyfin container
# when a recording is finalized. Deliberately does almost nothing: it only
# appends the finished recording's (container-side) path to a queue file.
# The actual commercial detection/cutting runs on CT105 via
# comskip-postprocess.timer, fully outside this container — so this hook
# can never slow down or destabilize Jellyfin itself.
#
# Path inside container:  /media/recordings/.postprocess/on-recording-finished.sh
# Path on CT105 host:     /srv/media-core/media/recordings/.postprocess/on-recording-finished.sh
QDIR=/media/recordings/.postprocess
LOG="$QDIR/hook.log"
echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) hook fired: $1" >> "$LOG"
[ -n "$1" ] || exit 0
echo "$1" >> "$QDIR/queue"
exit 0
