# Lessons learned — Media-Core

Durable, reusable findings. Dated entries live in the per-incident docs;
this file holds only what should change how the *next* piece of work is
done. Add to it; do not let it grow into a narrative.

---

## Diagnosis

**Do not infer a mechanism from a metric without confirming the mechanism
ran.** On 2026-08-01 three separate EPG theories were stated confidently
and all were wrong, because the numbers being reasoned about came from a
job that was inserting **zero** rows. `[0 inserted, 0 updated, 0 skipped]`
was the tell, sitting in plain sight. Reading the log *during* the
operation solved in minutes what hours of counting had not.

**An empty result is not a negative result.** Twice the same error:
- `sqlite3` was not installed in CT 107; the query returned nothing and it
  was reported as "no alert rules exist". There were five.
- `/LiveTv/TunerHosts` returns empty even when tuners are configured. It
  briefly looked like production's tuner had been destroyed.

Check stderr, check the tool exists, and confirm the absence means what
you think.

**Verify at the layer the user sees.** Channel icons were confirmed
correct at the NextPVR API (`HTTP 200`, right byte count) while the user
still saw the old ones, because Jellyfin caches separately. A green check
one layer down proves nothing about the rendered result.

**Check your own probe before believing a total failure.** `channel.icon`
returned 404 for every channel tested, which read as "NextPVR is serving
nothing" — the probe was passing channel *numbers* where the API wants
`CHANNEL.oid`. With real OIDs every icon returned 200. A result that says
*everything* is broken usually means the measurement is broken.

**Byte counts catch what status codes miss.** Two channels returned
`HTTP 200` for their icon — at exactly 5,586 bytes, the size of the
placeholder. The request succeeded; the content was wrong.

**Empty test data validates nothing.** Recording detection "passed" for
days while parsing was completely broken, because there were no recordings
to parse. The bug surfaced the moment a real recording existed.

---

## Destructive-action discipline

**Look at the target before deleting.** Before removing 54 NextPVR
recordings, enumeration showed all 54 were from the same night — nothing
older existed. Before touching Jellyfin's VOD entries, matching on *path*
rather than *name* avoided deleting 259 legitimate library episodes that
merely shared a title.

**Prefer the application's own API over the filesystem.** Deleting
recordings through `recording.delete` removed the database rows too; `rm`
would have left orphaned records pointing at missing files.

**Deleting a cached file without its database row is worse than deleting
neither.** `rm -rf jellyfin/config/metadata/*` on CT 112 removed every channel
poster but left a `BaseItemImageInfos` row for all 997 channels still pointing
at them. Jellyfin reads the row, concludes it already has the image, and never
re-fetches — 997 channels blank, permanently, surviving restarts and guide
refreshes. Verified 2026-08-02: 997 of 997 rows pointed at missing files.

Before clearing any application's cache, ask what *else* records that the cache
is populated. If you cannot clear both halves, clear neither.

**Move files, do not delete them.** The NextPVR plugin was *moved* to
`plugins-disabled-<date>/`, databases copied to `.bak-<purpose>` before
edits. Every destructive step this session was reversible.

**Clearing a cache is not always safe.** NextPVR never re-fetches channel
icons — it populates them once at channel import. Clearing the cache left
zero icons and required restoring from backup.

---

## Watchdogs and automation

**Never take destructive action on a measurement you could not make.**
`pre-recording-guard` returned `0` bytes both for "stalled" and for "could
not find the file". With a second recorder present it could not see, it
cancelled and recreated healthy recordings once a minute — 18 fragments
from one 30-minute programme, plus a Threadfin restart each time that broke
an unrelated recording on the other DVR.

Now: unmeasurable returns `None`, and the guard skips. *Doing nothing costs
one missed detection; acting on a false positive costs the recording.*

**Automation written for one backend breaks when a second appears.** Every
watchdog here assumed a single recorder. Adding NextPVR silently invalidated
that assumption. When adding a component, audit what already exists that
assumes it is alone.

This recurred verbatim. On 2026-08-02 both `dvr-clean-shutdown` and
`dvr-power-reminder` were still hardcoded to `CT = "105"`, so CT 112 — by then
the *only* stack that had ever recorded successfully — was invisible to both:
no shutdown reprieve, and no reminder to leave the mains timer on. Both now
iterate a `STACKS` list.

**Prove a scope fix in both directions.** With nothing scheduled, the fixed and
broken versions print identical output. The fix was only demonstrable by
scheduling a real recording on CT 112 and watching the old script say
"shutting down cleanly" while the new one refused. Absence of a complaint is
not evidence of coverage.

**Distinguish "nothing there" from "could not look".** When extending a guard
across containers, a missing `timers.json` means that stack has never scheduled
a recording — an answer. A container that is running but unreachable is a
failed measurement. Conflating them either wedges the shutdown forever or
silently skips a recorder.

**An alert that fires nightly for an expected event will be muted, and the
real ones with it.** The host is on a power timer (off 22:24–04:57);
anything that would alarm on that outage must be suppressed.

**An alert that has never been observed firing is not monitoring.** Force
the condition and confirm delivery.

On 2026-08-02 that turned out to be literal: Grafana's *entire* Alertmanager
config had been failing to parse since roughly 2026-08-01, so no rule could
deliver anything. Rules evaluated normally the whole time and every instance
sat at `Normal`, which looks exactly like a healthy quiet system. The only
outward sign was a UI banner nobody had reason to open.

Two separate invalid constructs, either fatal on its own:

- a mute timing of `22:20-05:10`, which **wraps midnight**. Alertmanager time
  ranges must satisfy start < end within one day; a wrapping window has to be
  split into `22:20-24:00` plus `00:00-05:10`.
- `mute_time_intervals` set on the **root route**, which Grafana forbids
  outright. It belongs on a child route.

**Both got in because the config was written straight into `grafana.db` by
SQL, bypassing the API that would have rejected them.** The fingerprint was
`configuration_hash` still matching the *previous* config while the content
differed, and no `alert_configuration_history` row containing the mute timing
at all. Repair was to restore the last history row and re-apply the change
through the provisioning API, where it was validated.

**Write alerting config through the API, never into the database.** The API
is the only thing that validates it, and an invalid config does not degrade —
it takes the whole notification pipeline down silently.

**Alertmanager mute timings are UTC unless `location` is set.** The container
also runs UTC, so both readings agreed here: `22:20-05:10` meant 00:20-07:10
CEST — missing the actual 22:24 power cut and muting 04:57-07:10 when the
system is up. Always set `location` explicitly.

---

## Configuration traps seen more than once

**Trailing whitespace in paths.** NextPVR stored `/config/epg.xml ` and
then failed `File.Exists` silently, reporting `[0 inserted]`. The same
class of bug broke Jellyfin logins (`"family "` from Android's keyboard).
**Check paths and credentials with `cat -A`.**

**Config values are not behaviour.** Twice a setting looked right and did
nothing:
- `Direct play TS` wrote its preference correctly but `createDeviceProfile`
  never read the parameter — the toggle was inert.
- `EPGUpdateTime 12:35` fires on NextPVR's *internal* clock, which had its
  own timezone, so it ran at 14:35.

Trace a setting to its point of use, and verify timing by observation.

**Identifiers derived from content change when the content changes.**
Removing a trailing space from an XMLTV path changed NextPVR's EPG source
id *and* every per-channel mapping id beneath it.

**Filename conventions are load-bearing.** NextPVR strips *filesystem-illegal
characters* when caching channel icons — a file written with the character
still in it is silently ignored. Confirmed for `:`, `/` and `|`. This was
learned as "colons are stripped", then cost the last 2 channels of 997 because
the rule is broader than that. Assume any character illegal in a filename is
stripped. Full procedure in `channel-icons-runbook.md`.

**Unescaped `&` in machine-generated XML.** Ten channel names containing a
raw ampersand made their stored XML blobs invalid and aborted every EPG
update for all 997 channels. After any bulk rename, verify the XML still
parses.

---

## Media-stack specifics

**Guide artwork is measured in gigabytes.** ~33,000 programmes produced
7.6 GB on Jellyfin plus 1.6 GB on NextPVR, filling a 16 GB disk until
Jellyfin refused to start (`Required: 2GiB`). Production carries 130 GB of
metadata. Size disks accordingly; 16 GB is not enough for a Live TV stack.

### Sizing a Jellyfin stack container

Measured on this host, 2026-08-02, with the workloads that actually broke
things. Metadata — not media — is what fills these disks.

| Stack | Disk | Used | Drives the number |
|---|---|---|---|
| CT 110 `jellyfin-live` | 40 GB | 8.6 GB | Live TV guide artwork |
| CT 112 `jellyfin-npvr` | 40 GB | 7.0 GB | Live TV guide artwork + recordings buffer |
| CT 111 `jellyfin-vod` | 79 GB | 53 GB | 236k VOD items → 1.3 GB database alone |
| CT 107 `log-server` | 32 GB | 5.7 GB | Loki/Prometheus retention |

**Floors, not targets:**

- **Live TV stack: 40 GB.** 16 GB is not survivable — proven twice.
- **VOD stack: 80 GB per ~236k items.** CT 111 sits at 70% after a full scan.
- **RAM: 4 GB is the floor and it is tight.** `jellyfin-vod` held 2.8 GB of
  its 4 GB cap during the library scan. Under-sizing here does not fail
  cleanly, it just thrashes.

**Running out of disk does not fail politely.** On CT 111 the disk hit 100%,
**Docker itself died and took both containers with it**, and nothing noticed
for about an hour. Jellyfin refuses to start below 2 GiB free, which at least
announces itself; Docker dying does not.

Size for the *scan*, not the steady state — peak metadata during a first full
scan is well above what the library settles at.

**Grow the disk before a first scan, not during one.** Both CT 111 and CT 112
hit their ceiling mid-scan, and the recovery is slower than the provisioning
would have been.

**Jellyfin metadata cannot be shared between instances.** It is keyed by
per-instance item GUIDs (`metadata/livetv/<guid>/poster.jpg`), so the same
programme lands under a different path on every server.

**Provider logo sets are largely placeholders.** 503 channels share one
image on *production* too. Do not treat "production has it figured out" as
given — measure before copying.

**Wireless debugging does not survive a reboot on Android 11+.** With the
TV on a power timer it will be off every morning. ADB is a diagnostic tool
here, not a delivery mechanism — use the self-update release channel.

**Release tags must be plain semver.** A describe-shaped tag
(`v1.0.3-34-g693c0e3c`) makes `git describe` nest inside itself and
produces a version string the updater cannot parse.

---

## Working with agy

Well-suited to bulk mechanical work; it respected explicit traps when told
about them (avoided the trailing-space bug, kept schedules out of the
power-off window).

But **verify its claims against the system, not its report.** It has
asserted numbers that could not be reproduced (a "420 channel" figure), and
its bulk rename introduced the unescaped-`&` bug that broke all guide data.
Give it the known traps up front, and check the artefacts afterwards.
