# Handoff prompt for Gemini (Antigravity) — 2026-07-25

Copy everything below the line into Gemini.

---

You are picking up work on the **pve-01 Proxmox homelab** (Project
Media-Core). Claude Code did the previous shift; we alternate to spread
token usage. Read `/root/pve-01-docs/README.md` and
`/root/pve-01-docs/CLAUDE.md` first — they are the source of truth and
were just updated. Key docs:

- `docs/wholphin-fork-status.md` — the current focus
- `docs/router-rebuild-runbook.md` — router backup/restore + gotchas
- `docs/router-security-audit-20260725.md` — open security items

## Current objective: get the forked Wholphin APK running on the Chromecast

**Context.** The Live TV **no-audio** bug is NOT server-side. Three
server-side theories were tested and disproven:
1. DVB-subtitle interference — `ffprobe` shows no subtitle stream on
   affected channels.
2. Swapping channels 100/102 to "clean" provider streams — no change.
3. **NextPVR** was deployed and channels 100/102 served to Jellyfin
   through it instead of Threadfin — **audio problem persisted**.

So effort moved to the client. NextPVR is **parked, not removed** — leave
it alone for now; we will revisit only after the client fix is proven.

**The fork** lives at `/srv/media-core/wholphin` on the pve-01 host (not
CT 105). Three custom commits on top of upstream `cf3c00a1`:
`a5e605fe` (TiviMate-grade TsExtractor flags + ExoPlayer
`onTracksChanged` audio-fallback listener), `96b2f41b` (IPTV Audio Track
Recovery toggle under Settings → Experimental Settings), `ec562b4e`
(default that toggle ON).

## What Claude just did — do not redo

1. **Fixed the build.** `./gradlew assembleDebug` was failing on all three
   flavours with `Unresolved reference 'iptvRecovery'` at
   `PlayerFactory.kt:105`. Cause: `patch_pf_pref.py` anchored the
   `iptvRecovery` declaration to the `tunneling` read at ~line 137, but
   it is consumed at ~line 105 — a use-before-declaration, which Kotlin
   rejects. The declaration was relocated to sit beside `decodeAv1`
   (~line 92, before the use). Original saved as
   `PlayerFactory.kt.pre-20260725-declfix`.
   A rebuild was started; check `/root/wholphin-build2.log` for
   `BUILD SUCCESSFUL` / `BUILD FAILED` and re-run
   `cd /srv/media-core/wholphin && ./gradlew assembleDebug` if needed.

2. **Corrected two wrong claims in the docs:**
   - The fork is **NOT on GitHub**. All three commits were missing from
     `/root/wholphin` (the only clone with a GitHub remote), and
     `nk-sys-ops/wholphin` does not resolve with any credential on this
     box (the `github-mirror` deploy key is scoped to `pve-01-docs`; `gh`
     is authed as `InDaSky314`). Mitigated: pushed as branch
     `iptv-audio-fix` into `/root/wholphin`, plus a verified `git bundle`
     in `/root/wholphin-backups/`. **Do not claim GitHub is up to date.**
   - **The APK path in the old docs does not exist.** The project has
     build flavours, so `assembleDebug` emits
     `app/build/outputs/apk/<flavour>/debug/app-<flavour>-debug.apk`.
     Use the **`default`** flavour for the Chromecast (`firetv` is for
     Amazon devices, `appstore` is the Play Store build). List the
     directory rather than assuming a filename.


## UPDATE 2026-07-25 (later) — read this before touching the player code

Two owner observations changed the picture, and they rule out most of what
we had been chasing:

1. **The Jellyfin web client plays the affected channels WITH audio.** The
   web client is served HLS, so **the audio is present in Jellyfin's
   output** — this is not server-side stripping.
2. **Forcing "always use FFmpeg" for decoding did NOT fix it**, which
   rules out HE-AAC / device audio-decoder theories.

Verified from Jellyfin's logs: Wholphin gets
`SupportsDirectStream: true` and fetches
`LiveTv/LiveStreamFiles/<id>/stream.ts` — **raw MPEG-TS demuxed by
ExoPlayer's TsExtractor**. It never requests `master.m3u8`. The web client
gets HLS, where **ffmpeg** does the demuxing. Same audio, different
demuxer: ffmpeg copes, ExoPlayer does not.

**So the highest-value next experiment is transport, not extractor flags:**
force Wholphin onto HLS by removing `Codec.Container.TS` from the video
`directPlayProfile` in
`util/profile/DeviceProfileUtils.kt` (~line 200-213), gated behind a new
`tsDirectPlay` flag on `createDeviceProfile(...)` (~line 64) and wired from
an Experimental preference in `services/DeviceProfileService.kt`
(~line 55-68) — the same pattern as the existing IPTV Audio Track Recovery
toggle. Full reasoning and the tradeoff (it affects DVR .ts recordings too,
since DeviceProfile is global) is in `docs/wholphin-fork-status.md`.

**Build environment:** the project's `-Xmx2048m` OOMs the Kotlin compiler
during IR lowering when flavours build in parallel. Already fixed at user
level in `/root/.gradle/gradle.properties` (6g/4g, workers.max=3) — run
`./gradlew --stop` after changing those. Build **`assembleDefaultDebug`**
only. pve-01 (4-core N5105) is a weak build host; prefer building
elsewhere and installing over ADB to `192.168.9.203:5555`.


## LATEST STATE — 2026-07-25, end of Claude's shift (read this first)

### Where we actually are

APK built and installed on the Chromecast. **Awaiting one on-device test
result** (owner to tune channel 133 with all experimental toggles OFF).
A cleared `adb logcat` capture is running to `/root/wholphin-playback.log`
on pve-01, so that test will be captured.

`git log` on the fork (`/srv/media-core/wholphin`, branch `main`, all
pushed to `github` = `nk-sys-ops/wholphin`):

| Commit | What |
|---|---|
| `8f45372a` | **Latest.** IPTV recovery made opt-in; always-on playback diagnostics |
| `08decc3e` | Build fix — `iptvRecovery` use-before-declaration |
| `ec562b4e` | Default IPTV Audio Recovery ON (**superseded by `8f45372a`**) |
| `96b2f41b` | IPTV Audio Track Recovery toggle in Experimental Settings |
| `a5e605fe` | TiviMate TsExtractor flags + audio fallback listener |

### The two most recent commits, in detail

**`08decc3e` — build fix.** `patch_pf_pref.py` anchored the `iptvRecovery`
declaration to the `tunneling` read (~line 137) but it is consumed at
~line 105. Kotlin requires a local `val` before use, so all three flavours
failed with `Unresolved reference 'iptvRecovery'`. Declaration relocated
beside `decodeAv1` (~line 92). Original kept as
`PlayerFactory.kt.pre-20260725-declfix`.

**`8f45372a` — two changes, both important:**

1. **Fixed an inverted default that is the suspected cause of playback
   breaking entirely.** The guard read:
   ```kotlin
   if (experimentalPreferences.enabled) experimentalPreferences.iptvAudioRecoveryEnabled else true
   ```
   `enabled` is the experimental **master switch**. On a fresh install
   proto3 bools default to `false`, so the master read false → the `else
   true` branch → **the TsExtractor flags were forced ON for every
   channel** while all three toggles in the UI read OFF. The owner
   confirmed seeing exactly that: everything off, nothing playing. Those
   are *video*-path flags (`FLAG_ALLOW_NON_IDR_KEYFRAMES`,
   `FLAG_DETECT_ACCESS_UNITS`), so forcing them on plausibly broke video,
   not just audio. Now reads the preference directly — **opt-in, defaults
   off**, upstream behaviour until deliberately enabled.
   **This supersedes `ec562b4e`'s intent.** Do not "restore" that default
   without understanding the above.

2. **Added always-on playback diagnostics**, deliberately *not* gated on
   any toggle. There was **no `onPlayerError` handler at all** — the reason
   failures surfaced as "not sure what the error is" with nothing in
   logcat. Now logs, all tagged **`WHOLPHIN_DIAG`**:
   - `onPlayerError`: ExoPlayer `errorCode`, `errorCodeName`, message,
     cause chain
   - `onTracksChanged`: every track's mime, codec, channel count, sample
     rate, language, **and selected state** (or `NO_TRACKS_AT_ALL`)

### How to read the diagnostics — this is the decision table

```bash
grep WHOLPHIN_DIAG /root/wholphin-playback.log
# or live:
adb -s 192.168.9.203:5555 logcat -v threadtime | grep WHOLPHIN_DIAG
```

| Observation | Meaning | Correct next move |
|---|---|---|
| `playback_error code=… codeName=…` | A real, nameable failure | Act on that specific ExoPlayer error code |
| Audio track listed, `selected=false` | Track exists, not chosen | The fallback listener is the right fix — enable the toggle deliberately and retest |
| No audio track / `NO_TRACKS_AT_ALL` | ExoPlayer cannot see audio in the raw TS | **Do the HLS transport experiment** (below). ffmpeg *does* find it — the web client proves that |

Three distinct outcomes, three different fixes. **Do not start changing
player code until you have looked at this output** — that is the whole
reason the instrumentation was added.

### Still queued, in priority order

1. **Read the diagnostics from the owner's channel-133 test.** Decide via
   the table above. Everything else waits on this.
2. **If no audio track appears: the HLS transport experiment.** Full
   rationale in `docs/wholphin-fork-status.md`. Short version: Wholphin
   direct-streams raw MPEG-TS (`SupportsDirectStream: true`, fetches
   `LiveTv/LiveStreamFiles/<id>/stream.ts`) and demuxes with ExoPlayer's
   TsExtractor, while the Jellyfin **web** client gets HLS demuxed by
   **ffmpeg** and plays these channels **with audio**. Fix: drop
   `Codec.Container.TS` from the video `directPlayProfile` in
   `util/profile/DeviceProfileUtils.kt` (~line 200-213), behind a
   `tsDirectPlay` gate on `createDeviceProfile(...)` (~line 64), wired from
   an Experimental preference in `services/DeviceProfileService.kt`
   (~line 55-68) — same pattern as the existing IPTV toggle. Tradeoff:
   DeviceProfile is global, so DVR `.ts` recordings would transcode too
   (real CPU cost on CT 105's 2 vCPU).
3. **Debug-only settings API.** Owner explicitly approved accepting some
   security risk *while debugging*, on condition it is tightened later.
   An exported `BroadcastReceiver` gated on `BuildConfig.DEBUG` that
   reads/sets experimental preferences, so an assistant can flip settings
   over ADB instead of asking the owner to navigate menus:
   ```bash
   adb shell am broadcast -a com.github.damontecres.wholphin.debug.SET_PREF \
     --es key iptv_audio_recovery --ez value false
   ```
   **Must** be `BuildConfig.DEBUG`-gated and never reach a release variant.
   Flag the tightening explicitly when the audio issue is resolved — do not
   leave it as a silent loose end.

### Build & install facts you will need

- `cd /srv/media-core/wholphin && ./gradlew assembleDefaultDebug`
  (**not** `assembleDebug` — that builds appstore+default+firetv)
- Heap is already fixed at user level in `/root/.gradle/gradle.properties`
  (`-Xmx6g`, `kotlin.daemon.jvmargs=-Xmx4g`, `workers.max=3`). The
  project's own `-Xmx2048m` OOMs the Kotlin compiler during IR lowering.
  Run `./gradlew --stop` after changing those — daemons cache JVM args.
- Clean build ~17 min; incremental ~6 min on this box (4-core N5105).
- Install the **`armeabi-v7a`** APK. The Chromecast reports
  `ro.product.cpu.abilist = armeabi-v7a,armeabi` — **32-bit userspace**
  despite 64-bit hardware. The arm64 APK is wrong.
  ```bash
  adb -s 192.168.9.203:5555 install -r \
    app/build/outputs/apk/default/debug/*armeabi-v7a.apk
  ```
- Installed package id is **`com.github.damontecres.wholphin.debug`**
  (debug `applicationIdSuffix`). `dumpsys package com.github.damontecres.wholphin`
  returns nothing — use the `.debug` id.
- Wireless debugging **does not survive a Chromecast reboot** and the port
  is not stable; it must be re-enabled by hand in Settings → System →
  Developer options.
- Push work to **all three** places so it is never single-copy:
  ```bash
  git push github main                      # nk-sys-ops/wholphin (via github-wholphin alias)
  git push origin HEAD:refs/heads/iptv-audio-fix --force   # /root/wholphin
  git bundle create /root/wholphin-backups/wholphin-$(date -u +%Y%m%dT%H%M%SZ).bundle cf3c00a1..HEAD
  ```
  Plain `git@github.com` does **not** authenticate for this repo — the
  `github-wholphin` SSH alias and its deploy key do.

## Your next steps

1. Confirm the build succeeded and locate the **`default`**-flavour APK.
2. `adb connect 192.168.9.203:5555` — note **wireless debugging does not
   survive a Chromecast reboot** and the port is not stable; if it
   refuses, it must be re-enabled by hand in Settings → System →
   Developer options.
3. `adb -s 192.168.9.203:5555 install -r <default-debug apk>`
   (both the old Wholphin and official `org.jellyfin.androidtv` were
   already uninstalled, so the device is clean).
4. Sign in to Jellyfin, confirm **Settings → Experimental Settings →
   IPTV Audio Track Recovery** exists and is **ON** by default.
5. **Test audio on the known-bad channels: 133, 134, 313** (and 100/102).
   If audio works, that confirms the client-side root cause.
6. Commit any code changes in `/srv/media-core/wholphin`, and push to
   `/root/wholphin` so the work is not single-copy.



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

## Rules for this project

- **Verify, don't assume.** Claude found several documented claims that
  were untrue (GitHub state, APK path, "everything up to date"). Test
  things and report what you actually observed.
- **Confirm before touching shared infra.** CT 105 runs the live media
  stack the household uses. Nothing is streaming right now, but check
  `/root/bin/check-iptv-stream.sh` before restarting Threadfin/Jellyfin.
- **Never commit secrets.** Provider credentials live only in
  `/srv/media-core/.env` (600). Router snapshots in
  `/root/router-backups/` contain WireGuard private keys — 600, never
  committed.
- **Pinned image tags only**, never `:latest`.
- **`AGENTS.md` is a symlink to `CLAUDE.md`** — edit `CLAUDE.md` only.
- `origin` in `/root/pve-01-docs` pushes to **two** GitHub repos; after
  merging a PR run `git checkout main && git pull && git push origin main`
  to propagate to the mirror.
- **On the router**, `route_policy` `from_mac` must be a UCI **`list`**
  (`uci add_list`), never a scalar `option` — a scalar both crashes the
  Lua backend and leaves the firewall ipset empty, which **fails open**
  and silently leaks traffic outside the VPN. Apply route_policy changes
  with `/usr/bin/rtp2.sh apply`; `network reload` does not repopulate
  ipsets.

## Deferred — do not lose track of these

The owner wants these done later, not forgotten:

1. **Prune stale Tailscale nodes and add tailnet ACLs** (MEDIUM-HIGH).
   The tailnet is a fully trusted zone — any peer reaches the router
   admin UI, SSH, and the whole LAN — while **16 of 21 peers are
   offline**, several 500–972 days, three still advertising as exit
   nodes. Each retains valid credentials. Owner action in the Tailscale
   console.
2. **WireGuard keys in Loki history** (MEDIUM). Present from before the
   2026-07-25 redaction fix. LAN-only, ~30-day retention. Options:
   accept retention / purge via Loki delete API / rotate keys.
3. **Active WAN is the WiFi repeater** (`apclix0` = logical `wwan`, SSID
   `GIOT`) at metric 1, with ethernet at metric 2 — confirm that is
   intended. It also means the ~104 Mbps "direct WAN" baseline was
   measured through the repeater.

## How to hand back to Claude — required format

The owner alternates between Claude and Gemini to spread token usage, and
feeds each side's handoff to the other. **Before you run low on tokens**,
do both of these:

**1. Push everything.** Commit code, push the fork to all three locations
(above), and commit doc updates to `/root/pve-01-docs` — branch, PR, merge,
then `git checkout main && git pull && git push origin main` to propagate to
the mirror. Nothing should exist only in your session context or only on one
disk.

**2. Write a handoff to `docs/handoff-to-claude-<YYYYMMDD>.md`** and commit
it, covering these sections explicitly:

- **Exact stopping point.** Not "working on X" — the precise state. Which
  command was mid-flight, which file half-edited, what was about to be
  tested.
- **Verified vs assumed.** Mark each claim. This matters more than anything
  else here: this project has repeatedly lost time to documentation that
  claimed things which were never true (the fork was documented as "on
  GitHub, fully up to date" when the commits had never been pushed; the
  documented APK path did not exist). If you did not personally observe it,
  say so.
- **What you changed**, with commit hashes and *why* — the reasoning, not
  just the diff.
- **What you tried that did NOT work**, and how you know. Negative results
  are as valuable as positive ones here; the NextPVR test and the
  always-FFmpeg test both eliminated whole hypotheses.
- **Next steps**, in priority order, with the reasoning for the ordering.
- **Deferred items** — carry forward the list below verbatim if still open.
  The owner has said explicitly he does not want these dropped.
- **Anything you broke or left in a non-default state**, including debug
  hacks, relaxed security, or temporary credentials that need tightening.

Then tell the owner, in your final message, that the handoff is committed
and give him a short prompt he can paste to Claude.

### Working rules worth repeating

- **Verify, don't assume.** Several documented claims in this project turned
  out to be false. Test and report what you actually observed.
- **Confirm before touching shared infra.** CT 105 runs the live media stack
  the household uses. Check `/root/bin/check-iptv-stream.sh` before
  restarting Threadfin/Jellyfin.
- **Never commit secrets.** Provider credentials live only in
  `/srv/media-core/.env` (600). `/root/router-backups/` snapshots contain
  WireGuard private keys — 600, never committed.
- **`AGENTS.md` is a symlink to `CLAUDE.md`** — edit `CLAUDE.md` only.
- **Heavy builds** belong in the 01:00-05:00 maintenance window, or better,
  on a machine other than pve-01 (a 4-core N5105 is a poor build host; ADB
  reaches the Chromecast from any LAN/tailnet host).
- **On the router:** `route_policy` `from_mac` must be a UCI **`list`**
  (`uci add_list`), never a scalar `option` — a scalar crashes the Lua
  backend *and* leaves the firewall ipset empty, which **fails open** and
  silently leaks traffic outside the VPN. Apply with
  `/usr/bin/rtp2.sh apply`; `network reload` does not repopulate ipsets.
