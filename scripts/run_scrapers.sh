#!/bin/bash
set -u
cd /srv/scrapers
mkdir -p output logs

# Simple log rotation if run.log exceeds 200KB (found in review, 2026-08-17)
if [ -f logs/run.log ] && [ "$(stat -c%s logs/run.log 2>/dev/null || echo 0)" -gt 204800 ]; then
    mv logs/run.log logs/run.log.old
fi

for f in espn espn2 espnu espnews metv bally_sports rt_news hgtv hallmark_drama bally_arizona bally_prime_ticket bally_great_lakes; do
    tmp=$(mktemp)
    # `[ -s "$tmp" ]` alone was a real bug (found + verified in review,
    # 2026-08-17): a scraper that catches an internal error and does
    # `print("")` still exits 0 with a 1-byte (just "\n") non-empty file,
    # which passed this check and overwrote a genuinely good XML guide
    # with garbage -- "keeping last-known-good" in the log was a lie in
    # exactly that case. `grep -q "<programme"` requires the output to
    # actually contain real guide data before it's trusted, closing this
    # for all 12 scrapers at once rather than needing to audit each
    # one's own error path individually. `timeout 60` added too --
    # nothing here previously bounded how long one hung scraper could
    # block the rest of the loop.
    if timeout 60 python3 "$f.py" > "$tmp" 2>> "logs/$f.log" && [ -s "$tmp" ] && grep -q "<programme" "$tmp"; then
        chmod 644 "$tmp"
        mv "$tmp" "output/$f.xml"
        echo "$(date -u +%FT%TZ) $f: OK" >> logs/run.log
    else
        rm -f "$tmp"
        echo "$(date -u +%FT%TZ) $f: FAILED, keeping last-known-good" >> logs/run.log
    fi
done
