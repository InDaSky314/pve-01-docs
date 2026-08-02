#!/bin/bash
# Run the remaining icon batches through agy, one at a time.
#
# Sequential on purpose: agy died twice today on long tasks, and running
# batches in parallel would make a partial failure hard to attribute. Batch
# size is 6 -- 12 produced cropped wordmarks and invented text.
#
# Each batch is checked for its expected files before moving on; a batch that
# produces nothing is recorded and skipped rather than aborting the run, so one
# bad batch does not cost the other 23.
set -uo pipefail

BATCHDIR=/root/agy-batches
KEEP=/root/agy-icons-keep     # batches are copied here immediately
LOG=/root/agy-icons-run.log
mkdir -p "$KEEP"
: > "$LOG"

for f in "$BATCHDIR"/fast-*.json "$BATCHDIR"/sports-*.json "$BATCHDIR"/affiliate-*.json; do
    slug=$(basename "$f" .json)
    want=$(python3 -c "import json;print(len(json.load(open('$f'))))")

    # Resumable: skip batches whose icons are already in the keep directory.
    # The 2026-08-02 run lost fast-01..17 when a later agy session cleared the
    # working directory, so only the missing ones need regenerating.
    have=$(python3 -c "
import json, os
items = json.load(open('$f'))
print(sum(os.path.exists('/root/agy-icons-keep/' + i['channel'] + '.png') for i in items))
")
    if [ "$have" = "$want" ]; then
        echo "$slug already complete ($have/$want), skipping" | tee -a "$LOG"
        continue
    fi

    python3 /root/mkprompt.py "$slug" >/dev/null

    /root/bin/agy-task.sh run "icon-$slug" build "@$BATCHDIR/$slug.md" \
        --model gemini-3.6-flash-high --timeout 20m >/dev/null 2>&1

    got=$(python3 - "$f" <<'PY'
import json, os, sys
items = json.load(open(sys.argv[1]))
print(sum(os.path.exists(f"/root/agy-icons/{i['channel']}.png") for i in items))
PY
)
    # Snapshot immediately. On the 2026-08-02 run every batch reported 6/6
    # while the working directory ended up with 46 of ~148 files: something in
    # a later agy session cleared /root/agy-icons, destroying fast-01..17.
    # Per-batch counts looked perfect throughout, so only the total caught it.
    cp -a /root/agy-icons/*.png "$KEEP"/ 2>/dev/null
    kept=$(ls "$KEEP"/*.png 2>/dev/null | wc -l)
    echo "$slug: $got/$want (kept total: $kept)" | tee -a "$LOG"
done

echo "--- done ---" | tee -a "$LOG"
ls /root/agy-icons/*.png 2>/dev/null | wc -l | xargs echo "total icons generated:" | tee -a "$LOG"
