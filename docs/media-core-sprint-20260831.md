# Media-Core health sprint — 2026-08-31

Health audit of the media-core ecosystem followed by a six-item change sprint. Claude Code
planned, briefed and inspected each item; agy (gemini-3.7-flash-high) executed them via
`agy-task.sh ... build`. Every item had a recorded pre-state and a back-out plan before it ran.

Baseline, per-item detail and back-out commands: `/root/agy-reports/sprint-baseline-20260831.md`.
Individual execution reports: `/root/agy-reports/20260831T*-p{1,2,3,4,6,7}*.md`.

## Why

CT 105 was I/O-saturated: `dm-9` (its 1 TB mount) at ~99.8% utilisation while moving only
~488 KB/s — seek-bound, not throughput-bound — on the shared `pve-data` thin pool that backs
every guest. Host CPU idle sat at 29.6% with two Jellyfin instances pegged and **no ffmpeg
running**, i.e. pure scan/metadata work rather than transcoding.

## What changed

| # | Change | Effect |
|---|---|---|
| P1 | Retired CT 111 `jellyfin-vod` (stopped, `onboot=0`, **disk preserved**) | removed a duplicate ~250k-item catalogue and ~1 core |
| P4 | Fixed the change-detection guard in `ppv-refresh.py` | ~23 of 24 daily full guide rebuilds eliminated |
| P2 | CT 105 Serilog `Debug`→`Information`; disabled Media Segment Scan, Generate Trickplay Images, Extract Chapter Images | log rate 1,547 → 0 lines/60 s |
| P3 | Moved 3 stale `jellyfin.db.bak-*` copies to the SATA SSD | 7.85 GB reclaimed off the NVMe thin pool |
| P6 | `Environment=HOME=/root` on `alert-responder.service`; added `epg_coverage_ratio` / `epg_real_channels` / `epg_total_channels` gauges | alert auto-diagnosis works again; coverage dilution now visible |
| P7 | vzdump `vmid` 102,105 → 102,105,107,108,112 | log-server, scraper and jellyfin-npvr now protected |

### Measured result

| Metric | Before | After |
|---|---|---|
| Host CPU idle | 29.6% | 87.3% |
| Load average (1 min) | 4.83 | 1.60 |
| PSI io `full avg300` | 26.57 | 2.33 |
| PSI io `some avg300` | 52.91 | 3.70 |
| Jellyfin log rate | ~1,547 lines/60 s | 0 |

## The P4 bug, specifically

`ppv-refresh.py` has always contained a guard intended to skip the expensive Jellyfin
"Refresh Guide" when no PPV event actually changed. **It had never once fired — 0 times in
1,137 runs.** `ET.iterparse` yields `elem.tail is None` for elements whose closing tag lands
on a 16/32 KB buffer boundary, so `ET.tostring()` omits the trailing newline for those,
while `xs.ppv_programmes()` always appends one. Wrapped in `all()`, a handful of boundary
elements out of 608 forced the result False every hour. Fix: `.strip()` both sides before
comparing (and in the "updated N/M" counter, which was likewise comparing rolling placeholder
timestamps and so was never trustworthy).

`epg.xml` is still rewritten and Threadfin's `update.xmltv` still fired **every** hour —
deliberately, so `epg_age_hours` stays fresh and the EPG-staleness rule cannot false-fire.
Only the Jellyfin refresh became conditional. PPV placeholder windows span 24 h and the
internal Refresh Guide runs daily, so maximum staleness never exceeds the placeholder window
— no guide gaps.

## Reviving CT 111

The disk (`vm-111-disk-0`, 180 GB) was deliberately preserved.

```bash
pct set 111 --onboot 1 && pct start 111
cp -a /root/bin/stack-monitor.py.pre-ct111-retire-20260831 /root/bin/stack-monitor.py
cp -a /usr/local/bin/dvr-dashboard.pre-ct111-retire-20260831 /usr/local/bin/dvr-dashboard
cp -a /srv/log-server/prometheus/prometheus.yml.pre-ct111-retire-20260831 \
      /srv/log-server/prometheus/prometheus.yml   # (on CT 107, then restart prometheus)
systemctl enable --now vod-sync-ct111.timer
systemctl restart stack-monitor dvr-dashboard
```

Note CT 111 is **not** in the vzdump job. Its catalogue duplicated CT 105's and its disk is
retained on the pool, so a backup would be redundant. If it is ever revived as a primary
server, add it.

## Findings that turned out to be wrong

Recorded because the reasoning errors are more reusable than the conclusions. Full write-ups
in `lessons-learned.md`.

* **"EPG coverage collapsed to 34.6%, ~800 channels blind."** False. All 1225/1225 channels
  carry programmes. 424 is the `real=` metric; the rest are PPV (608) and synth (193) by design.
* **"The CT 108 scraper's output is discarded."** False. `0/243` means *no additional
  uncovered* channels — those were already covered upstream. ESPN/ESPN2/ESPNU/ESPNews all
  carry guide data.
* **"Refresh Guide is on a runaway curve."** Overstated. `lineup-runbook.md` documents ~20 min
  as normal, and the "already Running" collision dates to 2026-07-09 at 3.6% of runs. The real
  defect was the P4 guard, which is a different problem with a much better fix.
* **"`xtream-sync.py` destroys artwork on every sync."** False — read off a stale proposed
  diff; the fix was already in both the repo and CT 105.
* **"Monitoring collects but cannot alert."** False. Grafana alerting, a webhook receiver and
  automated agy diagnosis have existed since 2026-07-21 / 2026-08-12. The real gap was
  narrower: coverage was alerted on an *absolute* count (`real < 380`), which lineup growth
  silently dilutes — hence the new ratio gauge.

## Still open

* **CT 105 holds both Live TV and the full VOD catalogue** (25,720 movies / 7,686 series /
  216,763 episodes; 139 GB of metadata on a ~40 MB `.strm` catalogue). This sprint removed the
  *duplicate* on CT 111 and the wasted scans, but the single-box concentration remains by
  owner's choice. Revisit only if CT 105 saturates again.
* **`process-queue.py` recording-loss risk** — staged at
  `/root/agy-reports/process-queue.py.proposed.diff` (2026-08-17), still unapplied. Newly
  queued recordings can be overwritten while comskip/ffmpeg runs. Not triaged this sprint.
* **The remaining chunks of `xtream-sync.py.proposed.diff`** (`mc_gen_` prefix rename,
  `start_chno` handling, alias country-stem matching, `_merge_one_source` exception handling).
  The exception-handling chunk is genuinely valuable — one failing external source currently
  aborts the whole EPG merge. Deferred deliberately: they change channel numbering and
  coverage classification and need their own before/after comparison.
* **Grafana admin password** is not the compose default, so alert rules cannot be managed via
  the provisioning API. New rules must be file-provisioned (see `grafana/`).
