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
| Intended GitHub remote | `git@github.com:nk-sys-ops/wholphin.git` (private) |
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

## Deferred, explicitly not forgotten

- **Prune stale Tailscale nodes + add ACLs** (MEDIUM-HIGH). 16 of 21
  peers offline, several 500–972 days, three still advertising as exit
  nodes; the tailnet is a fully trusted zone with full router admin and
  whole-LAN access. See `docs/router-security-audit-20260725.md`.
- **WireGuard keys in Loki history** (MEDIUM). Present from before the
  2026-07-25 redaction fix; LAN-only, ~30-day retention. Options:
  accept retention / purge via Loki delete API / rotate.
