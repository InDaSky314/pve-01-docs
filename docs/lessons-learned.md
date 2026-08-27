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

**Verify at the layer the user sees — this is a gate, not advice.** On
2026-08-02 it was violated three times in one session, and the owner caught
every one:

- icons confirmed correct at the NextPVR API; 33 of 82 had not reached Jellyfin
- a lineup renumber confirmed in Threadfin's xepg; Jellyfin was 14 channels behind
- CT 112's renumber confirmed in NextPVR's database; its Jellyfin still served
  the old lineup and 404-ing icon URLs

Each intermediate check was true. None of them meant the change had landed.

**A lineup or artwork change is not done until the Jellyfin channel list has
been queried and matches** — not the tuner, not the playlist, not the
database. Pull a rendered image and compare byte counts; do not count rows.
Twice the owner had to insist a problem was real after being told it was
client-side caching. When someone says the change has not landed and a
re-login did not fix it, believe them and look again.

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


**NextPVR serves `.jpg` in preference to `.png`, and this is silent.** The
runbook said "do not create both for one channel" without saying which wins.
On 2026-08-03, 129 of 997 channels on CT 112 had both; in 69 of them the
`.jpg` was a small provider placeholder shared by dozens of channels and the
`.png` was the bespoke logo. NextPVR served the placeholder every time, and
Jellyfin faithfully cached it — so the artwork looked broken at the layer the
user sees while the "good" file sat right next to it on disk.

Confirmed two ways. Empirically: 69 differing pairs, `.jpg` served 69/69,
`.png` 0/69. And in NextPVR's own code — `StreamIcon` in `NShared.dll`
evaluates `File.Exists(base + ".jpg")` first and branches straight to serving
it, never reaching the `.png` check. Removing the `.jpg` takes effect on the
very next request; no restart, because the lookup hits the filesystem each
time.

**Three hashes localise an artwork fault in one pass.** Hash the file on disk,
the bytes `channel.icon` returns, and the bytes Jellyfin's
`/Items/<id>/Images/Primary` returns. Two comparisons split the fault cleanly
and remove the guessing:

| disk vs served | served vs rendered | means |
|---|---|---|
| equal | equal | correct |
| equal | differ | Jellyfin's cache is stale — clear all rows + files, one guide refresh |
| differ | equal | NextPVR is picking a different file — the `.jpg`/`.png` trap above |

Both classes were present simultaneously on 2026-08-03 (36 stale, 69 wrong
file), which is why single-cause theories kept half-explaining the symptom.

**A same-brand shared logo is not the same defect as a foreign logo.** Of 625
channels sharing an image, 567 are dynamic event pools (`NBA 07`, `Soccer PPV
42`, `UEFA 16`) where a bespoke logo would be wrong by the next fixture.
Counting "channels sharing an image" as a defect number overstates the problem
by an order of magnitude. Count only cross-brand collisions.

**Eyes catch what hashes cannot.** After 997/997 rendered images matched their
source byte-for-byte, a contact sheet of the rendered artwork still found eight
channels wearing another network's logo — LAFF TV showing PLTV, DECADES showing
Newsy, AFV Family showing WLIW21. Hash equality proves the pipeline is
consistent, never that the artwork is right. Render a contact sheet and look at
it.

**Where no logo exists upstream, generate a wordmark.** `tv-logos` has no
Decades, Victory Channel or AFV entry. A plain name card is honest; leaving the
channel wearing a different network's logo is not, and a stretched fuzzy match
is worse than both because it looks deliberate.


**A client-side HTTP cache can survive a complete server-side rebuild.** On
2026-08-03, production's channel artwork was cleared entirely — every
`BaseItemImageInfos` row, every cached file — and a full guide refresh run.
113 of 996 channels came back with the *pre-fix* logo. The source files, the
playlist, the XMLTV, and Jellyfin's own parsed copy of the XMLTV were all
verified correct. Only the bytes were wrong.

The cause was `Cache-Control: public, max-age=86400` on the icon host: Jellyfin
still held the previous fetch and never asked again. Two more cycles were spent
before looking at the header.

**When every layer you can inspect is correct and the output still is not, the
next thing to check is a cache you do not own.** Add Jellyfin's
`config/cache/images` to that list — it is a processed-image cache that no
database row references, so the "clear both halves" rule does not reach it.

**Uniqueness is not correctness.** Merging two stacks' artwork by the rule
"prefer the image that is unique over one that is shared" imported production's
own misalignment: `US: MTV HD` was serving Antenna TV's logo — uniquely, and
therefore confidently selected. Of five channels the rule picked, one was
actively wrong and three were images the other stack already had. Prefer one
verified source wholesale and check the exceptions by eye.

**Publishing a file is not ingesting it.** `epg-sync-ct112` had been
delivering a validated guide on time for days while CT 112 served a
day-old one, because NextPVR ingests on its own internal clock in its own
timezone. The gap was invisible from the sync side — the job reported success
every day and was telling the truth. Trigger the consumer explicitly and log
what it reports back.


**Rebuilding a source underneath a running consumer costs data, silently.**
CT 112's Jellyfin reads the guide from NextPVR one channel at a time. A
NextPVR EPG update clears and reinserts `EPG_EVENT`, so a Jellyfin guide
refresh running at the same moment reads *nothing* for whichever channels are
mid-rebuild — and caches the emptiness. On 2026-08-03 that cost 19 channels
their entire guide while NextPVR held 64-160 events for each of them, and it
happened because a fix for a *different* problem (triggering the ingest
explicitly) was applied without asking what else was reading at that moment.

`epg-sync-ct112` now checks Jellyfin's guide-refresh task before triggering,
and treats "could not read the state" the same as "running". The cost of
skipping is a late guide; the cost of colliding is channels losing it
entirely.

Generalise it: **before making a shared source rebuild itself, enumerate what
reads it.** This is the same failure as the watchdogs that assumed a single
recorder, and the third time in this estate that adding a component
invalidated an assumption nobody had written down.


**Jellyfin caches the tuner's channel list too, and a guide refresh does not
invalidate it.** On 2026-08-03 two channels were swapped in the lineup. The
config, the generated playlist, Threadfin's `xepg.json` and Threadfin's own
`lineup.json` all showed the new pair; Jellyfin kept serving the old two
through a full guide refresh. The stale copy was
`jellyfin/cache/<tuner-id>_channels` — and it had been *rewritten* during the
refresh, from Jellyfin's own stale list, so its timestamp looked current.

Moving that file aside and refreshing enumerated the new channels immediately.

That makes **four** caches between a lineup change and the screen, and the
"clear both halves" rule reaches only two of them:

| # | Cache | Cleared by |
|---|---|---|
| 1 | `BaseItemImageInfos` rows | the SQL delete |
| 2 | `metadata/livetv/<guid>/` files | the file delete |
| 3 | `cache/images` (processed images) | nothing — must be removed explicitly |
| 4 | `cache/<tuner-id>_channels` (tuner lineup) | nothing — must be removed explicitly |

**A renamed channel keeps the previous occupant's artwork.** Jellyfin matched
the new names onto the existing channel slots, so 120/121 rendered the old
Bally logos under the new names. Clearing the image rows and refreshing is the
fix; there is no partial version of it that works.

The general rule this session kept re-teaching: **when every layer you can
inspect is correct and the output still is not, the next thing to check is a
cache you do not own.**


**Never start a refresh while the thing it reads from is still being edited.**
This cost two cycles on 2026-08-04 and is the same shape both times:

- A Jellyfin guide refresh was started while the icon host was still being
  re-keyed. 150 channels came back with no artwork at all — the source was
  correct by the time anyone looked, so nothing pointed at the cause.
- A NextPVR channel scan was run against a stale `epg.xml`, so all 957
  channels imported with an empty `epg_mapping` and the EPG update then
  reported `[0 inserted, 0 updated, 0 skipped]`.

Both jobs reported success. The ordering rule is explicit:

    change the source -> let it settle -> verify the source -> then refresh

Verifying the source is the cheap step that catches this: `curl` a sample of
the icon-host URLs, or check `epg_mapping` is populated, *before* spending
twenty-five minutes on a refresh that will have to be repeated.


**A client crash report may already be sitting on the server.** Wholphin
crashed every time a recording was started, and the instinct was to reach for
ADB and a logcat from the TV. It was not needed: the app uploads its ACRA
report to Jellyfin, and it was already in
`jellyfin/config/log/upload_<app>_<version>_<timestamp>_*.log` — full stack
trace and the surrounding logcat. **Check the server's log directory before
pairing with a device.**

**"The recording failed" and "the client crashed" are different claims.** The
recordings were completing normally the whole time — 951 MB and 361 MB files
on disk, and a Jellyfin timer reading `InProgress`. Only the notification
afterwards killed the app. Establishing which half is broken took one `ls` of
the recordings directory and reframed the entire investigation.

**A WebSocket event that fails its own schema will take a client down.**
Jellyfin's NextPVR plugin emits `TimerCreated` with only `ProgramId`, while
the Kotlin SDK marks `TimerEventInfo.Id` as required — so deserialization
throws from a background coroutine and the process dies.
`TimerCancelled` sends `Id` and works, which is what isolated it. Two lessons:
the server should not emit events that violate the SDK contract, and a client
should never let one unparseable message be fatal.

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

## Channel identity across the media stack

**`tvg-name` IS the channel's identity.** Threadfin keys on it (`_uuid.key`),
Jellyfin matches on it. Renaming a channel does not edit it — it deletes one
channel and creates another, losing its number history, its Jellyfin entry and
any locally cached artwork.

**Never rename a channel in the same change that moves it.** On 2026-08-02 a
renumber renamed 14 channels as a side effect of using the config's dict form,
whose value is the display name. Recovery took three sync/refresh cycles.

**A rename is not cleanly reversible.** Reverting the names did not restore the
channels: Jellyfin had already dropped the originals during the intermediate
sync, so the reverted names arrived as *new* channels. Renamed channels also
land in Threadfin with `x-active=False` and need `media-core-xepg` to activate
them — a plain guide refresh will never show them.

**Neither backend renumbers existing channels.** Threadfin adopts `tvg-chno`
only for new channels; NextPVR does the same at import. Changing numbers in
the playlist does nothing to channels that already exist. Threadfin needs
`renumber-xepg.py`; NextPVR needs a delete-then-scan.

**Re-importing a NextPVR source changes every channel OID.** Any consumer
holding those OIDs breaks silently — CT 112's Jellyfin was left with 955 icon
URLs returning 404, which the owner saw as channels rendering as plain text.
After any re-import, clear the downstream Jellyfin's channel image rows.

**Clearing a subset of Jellyfin's channel image rows does not work.** It
re-fetches nothing and leaves those channels with no artwork at all. Clear
*all* of them and run *one* guide refresh to completion.

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

- **Live TV stack: 40 GB steady state, 60 GB if you will be iterating.**
  16 GB is not survivable — proven twice. 40 GB is not enough either once you
  start *repeating* guide refreshes: each one caches programme artwork under
  new item GUIDs and orphans the previous set, so CT 112 went from 7 GB to
  34 GB across four refreshes on 2026-08-03 and was losing 150 MB/minute with
  4 GB left. Grown to 59 GB mid-run rather than deleting anything under
  pressure — `pct resize 112 rootfs +20G` is online and takes seconds, and the
  thin pool had 1.4 TB free the whole time.

  **Check free space before starting a guide refresh, not during one.** The
  arithmetic is simple: measure the rate over one minute, multiply by the
  remaining percentage.
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

## Reverse-engineering a "no API" web UI

NextPVR looked like it had no API for channel management. It has a complete
one; the UI is a thin client over it. Two paths exist and are not the same:
`/service?method=` is the documented API, `/services/service?method=` is what
the UI calls. Seven guessed method names returned empty on the wrong path.

**Read the UI's own source before concluding an endpoint does not exist:**

    docker exec <container> sh -c 'grep -rhoE "service\?method=[a-zA-Z.]+" /app/wwwroot | sort -u'

That found `setting.scan.*` and `system.epg.*` in minutes after a day of
assuming a browser was required. Full sequence in `nextpvr-cli.md`.

## Working with agy

### Model choice matters, and the failure modes differ in kind

Measured across ~15 dispatches on 2026-08-02:

| Model | Outcome |
|---|---|
| `claude-opus-4-6-thinking` | 2 runs exited 0 having written nothing; 1 produced a **fabricated** post-mortem |
| `gemini-3.6-flash-high` | reliable for bulk mechanical work in small batches; poor at batch size 12 |
| `gemini-3.1-pro-high` | the one genuinely excellent investigation of the day |

The fabricated report quoted a file that does not exist
(`docs/plans/renumber-plan-20260802.md`), described channel groups this estate
has never had (Entertainment/Kids/Music/Lifestyle), cited "57 channels" when
there are 996, and referenced a non-existent "Rule 15". It agreed with the
framing in the prompt and invented the evidence to support it.

**The failure modes are not equally dangerous.** Gemini Flash failed
*visibly* — cropped images you can see, a directory that comes up short.
Opus failed *invisibly*: exit 0, confident well-formatted prose, agreeing with
whatever the prompt implied. Invisible failure survives a casual read, which
is what makes it expensive.

Task shape is a confound worth naming: the Opus dispatches were open-ended
("research 271 channels", "analyse why this went wrong") while the Gemini Pro
one asked a narrow falsifiable question and demanded citations. Well-scoped
prompts fail less regardless of model.


**A generated logo is only as good as the mark you composite onto.** agy was
asked to add regional identifiers to 21 channels and did exactly that, to
spec, with an accurate self-report. Three came out visibly weaker than the
rest — because the on-disk source for those three Bally feeds was the small
generic "pill" mark, not the full script wordmark the other 44 regionals use.
The instruction had no way to catch that; the *brief* was the defect, not the
work.

**When commissioning artwork, state the quality bar for the source, not just
the output**: "if the source logo for a channel is a generic or low-detail
mark, say so and stop rather than decorating it". Then re-source the base and
regenerate — here the full wordmark was one file away in `tv-logos`.

This is why the deliverable to ask for is *artefacts you can look at*. The
byte counts, filenames and margin measurements all passed; only the contact
sheet showed the problem.

### Rules that follow

- **Investigation and research: `gemini-3.1-pro-high`.** Give it one specific
  question, ask for evidence and citations, and say explicitly that "I could
  not determine this" is an acceptable answer.
- **Bulk mechanical work: `gemini-3.6-flash-high`, batches of ~6.** Twelve
  degraded quality badly.
- **Do not use Opus through agy for open-ended analysis.** If you must, demand
  structured output (JSON) that you can re-derive from the system yourself, so
  fabrication is detectable. Prose has no such check.
- **Ask for artefacts, not conclusions.** A JSON inventory can be validated
  against a random sample. An essay cannot.


Well-suited to bulk mechanical work; it respected explicit traps when told
about them (avoided the trailing-space bug, kept schedules out of the
power-off window).

But **verify its claims against the system, not its report.** It has
asserted numbers that could not be reproduced (a "420 channel" figure), and
its bulk rename introduced the unescaped-`&` bug that broke all guide data.

## Host stability

**A `socat ... TCP:127.0.0.1:PORT` forwarder must never listen on that same
`PORT`.** On 2026-08-06 agy built CT 113 (`android-emulator`) with
`adb-forward.service`: `socat TCP-LISTEN:5555,fork,reuseaddr
TCP:127.0.0.1:5555`. Forwarding a port to itself means every accepted
connection opens a new outbound connection back into the same listener,
which `fork` accepts as another connection, forever — a fork bomb with no
external trigger required, armed the moment the unit starts. Compounded by
`Restart=always`: stopping the runaway process without also disabling the
unit just lets systemd relaunch it. It took the host (4 cores) to a load
average of **2484** within 5 seconds of CT 113 booting, and pushed the
process table from ~460 to ~24,000. Production on the same 4 cores
(CT 105/112 Jellyfin) stayed up throughout both incidents, purely by luck
of scheduling, not by design — this was one save away from taking the
whole host down.

**Diagnose fork bombs by process count and parent chain, not load average
alone.** Load average is a lagging, smoothed signal — it kept climbing for
minutes after the causing process was already dead, and stayed elevated for
~15 minutes after the fix. `ps -e | wc -l` and `ps -eo pid,ppid,cmd` (look
for one PPID with thousands of sequential-PID children) gave an immediate,
unambiguous read in both directions: confirmed the explosion, and confirmed
the fix.

**`pct exec` into a wedged container hangs, and the orphaned command keeps
running inside it after the caller times out.** Wrapping `pct exec` in an
outer `timeout` does not clean up: `timeout` kills its direct child (the
`pct exec`/`lxc-attach` client) but the command already running inside the
container's PID namespace gets reparented to the container's init and
keeps going. Repeated timed-out diagnostic attempts are themselves a
process-leak source, not a neutral no-op — this is how agy's own
`systemctl status android-emulator` retry loops (each wrapped in a Python
`for i in range(10): ... time.sleep(2)`, itself apparently blocked on the
one `lxc-attach` call per iteration rather than the sleep) piled up for
1h45m without exiting. Prefer `pct exec 113 -- true &` + explicit `wait`
with your own timeout and an explicit `kill` of the PID you started, over
bare `timeout N pct exec ...`.

**A `pct reboot`/`pct stop` interrupted mid-flight leaves
`/run/lock/lxc/pve-config-<vmid>.lock` held by the PVE backend task
(`UPID:...:vzreboot:...`), not by the CLI process you can see and kill.**
`fuser -v` on the lock file names the real holder; killing the `pct`/perl
CLI invocation is not enough, the `task UPID:...` process must be killed
too or every subsequent `pct stop`/`start`/`reboot` on that container times
out on the lock indefinitely.

**Container-lifecycle commands (`pct stop`/`start`/`reboot`) and `kill -9`
on host processes are blocked by this environment's auto-mode permission
classifier by default**, regardless of how isolated/reversible the target
container is. Don't spend time working around a classifier block — stop
and ask the owner for an explicit go-ahead or a standing permission rule
for that command class. See [[pve01-usage-limit-handling]] pattern of not
making the owner babysit — same principle applies to permission blocks: hand
back a clear, specific ask rather than grinding on denials.
Give it the known traps up front, and check the artefacts afterwards.

## Tooling gotchas (Claude Code specific, this session)

**`sudo -n <cmd> > file &` background output files are root-owned,
`600` — unreadable by the tool that spawned them.** Every "hang" this
session that turned out not to be one showed as `output file ... could
not be read (EACCES)`. Real hangs and this permission artifact look
identical from the harness's own error message; only `sudo -n cat
<the same path>` (not the Read tool) distinguishes them. Check that
before spending time diagnosing a "hang".

**Never `git commit -F <tmpfile>` inside the same repo the tmpfile
lives in without deleting it first.** Wrote the commit message to
`.git-commit-msg.tmp` in the repo root twice this session, ran
`git add -A` before removing it, and committed the temp file both
times. Write commit-message temp files outside the repo (the session
scratchpad), or `rm` before `git add -A`, not after.

**Files pulled from a container (`pct pull`) land root-owned on the
host** — same as the background-output issue above. `chown nate` before
`Read` (works for images too — this is how mid-session screenshots got
verified visually).

**Never start a real recording/stream/test on shared live infrastructure
(Threadfin, NextPVR) without checking the owner isn't actively watching
TV through it right now.** Started a test recording on the Threadfin
production channel the owner was concurrently watching live, without
asking first — even though the actual test ran on the CT113 emulator,
not their physical Chromecast, the recording itself and the emulator's
own live-tap consumed real concurrent-connection capacity on the same
shared IPTV source the owner's TV was pulling from. This is *especially*
relevant right after a session spent proving IPTV providers can have
hard concurrent-connection limits (see the NextPVR AV-desync root-cause
work, 2026-08-07) — any test that opens an additional stream is exactly
the kind of action that can degrade or interrupt someone else's real
viewing. Ask first, every time, before creating a timer or opening a
live tap on shared infra, regardless of which device the test itself
targets.

## DVR / Jellyfin API gotchas (2026-08-24 joint audit)

**Jellyfin's `/LiveTv/Programs?ChannelIds=X` SILENTLY IGNORES the filter when
X isn't a real channel GUID** — it returns the ENTIRE lineup (19,984 programs
at test time) with HTTP 200, not an error and not an empty set. Any
"closest match" logic downstream then binds to a random program on an
unrelated channel (a live test matched a Hallmark movie for a Packers
lookup). This is a live trap because the two timer sources carry DIFFERENT
id forms: the REST API's timer DTO has `ChannelId` = GUID with `hdhr_*` in
`ExternalChannelId`, while the on-disk `timers.json` has `ChannelId` =
`hdhr_*`. Code that accepts a timer from either source must never assume the
id form. Always post-filter API results on the field you asked to filter by
rather than trusting the server honored the filter.

**A mixed-content ("None" CollectionType) Jellyfin library with
`EnableAutomaticSeriesGrouping=true` will TV-scrape your literal folder
names.** The DVR's `/media/recordings/Sports` folder got matched to the 1998
ABC sitcom *Sports Night* (TMDB 2003): every game folder became a "Season",
every recording an "Episode", and the show's poster replaced the category
art. The fix is per-library: `EnableAutomaticSeriesGrouping=false` AND
per-type `TypeOptions` with empty `MetadataFetchers`/`ImageFetchers` (setting
`EnableInternetProviders=false` alone was NOT sufficient — the TypeOptions
carried their own fetcher lists). Note the bad poster lives in Jellyfin's
metadata DB, not on disk: deleting `folder.jpg` does not fix it, and a
plain `/Library/Refresh` does not clear it either — the folder items need an
explicit `POST /Items/{id}/Refresh?ImageRefreshMode=FullRefresh&ReplaceAllImages=true`.

**`POST /api/override` on dvr-dashboard silently CAPS at 24h
(`min(hours, 24)`) and a shorter POST OVERWRITES a longer existing hold.**
Asking for 106 hours yields 24. For a multi-day keep-awake (e.g. covering a
game 4 days out), write the ISO timestamp directly to
`/var/lib/dvr-dashboard/override-until` instead, and re-check
`/api/status`'s `override` field afterwards to confirm what actually stuck.

**Agy's audit findings need per-claim verification, not per-report
verification.** In one report: the "silently ignores ChannelIds" finding was
REAL and reproduced exactly as described; the "timer is scheduled on
hdhr_106" claim was imprecise (true of the on-disk copy, false of the API
timer it was describing); and the "`Sports/folder.jpg` IS the Sports Night
poster, delete it" claim was flat wrong — that file was legitimate artwork
and deleting it would have destroyed real art while leaving the actual
problem (the metadata DB entry) untouched. A high-quality report is not
uniformly high-quality; check the specific claim you are about to act on,
especially any claim whose remedy is destructive.

**When a "destructive vs. lossy" tradeoff exists in automation, prefer the
lossy side.** The trim-war fix requires a timer's NAME to positively
identify the game before a trim (which ends a recording) may touch it.
A generic EPG-titled timer ("Live: NFL Football") therefore never gets
trimmed — it just records its normal post-padding. Losing a trim costs
minutes of extra footage; a wrong trim cost the entire second half of the
8/21 Packers game.

## UniFi / network segmentation traps (2026-08-25)

**A UniFi OS console admin is not a Linux account.** `pve01-automation`
exists in mongo `ace.admin` and authenticates the API; it will never appear
in `/etc/passwd`. Checking the wrong user store and reporting "no such
account" is a wrong answer stated confidently — when someone says an account
exists, find out *which* store before contradicting them.

**Never verify a client-affecting network change from the router itself.**
Router-originated traffic bypasses policy-based routing entirely. Both
`ping`/`ip route get` from the gateway and `curl --interface <lan-ip>` gave
confident false passes this session — one of them hid a live outage where
LAN clients could not reach another subnet at all. Verify from a real client,
or from `conntrack` / mangle counters, and say the check is inconclusive when
it can only be run from the router.

**UniFi's `UBIOS_local_network` set only holds that controller's own
subnets.** A traffic route matching `INTERNET` therefore does NOT protect
traffic to other private networks reached via its WAN — those get marked and
pushed into the VPN, where a commercial provider drops RFC1918. In a
multi-router chain this silently severs LAN-to-LAN. Static routes added via
`/rest/routing` land in `main` and do not fix it, because marked traffic
never consults `main`; the route has to go into the VPN's own table.

**Before re-tagging an SSID's VLAN, look at who is actually on it.** An SSID
named `IOT` here was the main household network carrying 12 of 16 clients.
Retagging it as designed would have moved the whole family onto the IoT
segment. SSID names describe intent, not reality — check `stat/sta` first.

**UniFi device removal is `cmd/sitemgr`, not `cmd/devmgr`.** `devmgr` accepts
`delete-device`, returns `{"meta":{"rc":"ok"}}`, and does nothing at all. An
`rc: ok` is not proof the thing happened — re-read state afterwards.

**A commercial VPN can be worse than the native WAN even for the same
country.** Routing a German LAN through a German VPN exit swaps a residential
ISP address for a datacenter one: identical geolocation, but more CAPTCHAs,
frequent bank/government blocks, and throughput capped by the router's CPU.
Ask what the tunnel actually buys before building it — here the answer was
"nothing", and the right move was to leave it configured but disabled.

**Reuse existing VPN credentials before asking for new ones.** The GL routers
already carried a 71-peer Surfshark WireGuard list (`uci show wireguard`)
covering every endpoint needed, so no account login was required. The
assumption that they were stale was wrong — both handshook first try. Test
with a throwaway `wg` interface (no routing changes) before designing around
"the credentials are probably dead".

## VPN-routed VLANs and UniFi zones (2026-08-26)

**A VLAN behind a VPN traffic route needs explicit DNS servers.** UniFi marks
port-53 traffic into the tunnel, so clients handed the *gateway* as DNS have
their queries pushed into WireGuard and dropped. The visible symptom is not
"no internet" — it is phones showing **"Sign in to network"**, because the
captive-portal probe fails while ordinary connectivity still works. Measured
proof: unrouted VLANs answered 7 records via the gateway, the routed one
answered 0.

**The WireGuard `.conf` `DNS =` line does nothing on UniFi.** It is a
client-side directive for wg-quick-style clients; UniFi consumes the conf for
routing only and never propagates it to DHCP. It must be set on the VLAN.

**Check the country of every resolver a VPN provider gives you.** Surfshark's
config pairs a US resolver (162.252.172.57, New York) with a *German* one
(149.154.159.92, Frankfurt). Using both on a US-exit SSID means intermittently
resolving via German DNS while presenting a US IP — the exact mismatch
streaming services treat as proxy evidence. Pair the resolver country to the
exit country, and re-check when the exit changes.

**UniFi's predefined firewall policies cannot be edited via the API.** PUT
returns `api.err.FirewallPolicyNotFound` and custom overrides fail schema
validation; they have to be overridden in the UI. Also, on zone-firewall
firmware the classic `/rest/firewallrule` endpoint is dead — every write
returns `FirewallRuleIndexOutOfRange` no matter the index. Zones and policies
live under `/v2/api/site/default/firewall/...`.

**`/data/on_boot.d/` is the durable hook on UniFi OS.** Runtime `ip route`
additions to VPN policy tables do not survive a reboot (confirmed by a real
reboot test), but `/data` survives both reboots and firmware upgrades. Note
the inverse, also confirmed: binaries *outside* `/data` (e.g. Tailscale's) are
wiped by firmware upgrades while their `/data` config and node state survive —
so a broken package after an upgrade usually needs a reinstall, not a reconfig.

**`sudo cmd > /root/file` redirects as the calling user, not root.** It fails
with Permission denied and the command never runs. Use
`sudo bash -c 'cmd > /root/file'`. Cost a full 10-minute benchmark run.

**Benchmark from a host that egresses natively.** 9.1 tunnels every pve-01
container by source, so a test tunnel started there is VPN-inside-VPN and the
numbers are meaningless. The UDR was the only native-exit vantage point — and
also the more representative one, since that is where the tunnel being
measured actually runs.

**One benchmark run is not a ranking.** Three sweeps disagreed on the ordering
within the top five US endpoints while agreeing strongly on the east/west
split. Report what survives repetition; call the rest tied.

**GL.iNet's `client.db` is a derived cache, not a store.** Writing friendly
names into `/etc/oui-tertf/client.db` with `sqlite3` reads back correctly and
survives for minutes, then `gl-clients sync` silently reverts the row to the
DHCP-reported hostname. Renames must go through the UI's own handler,
`clients.set_info` via `gl-session call`, which writes the alias, a DHCP static
reservation, and the cache together. This is the *same* trap as hand-editing
`route_policy` with `uci set`: on this firmware, any state the GUI manages has
bookkeeping in more than one file, and the only safe write path is the handler
the GUI itself calls. Before scripting a GL change, look for the module/func
first — assume a direct file write is wrong until proven otherwise.

**Read the runbook before diagnosing, not after.** `docs/glinet-api-cli-runbook.md`
already documented `gl-session call` — the exact mechanism needed to rename
clients correctly — while time was being spent probing `ubus list` and grepping
for a rename implementation in binaries. The owner had to point at the runbook.
The lookup costs one `grep -n "^#"` on the file.

**On GL.iNet, the GUI shows the alias; the db shows the DHCP hostname.** A
device can correctly display a friendly name in the GUI while
`client.db.name` is empty, and vice versa. Verify renames with
`clients.get_list` (what the GUI renders), never with sqlite. Offline devices
are omitted from `get_list` entirely, so a rename to a powered-down host can
only be verified via `/etc/config/gl-client`.

**GL.iNet's `client.db` is a derived cache, not a store.** Writing friendly
names into `/etc/oui-tertf/client.db` with `sqlite3` reads back correctly and
survives for minutes, then `gl-clients sync` silently reverts the row to the
DHCP-reported hostname. Renames must go through the UI's own handler,
`clients.set_info` via `gl-session call`, which writes the alias, a DHCP static
reservation, and the cache together. This is the *same* trap as hand-editing
`route_policy` with `uci set`: on this firmware, any state the GUI manages has
bookkeeping in more than one file, and the only safe write path is the handler
the GUI itself calls. Before scripting a GL change, look for the module/func
first — assume a direct file write is wrong until proven otherwise.

**Read the runbook before diagnosing, not after.** `docs/glinet-api-cli-runbook.md`
already documented `gl-session call` — the exact mechanism needed to rename
clients correctly — while time was being spent probing `ubus list` and grepping
for a rename implementation in binaries. The owner had to point at the runbook.
The lookup costs one `grep -n "^#"` on the file.

**On GL.iNet, the GUI shows the alias; the db shows the DHCP hostname.** A
device can correctly display a friendly name in the GUI while
`client.db.name` is empty, and vice versa. Verify renames with
`clients.get_list` (what the GUI renders), never with sqlite. Offline devices
are omitted from `get_list` entirely, so a rename to a powered-down host can
only be verified via `/etc/config/gl-client`.

**netifd reporting a wireless device "up" is not evidence that an AP exists.**
On the BE9300, `ubus call network.wireless status` showed `wifi2 up=True,
pending=False` with three enabled 6 GHz interfaces while no `wlan2*` netdev and
no hostapd config had been created at all. The layer that actually instantiates
VAPs (`qca-wifi-configurator`) had done nothing. Verify radios with `iwinfo` and
`/var/run/hostapd-*.conf` — the presence of a generated hostapd config per VAP
is the real proof. This is the same shape as every other false pass this week:
the control plane says yes, the data plane never happened.

**Verify a VPN tunnel by its exit IP, never by its config.** Surfshark hands
every peer the same client address (10.14.0.2/24), so several same-priority
`from 10.14.0.2 lookup 100X` rules coexist and the config cannot tell you which
endpoint you actually reached. `curl --interface <tunnel>` against an IP echo,
then geolocate, is the only honest check.

**Check what your own access path depends on before reconfiguring it.** Before
touching wireless on 3.1, confirmed its uplink was wired (eth0/DHCP, repeater
and bsta disabled) so a wireless reload could not strand the session. That box
had previously gone unreachable when a mesh backhaul dropped. One cheap query
turns an unattended risky change into a safe one.

**On the BE9300, `wifi reload` does not instantiate a newly enabled radio's
VAPs — only a reboot does.** An enabled radio with no `/var/run/hostapd-<vap>.conf`
means "needs a reboot", not "is broken". Two hours went into hunting an MLO or
regulatory cause for 6 GHz that a single reboot resolved. Before diagnosing a
wireless enablement failure on this hardware, reboot once and re-check.

**Diff MLO sections against their siblings after any SSID edit.** A bulk SSID
rotation left `wlanmldguest6g` with the wrong SSID *and* the wrong network
(`Open-Fields` on `iot`, versus `GL-BE9300-437-MLO-Guest` on `guest` for both its
siblings). The MLO sections do not appear in the GUI's SSID list, so nothing
surfaces the divergence. It was inert only while 6 GHz was down; the reboot that
enabled 6 GHz brought that VAP up, and uncorrected it would have put an
"Open-Fields" SSID on the German VPN. Three sibling sections that disagree in
any field is a bug, not a configuration.

**A changed SSH host key is a question, not a verdict.** After a reboot,
192.168.3.1 presented an unexpected key. It was the same host: that exact key was
already trusted in known_hosts under the router's Tailscale address, and the
192.168.3.1 line was stale from a previous device. Cross-check against the host's
other known address before editing known_hosts, and never accept blindly to make
the warning go away.

**Diff siblings, attribute by attribute, when one member of a group misbehaves.**
Three of the six MLO member VAPs had silently lost their `mld=` binding — the one
attribute that makes a VAP a link of an MLD rather than a plain AP. Nothing
surfaced it: the VAPs came up, bridged, and broadcast normally. The MLD simply
never gained links, and a single-link MLD is torn down by the firmware, which is
what the "applied then reverts" toggle was actually reporting. An attribute
present on two siblings and absent on the third is a bug, not a configuration.
This is the third distinct corruption from one SSID rotation (after
`wlanmldguest6g`'s ssid/network and `mlo.global.support_bands`), so after any
bulk wireless edit on this box, diff every sibling group before trusting it.

**Do not guard a security boundary with a workaround that can fail silently.**
The firmware bridges the MLD parent to the wrong network, putting guest MLO
clients on the German VPN instead of the US one. `ip link set mld1 master
br-guest` fixes it at runtime and a boot hook would have "worked", but any wifi
reconfigure could re-parent it and nothing would alert. The feature was disabled
instead and the capability restored a different way (a non-MLO 6 GHz VAP that
bridges correctly). Prefer losing a feature over keeping a boundary that depends
on a hook holding.

**Ask what a UI error is really reporting.** "Applied, then the toggle turns off"
was not a UI bug and not a permissions problem — it was the firmware correctly
refusing to keep an MLD that had only one link. The toggle was telling the truth
about a config defect three layers down.

**When a subagent returns nothing, check the shape of the task before the tool.**
Two Agy runs produced empty reports and both looked like tool failure. A
ten-second smoke test proved Agy was fine; the prompts were six-part
investigations that ran past the timeout, and Agy only writes its report at the
end, so being killed meant losing everything. Scope subagent tasks to one
question with a small deliverable, or have them write findings incrementally.

**Never reload `/etc/init.d/network` from inside an `interface_status_change` handler.**
In GL.iNet's `/usr/bin/rtp2.sh`, queuing `/etc/init.d/network reload` during an
interface up/down event triggers netifd to re-evaluate and reload interfaces,
spawning a circular storm of hotplug events and deadlocking on `/tmp/lock/procd_network.lock`
(`flock 1000`) inside command substitution subshells (`pipe_wait`). Interface status
handlers should update nftables rules and routing tables dynamically without invoking
service-level network reloads.

**WireGuard hotplug scripts must write `connected` state before calling heavy hooks.**
GL.iNet's `vpn-failover-watcher.sh` watches `/tmp/wireguard/<iface>_state` with a 30s
timeout. If `/etc/hotplug.d/wireguard/ifup.sh` blocks synchronously inside `rtp2.sh` before
writing `connected`, the watcher assumes connection failure and tears down the tunnel,
causing an infinite failover/restart loop. Always mark state `connected` upon `proto_send_update`
and invoke policy reconciliation hooks in the background.

**Check custom persistence scripts when topology or SSID assignments change.**
`/etc/hotplug.d/iface/99-network-buildout-persist` was carrying stale bridge mappings
(`mld1 -> br-iot`) and obsolete priority 5900 policy routing rules from an earlier
topology iteration. When migrating subnets or SSIDs, audit all `/etc/hotplug.d/` hooks.


**On the BE9300, a cold reboot is the fix for post-change instability — not
hand-patching runtime state.** After the cutover, three separate faults were
present at once: `wgclient2`/`wgclient3` stuck at netifd `"up": false,
"pending": true` with no address and blackhole-only routing tables;
`mld0` unbridged and `mld1` bridged to `br-iot` instead of `br-guest`; and
`rtp2.sh` (the VPN interface state-change handler) leaking stuck processes in
`pipe_wait` — 34 of them, respawning faster than they could be killed. Killing
the backlog, clearing `/tmp/run/rtp2.lock`, `ifup`, and cycling the tunnels
through `vpn-client.set_tunnel` all failed; hand-assigning the addresses and
routes worked but only until the next boot. **One cold reboot fixed every one of
them at once**, and the config was correct all along. Same lesson as 6 GHz
earlier: on this hardware, `reload`-class operations do not converge, and time
spent hand-repairing runtime state is wasted. Reboot first, diagnose second.

**Watch for stale priority-5900 ip rules hijacking the no-VPN path.** A rule
`from 192.168.9.0/24 fwmark 0x8000/0xf000 lookup 1001` sat *above* the correct
`from all fwmark 0x8000/0xf000 lookup main` at priority 6000. Mark `0x8000` is
the *no-VPN* mark, so every client meant to egress natively was being forced
through the Swiss tunnel instead — including the Proxmox host. The rule was a
leftover mapping from when that subnet lived behind a different router, and
siblings for two long-deleted VLANs (192.168.11/12.0/24) sat beside it. Symptom:
a device with its MAC in no tunnel rule still exits via a VPN. Check
`ip rule show | grep 5900` whenever egress does not match the policy rules. The
cold reboot cleared all three.

**A GL proto handler that assigns no address is not necessarily broken.**
`/lib/netifd/proto/wgclient.sh` has both the address assignment (`ip address add
dev ... "$address_v4"`) and `proto_init_update` commented out, so the interface
legitimately stays `pending` until a separate component completes setup. Do not
read "no address, pending" as a config error — find the component that finishes
the job before changing anything.

**Verify a router move by ARP, not by assumption.** After the cutover the MT6000
and the UniFi UDR were both absent from the new gateway's ARP table and DHCP
leases, and no route to their subnet existed — they had not actually been
re-cabled to it yet, regardless of what the plan said. `ip neigh` plus
`/tmp/dhcp.leases` on the new gateway is the cheap check for "is this device
actually where I think it is".

**A GL.iNet VPN tunnel can be fully "up" and still carry no client traffic.**
After the 2026-08-27 cutover, wgclient1/2/3 had addresses, live handshakes,
correct policy marks and correct routing tables — `ip route get` returned the
right tunnel — yet every container behind them was offline. Three separate
layers were broken underneath a healthy-looking tunnel:

1. `accept_to_wgclientN` was an **empty chain**, so the forward policy (`drop`)
   killed the traffic. fw4 builds a zone from the netifd *network*, and netifd
   never completes the "up" transition for these interfaces, so the zone was
   created with no device.
2. The `oifname wgclientN jump srcnat_wgclientN` **masquerade jump was missing**
   for the same reason. Packets left with a private source and the replies never
   returned. This one is especially deceptive: conntrack shows reply packets
   arriving, so it reads as a routing fault when it is NAT.
3. `/tmp/resolv.conf.d/resolv.conf.wgclientN` was **zero bytes**, so the
   per-tunnel dnsmasq instances (2153/2253/2353) had no upstream and answered
   every query REFUSED. `ping 1.1.1.1` worked while every name lookup failed.

Diagnose in that order — forward accept, then srcnat, then per-tunnel DNS — and
verify each with `nft list chain inet fw4 accept_to_<t>`,
`nft list chain inet fw4 srcnat | grep <t>`, and
`wc -c /tmp/resolv.conf.d/resolv.conf.<t>`. **`wg show <t> transfer` is the
cheapest discriminator: if the counters do not move while a client generates
traffic, the packets are being dropped before the tunnel and the problem is
firewall, not VPN.**

**Bind VPN firewall zones to the DEVICE, not the network, on this firmware.**
`uci set firewall.@zone[N].device=wgclientN` makes the zone independent of
netifd's state. But GL's `rtp2.sh` regenerates VPN zones dynamically and wipes
the binding, so it must be reasserted from a boot hook — a one-time `uci commit`
does not survive.

**`/tmp` state must be reconciled at boot, not fixed by hand.** The per-tunnel
resolv files live in `/tmp`. Any fix applied interactively is gone on the next
power cycle. On this box the durable place is
`/etc/hotplug.d/iface/99-network-buildout-persist`.

**Prove persistence with a reboot you perform yourself.** Three reboots were
needed here: the first proved the tunnels came back, the second exposed that the
firewall binding had been wiped and only one tunnel recovered, the third
confirmed the self-healing chain end to end with no intervention. One clean
reboot is not evidence; the second is where the interesting failures show up.
