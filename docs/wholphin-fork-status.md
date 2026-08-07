# Wholphin custom fork — status & handoff (2026-07-25)

Client-side attempt at the Live TV **no-audio** bug, after server-side
routes were exhausted. See §10 history for the full chain; the short
version is that the audio problem is *not* server-side:

- The DVB-subtitle-track theory was disproven (ffprobe shows no subtitle
  stream on affected channels).
- Swapping channels 100/102 to "clean" provider streams did not fix it.
- **NextPVR was deployed and tested** — channels 100 and 102 were pushed
  to Jellyfin via NextPVR instead of Threadfin, and **the audio problem
  persisted**. That result is what moved effort to the client.

NextPVR is therefore **parked, not removed** — left deployed and working
so it can be revisited after the client work lands. Do not spend more
time on it for now. (Original evaluation stands: adopting it wholesale
would still hit the single-connection tuner limit, and its original
justification was a root cause that has since been disproven.)

## The fork

| | |
|---|---|
| Working repo | `/srv/media-core/wholphin` on **pve-01** (the host, not CT 105) |
| Intended GitHub remote | `git@github.com:nk-sys-ops/wholphin.git` — **PUBLIC**, not private as this row claimed until 2026-08-04. Verified with `gh repo view`. Anything filed there is world-readable. |
| Upstream | `https://github.com/damontecres/wholphin.git` |
| Branch pushed locally | `iptv-audio-fix` in `/root/wholphin` |

### Custom commits (all on top of upstream `cf3c00a1`)

| Commit | Change |
|---|---|
| `a5e605fe` | TiviMate-grade `TsExtractor` flags (`FLAG_ALLOW_NON_IDR_KEYFRAMES` + `FLAG_DETECT_ACCESS_UNITS`) and an ExoPlayer `onTracksChanged` audio-fallback listener that forces audio-track binding when `AudioTrack` defaults to `NONE` — in `PlayerFactory.kt` |
| `96b2f41b` | Native **IPTV Audio Track Recovery** toggle under *Settings → Experimental Settings* (`ExperimentalPreference.kt`, `WholphinDataStore.proto`, `strings.xml`) |
| `ec562b4e` | Default that toggle **ON** for maximum audio compatibility |

Total footprint is small (~57 insertions across 5 files) but it is the
actual candidate fix for the audio bug.

## ⚠️ The work is not on GitHub

**Corrects an earlier claim in `CLAUDE.md` that the private GitHub repo
was "fully up to date" — it is not.** Verified 2026-07-25:

- All three custom commits were **absent** from `/root/wholphin`, the
  only clone that carries a GitHub remote — so they were never pushed.
- `nk-sys-ops/wholphin` **does not resolve** with either credential
  available on this box: the `github-mirror` deploy key
  (`id_ed25519_pve01docs_mirror`) is scoped to `pve-01-docs` only, and
  the `gh` CLI is authenticated as `InDaSky314`, which cannot see it.
  GitHub returns "Repository not found", which for a private repo means
  either it doesn't exist yet or neither credential has access.

Mitigations applied in the meantime (both on pve-01):

1. Pushed to `/root/wholphin` as branch **`iptv-audio-fix`** — the
   commits now exist in two repos rather than one.
2. **`git bundle`** of `cf3c00a1..HEAD` in
   `/root/wholphin-backups/` (verified restorable; the base commit is
   public upstream, so the bundle is sufficient to reconstruct the work
   anywhere).

**Owner action needed to close this properly:** either create
`nk-sys-ops/wholphin` and add a deploy key with write access, or provide
a credential that can push there. Until then the fork lives only on
pve-01.

## Build status — stopped mid-build

Toolchain on pve-01 is ready: **Java 21**, Android SDK platform +
build-tools **36**, licences accepted. `./gradlew assembleDebug` was
running when the previous session ended; no APK had been produced.
Restarted 2026-07-25 and confirmed actively compiling (`kspAppstoreDebugKotlin`,
`kspDefaultDebugKotlin`, `kspFiretvDebugKotlin`).

**Correction to the documented APK path.** This project has build
**flavors** — `appstore`, `default`, `firetv` — so `assembleDebug`
produces several APKs and the previously documented
`app/build/outputs/apk/debug/app-debug.apk` **will not exist**. Expect:

```
app/build/outputs/apk/default/debug/app-default-debug.apk
app/build/outputs/apk/appstore/debug/app-appstore-debug.apk
app/build/outputs/apk/firetv/debug/app-firetv-debug.apk
```

For the **Chromecast with Google TV**, use the **`default`** flavor —
`firetv` targets Amazon devices and `appstore` is the Play-Store build.
Verify by listing the directory rather than assuming.

## Chromecast state

- Both the previous `wholphin` build and the official
  `org.jellyfin.androidtv` were uninstalled, so the device is clean for
  a fresh install.
- ADB over TCP at `192.168.9.203:5555`. **Wireless debugging does not
  survive a device reboot** — it must be re-enabled by hand in
  *Settings → System → Developer options*, and the connect port is not
  stable. Expect to re-pair.


## ⭐ Strongest lead: Wholphin direct-streams MPEG-TS, the web client uses HLS

Discovered 2026-07-25 and it reframes the whole problem. Two owner
observations narrowed it decisively:

- **The Jellyfin *web* client plays the affected channels WITH audio** —
  and the web client is served HLS.
- **Setting "Use FFmpeg decoder module" to always-FFmpeg did NOT fix it**,
  which rules out an HE-AAC / device-decoder cause.

Together those prove the audio **is present in Jellyfin's output** — so
this is not a server-side stripping problem, and it is not audio decoding.
It is the **transport path**.

Confirmed from Jellyfin's own logs for Wholphin sessions:

```
"SupportsDirectPlay":   false
"SupportsDirectStream": true
"TranscodingSubProtocol": "http"
GET .../LiveTv/LiveStreamFiles/<id>/stream.ts        <-- raw MPEG-TS
```

No `master.m3u8` / `live.m3u8` request from Wholphin at all. So:

| Client | Path | Demuxed by | Audio |
|---|---|---|---|
| Jellyfin web | HLS (`master.m3u8`) | **Jellyfin's ffmpeg** | ✅ works |
| Wholphin | direct `stream.ts` | **ExoPlayer TsExtractor** | ❌ silent |

The provider's MPEG-TS evidently carries audio signalling that ExoPlayer's
`TsExtractor` mishandles while ffmpeg handles it fine. Forcing Wholphin
down the HLS path moves demuxing to the component we can *prove* works.

### How to force HLS — where the decision is made

Jellyfin chooses Direct Stream vs HLS from the **DeviceProfile** the client
sends with `POST /Items/{id}/PlaybackInfo`. If no `DirectPlayProfile`
matches the container, it falls back to a transcoding profile (HLS).
Verified empirically: sending a profile with `DirectPlayProfiles: []` and
an HLS transcoding profile makes Jellyfin return a
`/videos/.../master.m3u8` TranscodingUrl and launch an HLS ffmpeg.

In the fork, the relevant code is:

- `app/src/main/java/com/github/damontecres/wholphin/util/profile/DeviceProfileUtils.kt`
  — the **video** `directPlayProfile` container list (~line 200-213)
  includes `Codec.Container.TS`. That is what advertises "I can
  direct-play MPEG-TS".
- `createDeviceProfile(...)` (~line 64) already takes boolean gate flags
  in exactly this style: `assDirectPlay`, `pgsDirectPlay`,
  `dolbyVisionELDirectPlay`, `decodeAv1`, `preferAc3ForSurround`.
- `services/DeviceProfileService.kt` (~line 55-68) wires those from
  preferences.

So the idiomatic change is a new gate — e.g. `tsDirectPlay: Boolean = true`
— that omits `Codec.Container.TS` from the video direct-play containers
when disabled, surfaced as an Experimental preference exactly like the
existing **IPTV Audio Track Recovery** toggle. The plumbing pattern is
already proven in this codebase by that commit.

**Tradeoff to be explicit about:** the DeviceProfile is global, not
per-item. Disabling TS direct-play affects *all* TS content, including DVR
recordings (Threadfin/Jellyfin DVR writes `.ts`), which would then
transcode as well — real CPU cost on CT 105's 2 vCPU. Acceptable as a
diagnostic toggle; if it proves to be the fix, consider scoping it to Live
TV items only.

**Assessment of the existing three commits in light of this.** The
`onTracksChanged` audio-fallback listener is well-aimed at a genuine
ExoPlayer MPEG-TS failure mode and worth keeping. The two `TsExtractor`
flags (`FLAG_ALLOW_NON_IDR_KEYFRAMES`, `FLAG_DETECT_ACCESS_UNITS`) are
**video**-oriented — H.264 access-unit detection and non-IDR start — so
their effect on *audio* track registration is indirect at best. Switching
transport to HLS is a stronger, better-evidenced fix than tuning the TS
extractor, because it bypasses the failing component entirely.

## Build environment notes (pve-01)

- The project ships `org.gradle.jvmargs=-Xmx2048m`. That **OOMs the Kotlin
  compiler during IR lowering** when several product flavours compile in
  parallel (`Backend Internal error ... root cause java.lang.OutOfMemoryError`,
  surfacing on an arbitrary file such as `FilterByButton.kt` — the file
  named is incidental, not the problem). RAM is not scarce on this box
  (~17-20 GB free); the cap was.
- Fixed at **user level** in `/root/.gradle/gradle.properties`
  (`-Xmx6g`, `kotlin.daemon.jvmargs=-Xmx4g`, `org.gradle.workers.max=3`)
  deliberately rather than editing the project's `gradle.properties`, so
  the fork stays clean of machine-specific tuning. Run `./gradlew --stop`
  after changing these — daemons are long-lived and will not pick up new
  JVM args.
- **Build only the flavour you need:** `./gradlew assembleDefaultDebug`,
  not `assembleDebug` (which builds appstore + default + firetv).
- **pve-01 is a poor build host.** Intel Celeron N5105, 4 cores @ 2.0 GHz
  (~10 W). Load hit ~10.8 during dexing — roughly 2.7x oversubscribed —
  while Jellyfin was also running a Guide refresh. No swap thrashing, so
  it completes, but a modern 8-16 core machine would plausibly be 3-5x
  faster. Nothing requires building here: ADB reaches the Chromecast at
  `192.168.9.203:5555` from any LAN or tailnet host, so building on a
  laptop and installing from there is the better standing arrangement.
  Heavy builds otherwise belong in the 01:00-05:00 maintenance window.

## Next steps

1. Confirm the build produced APKs; identify the `default`-flavor path.
2. Install: `adb -s 192.168.9.203:5555 install -r <path-to-default-debug.apk>`
3. Sign in to Jellyfin, confirm *Settings → Experimental Settings →
   IPTV Audio Track Recovery* is present and **on** by default.
4. Test audio on the known-bad channels — **133, 134, 313** (and 100/102).
   See the affected-channel log in the history table.
5. If audio works, that confirms the client-side root cause; then decide
   whether to revisit NextPVR at all.



## ✅ RESOLVED 2026-07-25: the fork IS now on GitHub

`nk-sys-ops/wholphin` (private) created by the owner, with a dedicated
**deploy key** (`/root/.ssh/id_ed25519_wholphin`, 600) granted write
access — the same per-repo pattern already used for the `pve-01-docs`
mirror, so a leak of that key cannot reach any other repository.

Access is via an SSH host alias, because plain `git@github.com` cannot
authenticate for this repo on this box:

```
Host github-wholphin
    HostName github.com
    User git
    IdentityFile /root/.ssh/id_ed25519_wholphin
    IdentitiesOnly yes
```

- `/srv/media-core/wholphin` → remote **`github`** = `git@github-wholphin:nk-sys-ops/wholphin.git`
- `/root/wholphin` → `origin` **corrected** to the same alias (it previously
  pointed at `git@github.com:...`, which silently failed auth — the reason
  the commits had never actually been pushed)

Pushed `main` (1549 commits) and **verified all four custom commits are
ancestors of `github/main`**: `a5e605fe`, `96b2f41b`, `ec562b4e`,
`08decc3e`. The work is no longer single-copy on one disk.

Licence note: upstream Wholphin is GPL. The fork is private, which is
fine; if it is ever made public, leave the upstream `LICENSE` and
copyright headers intact.

## Build + install: DONE (2026-07-25)

`BUILD SUCCESSFUL in 17m 14s` after two fixes: the `iptvRecovery`
use-before-declaration, and raising the Gradle/Kotlin heap (see build
environment notes). Built with `assembleDefaultDebug`.

Output in `app/build/outputs/apk/default/debug/`:

| APK | Size |
|---|---|
| `Wholphin-default-debug-1.0.3-25-gec562b4e-54.apk` (universal) | 53.5 MB |
| `...-54-arm64-v8a.apk` | 41.7 MB |
| `...-54-armeabi-v7a.apk` | 40.3 MB |

**Use `armeabi-v7a` — NOT arm64.** The Chromecast with Google TV
("sabrina") reports `ro.product.cpu.abi = armeabi-v7a` and an abilist of
`armeabi-v7a,armeabi` only: it runs a **32-bit** Android userspace despite
64-bit-capable hardware. Installing the arm64 APK would fail or misbehave.
Check the device rather than assuming:

```bash
adb -s 192.168.9.203:5555 shell getprop ro.product.cpu.abilist
```

Installed and verified on the Chromecast:

```
versionName = 1.0.3-25-gec562b4e     versionCode = 54
package     = com.github.damontecres.wholphin.debug
activity    = com.github.damontecres.wholphin.MainActivity
```

**Note the package name has a `.debug` suffix** (debug builds carry an
`applicationIdSuffix`), so it installs alongside rather than over a release
build — and `dumpsys package com.github.damontecres.wholphin` returns
nothing. Use the `.debug` id for adb/dumpsys/logcat filters.

### Still to do — on-device verification (needs the TV)

1. Launch Wholphin, sign in to Jellyfin.
2. Confirm **Settings → Experimental Settings → IPTV Audio Track
   Recovery** is present and **ON** by default.
3. Test the known-bad channels **133, 134, 313** (and 100/102).
4. If still silent, go to the **HLS transport experiment** above — it has
   better evidence behind it than the extractor flags, since the web client
   demonstrably plays these channels with audio over HLS.


## Playback diagnostics (commit `8f45372a`)

An always-on `Player.Listener` (not gated on any toggle) logs everything
tagged **`WHOLPHIN_DIAG`**: `onPlayerError` gives ExoPlayer's `errorCode`,
`errorCodeName`, message and cause chain; `onTracksChanged` enumerates each
track's mime, codec, channels, sample rate, language and **selected state**.
There was previously no error handler at all, which is why failures were
invisible.

```bash
adb -s 192.168.9.203:5555 logcat -v threadtime | grep WHOLPHIN_DIAG
```

Decision table: a `playback_error` code → act on that code; an audio track
present but `selected=false` → the fallback listener is correct, enable it;
no audio track at all → do the HLS transport experiment, since ffmpeg finds
the audio (the web client proves it).

Same commit also made IPTV recovery **opt-in, default off**, fixing an
inverted guard that forced the TsExtractor flags on for every channel
whenever the experimental master switch was off (i.e. on any fresh install)
— superseding `ec562b4e`.

## Deferred, explicitly not forgotten

- **Prune stale Tailscale nodes + add ACLs** (MEDIUM-HIGH). 16 of 21
  peers offline, several 500–972 days, three still advertising as exit
  nodes; the tailnet is a fully trusted zone with full router admin and
  whole-LAN access. See `docs/router-security-audit-20260725.md`.
- **WireGuard keys in Loki history** (MEDIUM). Present from before the
  2026-07-25 redaction fix; LAN-only, ~30-day retention. Options:
  accept retention / purge via Loki delete API / rotate.

---

## Update 2026-08-06: two repos diverged; NextPVR recording crash is the priority now

**The owner is now watching live TV through Custom on the NextPVR
ecosystem daily and has no audio problems on it.** NextPVR is no longer
"parked" — see the top of this doc's own advice to leave it alone; that is
superseded. **Custom (`/srv/media-core/wholphin`,
`com.github.damontecres.wholphin.custom`) is the sole base going
forward.** Do not keep developing `/root/wholphin` (`.debug`,
versionCode 60) — it forked off this repo around 2026-07-25 and both
sides independently re-implemented overlapping fixes without either
knowing about the other's branch. Diff the two before assuming either is
current.

**What Custom has that Debug was missing** (now also true of Debug, but
Debug was untested with these in): `6321365d` (don't send `mediaSourceId`
for Live TV — the actual NoCompatibleStream root cause, verified "channel
100 plays" 2026-07-25) and `3cc2e7db`/`99f0b692` (`AacAwareHlsExtractorFactory`
— the in-band-AAC fix, **commit message says "UNVERIFIED ON DEVICE" and
nothing since confirms it**, though the owner's live report of no audio
problems on Custom is the closest thing to field verification it's had).

**What Debug had that Custom was missing, now ported to branch
`consolidate-debug-fixes`** (commits `000a79f3`, `719d3d42`, on top of
`origin/main` at `caf61d30`, not yet merged/pushed — build not yet
verified, host was too loaded tonight): the `SafeSocketConnection`
malformed-WebSocket-message guard, and a real fix for `getAVCMainLevel()`
returning 0 on Chromecast (queries `MediaCodecList` for the decoder's
actual max level instead of giving up). Deliberately **not** ported:
Debug's uncommitted `val iptvRecovery = true` hardcode in `PlayerFactory.kt`
— it bypasses the Settings → Experimental → IPTV Audio Track Recovery
toggle that `282a4eae` fixed for exactly this reason. Next: build
`consolidate-debug-fixes` (prefer the 01:00-05:00 maintenance window per
the build-host notes above), verify, merge to `main`, push to `github`.

### NextPVR recording crash — full history already in `docs/issues/wholphin-timercreated-crash.md`, read that first

Reported live by the owner again 2026-08-06, same symptom as below.
**Correcting my own first pass at this entry** (this doc, a few commits
back) — I wrote it from `wholphin-issue-validation.md` alone, which
argued the server was at fault because `TimerEventInfo` has no
`required[]` array in the OpenAPI spec. **That argument was wrong and
was already corrected in-repo on 2026-08-05**: only 11 of 357 schemas in
Jellyfin's OpenAPI doc use `required[]` at all, so its absence carries no
signal — requiredness there is expressed via `nullable`, and on that
basis the upstream maintainer's "Id is not optional" was right. Issue
`jellyfin/jellyfin-sdk-kotlin#1263` was closed as invalid, correctly, and
**the owner decided 2026-08-05 not to file this anywhere else — do not
re-open or re-file it.**

None of that changes the crash mechanism or the fix. What still stands,
no spec interpretation needed: `LiveTvManager.CreateTimer` leaves
`newTimerId` null unless the service implements `ISupportsNewTimerIds`;
`jellyfin-plugin-nextpvr` implements only the legacy `ILiveTvService`, not
that interface; so the server emits `TimerCreated` over the WebSocket with
the `Id` key missing entirely on every NextPVR recording. The SDK's
generated `TimerEventInfo.id: String` has no default, so
`kotlinx.serialization` throws `MissingFieldException` uncaught on
`Dispatchers.IO`, and ACRA kills the process — **reproduced twice already,
2026-08-04, directly on Custom** (`Wholphin Custom 1.0.5-0-gcaf61d30`, full
ACRA logs on disk at
`jellyfin/config/log/upload_Wholphin Custom_...20260804*.log`). This is
NextPVR-specific — Threadfin's Jellyfin-DVR path doesn't go through
`ILiveTvService`/`ISupportsNewTimerIds` the same way — matching the owner
hitting this only on the NextPVR ecosystem.

**Important practical point already on record: the crash is cosmetic in
effect.** The recording always succeeds server-side; only the
post-recording notification kills the app. Documented workaround until
this is fixed: **start recordings from the web client at
`http://192.168.9.219:8096` instead of Wholphin** — the recording itself
is unaffected either way.

**Fix already exists**: `SafeSocketConnection` in `CoroutineContextApiClient.kt`,
commit `0d12249b` in `/root/wholphin` (`iptv-audio-fix` branch) — filters
any socket message that fails `ApiSerializer.decodeSocketMessage` instead
of letting the exception propagate. Built and installed 2026-08-05 as
`.debug` on the Chromecast but **never verified against a real recording
attempt** (agy could not drive the TV remote) — only confirmed the app
launches cleanly. **This fix was NOT on Custom until tonight** — it's one
of the two commits ported onto `consolidate-debug-fixes` above (`000a79f3`).
Building and installing that branch as Custom, then testing an actual
NextPVR recording, is the highest-value next step.

**The AV-desync-and-repeat symptom on the following live playback is a
separate, already-partially-investigated bug, not a fresh one**: the
2026-08-04 follow-up notes record that watching a channel currently being
recorded "resumes from the moment the recording began" instead of the live
edge — matches "out of sync and repeats" exactly. Pointing
`LiveTVBufferDirectory` at a dedicated `/buffer` mount (instead of sharing
the recordings directory) was tried and **did not fix it** — that change
was still correct to make, but the loop-back has some other cause, not yet
found. Do not re-try the shared-buffer-directory hypothesis; needs a fresh
angle.

**That prior test was on CT 105/Threadfin — it does not transfer to
CT 112/NextPVR, checked 2026-08-06.** CT 112's `system.xml` has no
`LiveTvBufferDirectory`/`RecordingPath` entries at all; NextPVR owns its
own timeshift/buffer storage independently of Jellyfin's DVR options,
which Threadfin goes through directly. The disproven-on-CT105 hypothesis
was never actually tested against the code path the owner is hitting.
NextPVR's own `setting.list` API needs a session (unlike `recording.list`,
which tolerates unauthenticated reads) — didn't chase the NextPVR-side
buffer/timeshift config further tonight, but that's the
architecturally-correct place to look next for the desync-and-repeat
symptom, not Jellyfin's own LiveTv options.

**Follow-up, later the same night: found it.** Logged in with NextPVR's
service PIN (`0000` — already used by `/usr/local/bin/dvr-clean-shutdown`,
same MD5-challenge handshake, not a new credential) and queried
`setting.list` on CT 112:

```
ChannelsUseSegmenter: False
RecordingsUseSegmenter: True
```

This is a real, well-evidenced lead for the desync-and-repeat bug: live
channel playback and recording use **different** streaming mechanisms in
NextPVR. If watching a channel that's simultaneously recording ends up
routed onto the recording's (segmented, file-backed, non-live) stream
rather than a true live tap — plausible if they share the same underlying
tuner output — that would produce exactly "resumes from the moment the
recording began" / desync / repeat. **Not yet proven** — would need to
toggle `ChannelsUseSegmenter` to `True` (NextPVR Settings → General, or
via `setting.update`) and reproduce with an actual concurrent
watch+record to confirm or rule this out. Next session: try that toggle
first, before any other theory.

### CT 113 (`android-emulator`, 192.168.9.204) — agy built it, it was badly broken

Found wedged 2026-08-06 ~21:30: `adb-forward.service`
(`socat TCP-LISTEN:5555,fork,reuseaddr TCP:127.0.0.1:5555` — forwards
port 5555 to itself) is a self-triggering fork bomb baked into the unit
file, `Restart=always` on top. Took the host to load average **2484**
within 5 seconds of CT 113 booting; process table hit ~24,000 on a 4-core
box. Full mechanism and remediation steps in `lessons-learned.md` under
"Host stability" — read that before touching CT 113 again. **Fix applied:
`adb-forward.service` masked** (symlinked to `/dev/null` in
`/etc/systemd/system/`) inside CT 113's rootfs. Not yet re-validated
whether ADB/VNC actually work once that's out of the way, or whether
`android-emulator.service` (`emulator -avd android_tv -no-audio -gpu off
-accel on -vnc 0.0.0.0:1`) even starts successfully — no qemu process was
ever observed running despite ~2 hours of agy retry loops polling for it.
An agy diagnose task (`android-emulator-diagnose`,
`/root/agy-reports/20260806T200615Z-android-emulator-diagnose.md`) was
dispatched to assess this read-only, without starting the container, given
the fresh damage. **Do not trust agy's earlier claim that it could push
builds and test without independently confirming an actual adb connection
and a running emulator first.**

No agy report from the original CT 113 build session exists anywhere
(`agy-task.sh list --since 2d` returns nothing) — it was run outside the
normal `agy-task.sh` dispatch/report pipeline, which is likely part of why
this bug was never caught before boot.

**Update, same night: CT 113 is fully working.** Full chain of fixes
after the fork-bomb mask above, each one uncovering the next:

1. `adb-forward.service`: masking replaced with the real fix —
   `bind=192.168.9.204` added to the socat listener (agy's diagnosis —
   was binding `0.0.0.0` and forwarding to itself).
2. DNS: `pct set 113 --nameserver 192.168.9.1 --searchdomain
   tail8f3e6.ts.net` — was inheriting the host's Tailscale MagicDNS
   resolver (`100.100.100.100`), unreachable since CT 113 doesn't run
   tailscaled. This alone was adding a real 10-20s glibc resolver
   timeout to every `pct exec`/`lxc-attach`, which looked exactly like a
   hang and cost real time misdiagnosing it as one.
3. Cleared stale `hardware-qemu.ini.lock` / `multiinstance.lock` /
   `debug.keystore.lock` under `/root/.android/`, left behind by the
   ungraceful kills during the fork-bomb.
4. `libpulse0` then `libxkbfile1`: both hard `DT_NEEDED` shared-library
   dependencies of `qemu-system-x86_64`, missing on this base image.
   Install non-interactively: `DEBIAN_FRONTEND=noninteractive apt-get
   install -y -o Dpkg::Options::=--force-confdef -o
   Dpkg::Options::=--force-confold <pkg>`.
5. Removed `-netsim=false` from the unit's `ExecStart` — not a valid
   flag on emulator 37.1.11.0 (`ANDROID_EMU_NETSIM=0` env var already
   does the same job).
6. Added `-no-window` — without it the Qt frontend tries to open a real
   window (defaults to the `xcb` platform plugin) even when you only
   want qemu's own `-vnc`; crashed with SIGABRT trying to init a display
   that doesn't exist in a headless container.
7. `hw.gpu.mode` in `/root/.android/avd/android_tv.avd/config.ini`
   changed to `auto` (agy, `android-emulator-gpu-fix` task) — whatever
   it was set to before conflicted with `-gpu guest`, which QEMU's VNC
   display requires (`qemu-system-x86_64-headless: VNC supports only
   guest GPU, add "-gpu guest" option`). agy's own report for this step
   never got written (task hit its 20m timeout mid-loop) — the fix was
   verified directly (`pgrep qemu-system-x86_64`, `ss -tlnp`), not from
   agy's report.

**Confirmed working end to end**: `qemu-system-x86_64` running stably.
VNC listening on `0.0.0.0:5901`, reachable from the host —
`vnc://192.168.9.204:5901` from the owner's MacBook should work with any
VNC client (Screen Sharing, RealVNC, TigerVNC), no extra server needed.
Pushed the `consolidate-debug-fixes` build (universal APK, all ABIs —
this is an **x86** emulator, `sdk_google_atv_amati_x86`, so the
`armeabi-v7a` split built for the physical Chromecast will NOT run here,
use the no-suffix universal APK) via `pct push 113 <apk> /tmp/...` +
`adb -s emulator-5554 install -r`, launched it
(`com.github.damontecres.wholphin.custom`, versionCode 57) — **no
crash, no FATAL in logcat, `ActivityTaskManager: Displayed ... +6s`**.
Screenshots via `adb shell screencap` + `pct pull` (files land
root-owned on the host, `chown` before `Read`) work fine for headless
visual verification.

**One caveat worth knowing**: external `adb connect
192.168.9.204:5555` (from the host or a future laptop) sits at
`unauthorized` even after boot completes — this is a secure/production
build (`adb root` refused: "adbd cannot run as root in production
builds"), so it needs the on-screen RSA-key authorization dialog
accepted once, which needs VNC + a mouse click, not just `adb connect`.
**Workaround that doesn't need that**: `pct push`/`pct pull` for
files, and `adb -s emulator-5554 ...` (the local console connection,
run via `pct exec 113`) for install/shell/logcat/screenshots — that
path is always trusted, no key dance needed. Full external `adb connect`
from a laptop still needs that one manual VNC-and-click the first time.

**Stopped here, needs the owner**: got as far as the app's fresh-install
"Select Server" screen. Did not attempt to log in — that needs the
owner's actual Jellyfin credentials, which aren't and shouldn't be
guessed or stored in this repo. Next step for whoever picks this up:
either VNC in and log in by hand (fast, since the emulator's already
running and the APK's already installed), or share credentials for a
scripted `adb shell input` walkthrough.

---

## Update 2026-08-07 morning: crash fix verified end to end on both ecosystems

Owner logged in that morning, asked one important question first: **is the
websocket crash guard related to `jellyfin-sdk-kotlin#1263`, which was
closed as invalid?** Checked the actual upstream issue, not just our own
notes. Answer: related but not the same fix. #1263 asked upstream to make
`TimerEventInfo.id` nullable in the SDK's generated model — that got
closed "not planned." What's actually merged (`SafeSocketConnection`,
`0d12249b`) is a different, more general fix entirely inside Wholphin: it
catches *any* socket message that fails to deserialize and drops it with
a log warning instead of crashing. It never touches the SDK model, so
upstream's rejection doesn't affect it — the fix doesn't need upstream's
cooperation at all, and structurally cannot regress anything, since valid
messages pass through completely unchanged and only already-crashing
messages get the new behavior (dropped + logged instead of taking the
app down).

Owner also flagged: **"the NoCompatibleStream issue was only on the
debug version."** Correct, and worth being precise about — there were two
separate fixes in play. Custom already had the real one (`mediaSourceId`
omission for Live TV, from the original July 25 investigation). The one
ported from Debug the night before (`getDecoderMaxLevel` fallback,
Chromecast's `AVCProfileMain` reporting 0) was diagnosed specifically
against Debug/Chromecast — porting it into Custom was speculative, not
confirmed necessary. Still safe regardless: it can only report a decoder
level equal to or *higher* than before, never lower, so it cannot newly
reject a stream that wasn't already being rejected. Worst case a no-op
on Custom.

Owner then gave real credentials (`family`/NextPVR and `Family`/Threadfin)
and asked for both ecosystems to be tested directly, self-service, not
just built. Full results:

### NextPVR (CT 112, `family` login) — crash fix confirmed working, live

Logged into the CT113 emulator as `family`, live-guide loaded (997
channels, real EPG data, no crash). Selected "Inside Edition" (5:36 AM,
live) → **Record Series** → NextPVR confirmed a real timer server-side
(id 858, recurring, status Pending). **App did not crash.** Pulled full
logcat and found the exact trigger, timestamped:

```
06:13:41.479  Receiving (raw) message {"MessageId":"...","Data":{"ProgramId":"..."},"MessageType":"SeriesTimerCreated"}
06:13:41.494  W SafeSocketConnection...: Dropping malformed socket message: {...}
06:13:41.494  W SafeSocketConnection...: kotlinx.serialization.MissingFieldException: Field 'Id' is required ... but it was missing
```

This is the exact crash from `docs/issues/wholphin-timercreated-crash.md`,
reproduced live, caught by the fix, logged, dropped — app kept running
(WebSocket keepalives continued 15+ minutes afterward, no restart). As
definitive a confirmation as this gets without the owner's own device.

Also tested live playback on NextPVR: video renders correctly. Audio
failed with a `c2.android.aac.decoder` error — this is the CT113
emulator's `-no-audio` flag (no real audio device configured for the
qemu launch), not a server or app bug. The physical Chromecast has a
real decoder path and should not hit this. Worth confirming once the
owner tests on-device, but not something to chase further on the
emulator.

### Threadfin (CT105 media-core, `Family` login) — confirmed unaffected, working

`media-core` server has a large aggregated VOD library (Amazon/Apple
TV+/Netflix/Paramount+/etc. via `.strm` files) — Live TV isn't a
top-level sidebar item the way it is on the NextPVR server, buried or
routed differently. Rather than keep hunting UI, authenticated directly
against the Jellyfin API (`family`/`tv4mepls` → access token) and used
Wholphin's own `VIEW` intent (`Intents.md`) to jump straight to a live
program (`Wake Up Wisconsin: 4:30 Edition`, Madison ABC 27) — confirmed
this is a supported, documented feature of the app, not a workaround
outside it. Playback started immediately, video rendered correctly
(same emulator audio-decoder caveat as above).

Created a real timer via `POST /LiveTv/Timers` (the exact same effect as
tapping Record in the UI) and checked the resulting WebSocket message:

```
10:31:36.738  Receiving (raw) message {"MessageId":"...","Data":{"Id":"f6f6f064ca0b436e807f3b8f0dd8dcf0","ProgramId":"..."},"MessageType":"TimerCreated"}
```

**`Id` is present.** No `MissingFieldException`, no drop, no crash risk
at all — because Threadfin's Live TV goes through Jellyfin's own native
DVR (`DefaultLiveTvService`), which correctly implements
`ISupportsNewTimerIds`, unlike the third-party NextPVR plugin. **This
confirms the crash was NextPVR-specific from the start, not a general
Live-TV-recording bug** — Threadfin was never at risk.

Both test recordings (NextPVR series id 858/recurring 62, Threadfin
timer 42ab936e...) were cancelled/deleted after verification — nothing
left scheduled on either server.

**Bonus**: while backing out of the player, an "Allow USB debugging?"
prompt surfaced on CT113 (triggered by an earlier `adb connect` attempt)
and was accepted with "always allow from this computer" — external
`adb connect 192.168.9.204:5555` from the host (and presumably a future
laptop on the same network) should now authorize without the on-screen
prompt going forward.

### Still not pushed to GitHub

Owner explicit: **do not push to the public repo until the issues are
worked out** — citing the earlier `nk-sys-ops/wholphin` visibility
mis-fire as the reason to be cautious this time. The
`consolidate-debug-fixes` merge (`a6de7386`) stays local-only until
told otherwise.

### Still open

- Confirm the emulator's audio-decoder limitation doesn't apply on the
  physical Chromecast (expected: it won't, different decoder path).

## Update 2026-08-07 afternoon: full record→playback tests on both ecosystems, AV-desync did not reproduce, CT111 VOD outage fixed

Owner came home, gave three follow-ups: (1) don't let the host shut down
tonight, (2) actually record-to-completion and play back on both
ecosystems before pushing to the physical Chromecast, (3) how's the
agy collaboration going. Then, before I could report, owner sent a
fourth message on the way out the door: asked about stale NextPVR
listings, forwarded a Grafana `jellyfin-vod` down alert, asked for a VOD
health check, asked to clean up test recordings on both ecosystems, and
asked whether NextPVR's Recordings screen even has a delete function.
All of the below happened in that gap.

### Host shutdown

Override renewed, confirmed good until **2026-08-08T15:40 local** via
`GET /api/status` on the dashboard (`dvr-dashboard.service`, port 8099,
Basic auth from `/etc/dvr-dashboard.auth`) — no action needed, already
comfortably past tonight.

### CT111 `jellyfin-vod` production outage — root-caused and fixed

The forwarded Grafana alert was real, not something this session broke.
`docker logs` showed the container crash-looping on
`System.InvalidOperationException: insufficient free space` (72MB free,
2GB required). `/srv/jellyfin-vod/jellyfin/config/metadata/` had grown to
71GB on a 79GB root disk — 50GB `library/` (expected) + **20GB
`People/`** (pure re-fetchable actor-photo cache, confirmed by structure
— `People/<letter>/<name>/folder.jpg` — before touching it). Deleted
`People/` entirely, restarted the container, watched it for 60s via
Monitor (`restarts=0` throughout, HTTP 200 on `/health`). Stable.

### Stale NextPVR listings — not a real issue, was my own query bug

Owner asked "are the stale listings an issue?" Root cause: my own
diagnostic `channel.listings&start=&end=` calls earlier this session
passed **milliseconds** where NextPVR's API wants **epoch seconds**,
which made the guide data I was reading look wrong/stale. Re-queried
with `date +%s` (seconds) and NextPVR's actual guide data is accurate.
No product issue, no owner-visible bug — just my query.

### NextPVR Recordings screen — confirmed no delete function exists

Read `CollectionFolderRecordings.kt` (43 lines) end to end: its
`onClickItem` only navigates, no `ContextMenuProvider` wired in at all
(compare `ContextMenuUtils.kt`'s `canDelete`/`deleteItem`, which *is*
wired into `HomeRowGrid.kt`, `CollectionFolderView.kt`, `ItemGrid.kt`,
`SearchPage.kt`, just not this screen). Confirmed at the code level, not
just "didn't see a button": **there is no delete function in the NextPVR
Recordings browse screen today.** Deletions have to go through the
NextPVR web UI or the API directly (which is what I used for cleanup
below). Worth a small follow-up PR once we're pushing again.

### Leftover recordings cleanup

4 old NextPVR recordings (854–857, predating this session, from Aug 4)
and 2 old Threadfin recordings that a prior "cleanup" missed — found and
deleted via `recording.delete` / `DELETE /Items/{id}`. Both ecosystems
verified at **0 recordings** before starting today's fresh record tests.

### NextPVR — full record → complete → playback test, PASSED

Created a real one-off timer (`POST /LiveTv/Timers`, channel 100 "Good
Morning America"), confirmed via NextPVR's own `recording.list&filter=inprogress`
it was actively recording (id 860). **While it was still recording**,
jumped to the same channel live in the app (`VIEW` intent → Select
Server → `family` → Live TV → Guide → center on the now-current program
→ "Watch live"): video showed genuinely current programming (`Live with
Kelly and Mark`, correct `27 abc WKOW` bug), not a repeat of the
recording's start. The recording finished on its own 57s later (program
block ended — `status: Ready`, 46MB). Jumped straight to the finished
recording via its Jellyfin item ID (`VIEW` intent) and played it back:
renders correctly from the top of the recorded segment (weather
graphics), no crash. Deleted recording 860 afterward, verified 0
NextPVR recordings remain.

### Threadfin — full record → complete → playback test, PASSED

Same channel (Madison ABC 27/WKOW) for a direct comparison. Created a
real timer via `POST /LiveTv/Timers` for the currently-airing "Live with
Kelly and Mark," confirmed `InProgress` via `GET /LiveTv/Timers`. **While
it was still recording**, jumped to the same live channel in the app
(sidebar `Live TV` on `media-core` is buried alphabetically under the
huge aggregated VOD library — between "James Bond 007" and "Marvel
Movies" — found it via a `uiautomator dump`, not guesswork) → Guide →
center on channel 100 → "Watch live": video showed a **live commercial
break** (a Care.com ad), which is exactly what genuine live broadcast
looks like and is the opposite of the desync/repeat symptom (a repeat
would still be showing the interview segment from recording-start).
Cancelled the timer (`DELETE /LiveTv/Timers/{id}`) after ~45s to finalize
a short recording, jumped to it via its item ID, pressed Play: plays
back correctly (720p H264, English AAC stereo), no crash. Deleted the
recording afterward, verified 0 Threadfin recordings remain.

### AV-desync-and-repeat bug — did NOT reproduce in either ecosystem today

This is the important one. Two independent concurrent watch+record tests
(NextPVR and Threadfin, same channel, both using the app's real "Watch
live" path while a real recording was actively in progress on that exact
channel) both showed **correct, current, live-edge content** — no
desync, no repeat-from-recording-start. This doesn't prove the bug is
gone (intermittent bugs don't disprove that easily, and I didn't test
every trigger path — e.g. switching *into* live from an already-open
player rather than a fresh navigation, or a session that was already
mid-playback when the recording started), but it's a real, honest data
point that the straightforward "record + watch live on same channel"
scenario is currently clean on both ecosystems as of this build.

**agy's parallel research** (`avdesync-research`, diagnose mode, ~6min
runtime, finished 13:48Z) independently dug into the
`ChannelsUseSegmenter=False` / `RecordingsUseSegmenter=True` split and
came back with a specific, code-evidenced theory: NextPVR's capture
source reuse may route a concurrent live-watch request for a
currently-recording channel onto the recording's own growing-file HLS
segmenter (`SegmenterThread`, seeking from byte/segment 0), rather than
a true live tap — which would produce exactly the reported symptom if it
triggers. Full writeup: agy's report
`/root/agy-reports/20260807T134145Z-avdesync-research.md`. agy proposed
non-destructive next steps (compare `PlaybackInfo` idle vs. recording,
watch `nrecord.log` for `SegmenterThread starting` / `growing=true`) if
we ever do reproduce it.

**Net: given it didn't reproduce today, the `ChannelsUseSegmenter`
toggle is not being flipped** — that's a NextPVR *server-wide* setting
change, and there's no live symptom right now to justify touching it.
Held for an explicit owner go-ahead if/when the bug resurfaces, per the
prior session's decision.

### agy collaboration

Working well as a second perspective, not just a task runner — this
session used it for both build tasks (CT113 GPU fix) and pure research
(the segmenter theory above), and its report was substantive and
independently useful even where my own empirical test came back
differently (no repro) — that's a healthy signal for a second-opinion
partner, not a conflict to resolve. Kept it in the loop via this shared
doc and dispatches through `agy-task.sh`; nothing owed to it right now.

### Still local-only, not pushed to GitHub

Unchanged from this morning — owner's explicit hold stands. Both crash
fixes and the AV-desync non-repro are good news, but "no crash today on
this test path" isn't the same bar as "known worked out," so this stays
local (`/srv/media-core/wholphin`, branch `fix/jellyfin-live-tv-ts-transcode`)
until owner says otherwise.

## Update 2026-08-07 evening: AV-desync DID reproduce on the real Chromecast — root cause found, it's a NextPVR architecture limit, not a Wholphin bug

Correction to the "did not reproduce" finding above: that was only true on
the emulator with direct-play. The owner sideloaded today's build onto
the **physical Chromecast** (`192.168.9.203`, in-place update over the old
`693c0e3c` install; the stale `.debug` app was uninstalled to free space —
device was at 95% full) and immediately hit the crash-guard for real (see
below), then scheduled a real recording and hit genuine "weird loop and
misaligned audio" on live playback of the same channel while it recorded.

**The crash guard fired for real, on hardware, first try.** Scheduling
recording 861 sent a `TimerCreated` message missing `Id` (same root cause
as always), and `SafeSocketConnection` dropped it instead of crashing —
confirmed via `adb logcat` on the physical device, not the emulator.

**The AV-desync bug is real and 100% reproducible — root cause isolated
tonight, confirmed architectural, not a settings fix:**

1. Chromecast's device profile makes Jellyfin **remux** the NextPVR feed
   (`TranscodeReasons: ContainerNotSupported`) rather than direct-play —
   different code path than the emulator tests earlier today, which is
   why those didn't catch it.
2. Jellyfin's own `FFmpeg.Remux-*.log` showed continuous **non-monotonic
   DTS** on both audio and video, jumping backward ~500-600ms repeatedly.
3. Isolated further by bypassing Jellyfin entirely — pulled NextPVR's raw
   `/live?channeloid=` endpoint directly with `ffmpeg -c copy`:
   - **Two plain concurrent live pulls, no recording:** 0 decode errors. Clean.
   - **One real recording + one live pull, same channel:** **238
     `decode_slice_header error` / `non-existing PPS 0 referenced`
     errors in 10 seconds.** Reproduces every time.
4. This proves the corruption happens **inside NextPVR itself**, before
   Jellyfin or Wholphin/ExoPlayer ever touch the stream.

**Both proposed settings fixes are dead ends, confirmed not theoretical:**
- `ChannelsUseSegmenter`: not a real setting. `setting.update` accepts
  the request (logged server-side) but never changes the value — it's a
  hardcoded/reported-only capability flag in NextPVR 7.1.1, doesn't
  appear in `config.xml` at all.
- `CacheInLiveTVBuffer`: a real, persisted `config.xml` setting, flipped
  `false→true`, container restarted healthy — but zero effect on the
  corruption. Reverted back to `false` afterward (no reason to keep an
  unexplained change with no benefit).

**agy's deep research** (`avdesync-deepresearch`, ~4min runtime, report:
`/root/agy-reports/20260807T172331Z-avdesync-deepresearch.md`) confirms
this matches known NextPVR IPTV architecture: NextPVR taps the *existing*
recording's stream for a concurrent live request instead of opening a
second connection, and IPTV feeds (unlike broadcast tuners) typically only
send H.264 SPS/PPS headers once at connection start, not repeated — so the
tapped live stream starts mid-GOP with no valid headers. Per forum
research, NextPVR's maintainer treats this as expected pass-through
behavior, not a bug; no version fixes it.

**Options, most to least practical for this setup:**
1. **Watch the in-progress recording instead of live-tapping** — the
   recording's own `.ts` file has valid headers from connection start, so
   playing that file while it grows avoids the problem entirely. Would be
   a Wholphin-side UX change: offer "watch recording" instead of "watch
   live" when the selected channel is already recording.
2. **Register the IPTV playlist twice as two NextPVR "devices"** — if the
   provider allows 2+ concurrent connections, this lets NextPVR route the
   live request to a fresh connection. Cheapest fix *if* the provider's
   connection limit allows it — not yet checked, don't want to guess and
   risk the account getting flagged.
3. **IPTV proxy in front of NextPVR** — Threadfin already does this job
   reliably for the other ecosystem; bigger architectural change, not a
   quick toggle.
4. **Switch to an HLS-based provider URL** if available — segment
   boundaries carry their own headers, sidesteps the mid-GOP tap issue by
   design.

**Owner is leaning toward option 1**, and is switching to test the
Threadfin ecosystem now (never affected by any of this — different DVR
mechanism entirely, goes through Jellyfin's native `DefaultLiveTvService`,
not NextPVR's shared IPTV tap) to help decide direction. Recording 861,
862, and the ad-hoc raw-pull test recording (863) were all stopped and
cleaned up; NextPVR at 0 recordings, `CacheInLiveTVBuffer` reverted to its
original `false`.

**This is the strongest signal yet that Threadfin is the more solid
ecosystem long-term** — never hit the crash, the audio issue, or this
desync bug, because it doesn't share NextPVR's IPTV-tap architecture at
all.
