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

**An alert that fires nightly for an expected event will be muted, and the
real ones with it.** The host is on a power timer (off 22:24–04:57);
anything that would alarm on that outage must be suppressed.

**An alert that has never been observed firing is not monitoring.** Force
the condition and confirm delivery.

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

**Filename conventions are load-bearing.** NextPVR strips colons when
caching channel icons; a file written with the colon is silently ignored.

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
