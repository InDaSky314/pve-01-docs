# Live TV: state, custom build, and the update plan

Updated 2026-07-26 midday. Supersedes
`livetv-audio-rootcause-20260726.md` for *status*; that doc remains the
reference for the investigation and the ruled-out list.

---

## 1. Both bugs are fixed

**Bug A — `NoCompatibleStream` on all Live TV.** Wholphin sent a
`mediaSourceId` that can never match a live-TV source (they are
`MD5(channelId)+MD5(tunerUrl)`, generated server-side), so Jellyfin
filtered every source out. Commit `bddf59cc`. Verified.

**Bug B — no audio on channels 100/102/133/134.** Two compounding faults:

1. *Server:* Jellyfin's probe of the live stream misses the audio track
   and advertises a video-only codecs string.
2. *Client:* `DefaultHlsExtractorFactory.createTsExtractor` sets
   `FLAG_IGNORE_AAC_STREAM` whenever the codecs string lacks AAC, telling
   the TS demuxer to discard AAC elementary streams — even though the
   stream served contains a valid AAC track with its PID in the PMT.

Commit `99f0b692` fixes fault 2 client-side. **Confirmed working**: with
the patched build, ch100 and ch102 play with audio while the server is
*still* misbehaving identically — its probe still reports video-only and
its ffmpeg command still has no `-codec:a:0`. Since the `.debug` build
cannot use MPV (it links the stub), the audio can only be coming from
ExoPlayer, on channels that were previously deterministically silent.

Caveat for honesty: the `hls_codecs_patched` log line has not been
captured directly — player init predated every log buffer window. The
inference is strong (server unchanged, client the only variable,
previously-deterministic failure now absent) but not a direct sighting.
Worth grabbing opportunistically: start `adb logcat` *before* pressing
play and grep `WHOLPHIN_DIAG`.

---

## 2. Which build is which

| | `com.github.damontecres.wholphin.debug` (ours) | `com.github.damontecres.wholphin` (upstream v1.0.3) |
|---|---|---|
| Bug A + Bug B fixes | yes | no |
| MPV / `libmpv.so` | **no — stub** | yes |
| ffmpeg SW decoders | no | yes |
| Self-update source | our fork (after `f248c314`) | upstream |
| mpv.conf tuning | n/a | lives here |

**MPV cannot be shipped in our build.** `libmpv`, the media3 ffmpeg
decoders and the AV1 decoder are all gated behind `extensionsRepoActive`,
which requires the private Maven credential `WholphinExtensionsUsername`.
Without it Gradle silently links `wholphin-mpv-stub`, so selecting MPV
does nothing and any bundled `mpv.conf` would be dead config.

Since the ExoPlayer patch works, **MPV is no longer needed** — it was the
proof of diagnosis, not the destination. ExoPlayer was always smoother on
this box. Recommendation: run the patched `.debug` build as the daily
driver and keep the upstream+MPV install as a fallback.

To get MPV into our build anyway, one of:
- ask damontecres for extensions repo access, then set
  `WholphinExtensionsUsername`/`Password` in `~/.gradle/gradle.properties`
- drop a prebuilt `app/libs/wholphin-mpv-release.aar` in place (the build
  already prefers it if present — see `app/build.gradle.kts:34`)
- build libmpv for `armeabi-v7a` ourselves (significant work)

---

## 3. Shipping changes made today (`f248c314`)

- **Self-update points at our fork.**
  `AppPreference.UpdateUrl.defaultValue` →
  `https://api.github.com/repos/nk-sys-ops/wholphin/releases/latest`.
  Previously upstream, which would have replaced the patched build with a
  stock one on the next update.
- **Fixes ship enabled.** `iptvAudioRecoveryEnabled` now defaults to true
  in `AppPreferencesSerializer`. That gate carries the AAC fix, and proto3
  bools default to false, so without this it would ship switched off.

Wholphin's updater is already built for this — `UpdateChecker` polls the
configured URL, compares versions, and installs. The `default` flavor sets
`UPDATING_ENABLED = true`. It expects a GitHub release whose asset is
named **`Wholphin.apk`** (`UpdateChecker.ASSET_NAME`).

---

## 4. BLOCKED: publishing the release (needs your credential)

`gh` on this box is authenticated as `InDaSky314`, which cannot see
`nk-sys-ops/wholphin`. Pushes work only through the `github-wholphin`
deploy key, and **deploy keys cannot create releases**. So the release
must be cut with an account that has write access.

APK is built and staged, already named correctly:

```
/tmp/.../scratchpad/Wholphin.apk
  = app/build/outputs/apk/default/debug/
      Wholphin-default-debug-1.0.3-31-g99f0b692-54-armeabi-v7a.apk
```

armeabi-v7a because the Chromecast runs a 32-bit userspace despite 64-bit
silicon; the arm64 APK fails with `INSTALL_FAILED_NO_MATCHING_ABIS`.

To publish (from an account with access):

```bash
gh release create v1.0.3-mediacore.1 \
  --repo nk-sys-ops/wholphin \
  --target fix/jellyfin-live-tv-ts-transcode \
  --title "v1.0.3-mediacore.1 - Live TV audio + NoCompatibleStream fixes" \
  --notes "Live TV NoCompatibleStream fix (bddf59cc) and ExoPlayer in-band AAC fix (99f0b692)." \
  /path/to/Wholphin.apk
```

**Version numbering matters.** `UpdateChecker` only offers an update when
the release version `isGreaterThan` the installed one. The installed
version string derives from `git describe`, currently
`1.0.3-31-g99f0b692`. Tag releases so they sort upward, and re-tag on every
rebuild or the app will not see the update.

**Signing matters more.** Android refuses in-place upgrades across
different signing keys. These are debug-signed with root's
`~/.android/debug.keystore` on pve-01. Keep building on this box, or the
next update will fail to install and need a manual uninstall/reinstall
(losing app settings). Generating a dedicated release keystore and
committing its config is the durable fix — worth doing before this gets
relied on.

---

## 5. Suggested rebuild-and-release loop

```bash
cd /srv/media-core/wholphin
sudo git pull                      # if changed elsewhere
sudo ./gradlew assembleDefaultDebug
cp app/build/outputs/apk/default/debug/*armeabi-v7a.apk /tmp/Wholphin.apk
gh release create <new-tag> --repo nk-sys-ops/wholphin \
  --target fix/jellyfin-live-tv-ts-transcode /tmp/Wholphin.apk
```

The TV then picks it up on its own within 12 hours (`UpdateChecker`
throttles notifications to one per 12 h), or immediately via
*Settings → Updates*.

Worth automating later as a small script or a GitHub Action; not done yet.

---

## 6. Remaining work, priority order

1. **Publish the release** (§4) — unblocks self-update. Needs your creds.
2. **Dedicated signing keystore** — before anyone depends on auto-update.
3. **Capture `hls_codecs_patched` once** — turns a strong inference into a
   direct sighting.
4. **Upstream the ExoPlayer fix** to androidx/media. A valid TS with a
   declared AAC PID being silently discarded is a real bug; we have a clean
   reproduction.
5. **Upstream the Jellyfin probe bug** (fault 1). Related and already
   fixed: [#15479](https://github.com/jellyfin/jellyfin/issues/15479) is
   the same class inverted. A 10.12 nightly may already fix ours.
6. **Optional cleanup** — the `tsDirectPlay` toggle and unconditional `ts`
   injection in `19048035` target a non-cause and can be reverted. Keep the
   `DEVICE PROFILE` and `WHOLPHIN_DIAG` logging; the track logging is what
   cracked this.
7. **Revisit MPV only if wanted** (§2). Not needed for correctness now.

## 7. Repo state

- `/srv/media-core/wholphin`, branch `fix/jellyfin-live-tv-ts-transcode`,
  pushed to `nk-sys-ops/wholphin`
- `bddf59cc` Bug A · `99f0b692` Bug B · `f248c314` shipping defaults
- Threadfin fully reverted to stock (`buffer: -`, `-c copy`); backup at
  `settings.json.bak-20260726-084846`
- Jellyfin untouched

---

## 8. Consolidation and hardening (end of 2026-07-26)

### One app, renamed
The patched build is now **"Wholphin Media-Core"**, package
`com.github.damontecres.wholphin.mediacore` (was `...wholphin.debug`).
The old `.debug` package is uninstalled. Upstream v1.0.3
(`com.github.damontecres.wholphin`) is deliberately kept as a fallback
because it is the only build with a working MPV backend.

**There is no single build with both.** libmpv is behind the upstream
author's private Maven credential; ours links the stub. This is fine —
the ExoPlayer patch works, so MPV is no longer needed for correctness. It
was the diagnostic that proved ExoPlayer was at fault.

Note the new package has **fresh app data** — it needs signing in again
(Quick Connect is easiest) and its experimental toggles start at defaults.
The Live TV fixes now default ON, so nothing needs enabling by hand.

### Security cleanup (`2114dcd8`)
- **Removed `DebugReceiver`.** It was `android:exported="true"` with no
  permission, so *any* app on the device could broadcast
  `com.github.damontecres.wholphin.UPDATE_PREFS` and change Wholphin's
  experimental preferences. Added purely to toggle prefs over adb during
  diagnosis; no reason to ship.
- **DeviceProfile dump ERROR → verbose.** It wrote the entire client
  capability profile to logcat on every playback.
- **Kept `WHOLPHIN_DIAG` track logging** — codec/mime/channel data only,
  no tokens or credentials, and it is what makes this class of bug
  diagnosable. Audited for token logging: none found.

### Remote access
pve-01 is on the tailnet at `100.125.154.95` /
`pve-01.tail8f3e6.ts.net` — SSH there gives full capability, since all
work runs from that host. `GL-MT6000` also advertises `192.168.9.0/24`,
so with `--accept-routes` a client reaches Jellyfin (`192.168.9.50`) and
the Chromecast (`192.168.9.203`) directly.

### Still outstanding
1. **Sign in to Wholphin Media-Core and re-test ch100/102.** Expected to
   work — same binary as the build that was confirmed, plus hardening.
2. **Publish the GitHub release** (§4) to enable self-update. Blocked on a
   credential with write access to `nk-sys-ops`. APK archived at
   `/root/wholphin-backups/Wholphin-MediaCore-1.0.3-32-gf248c314-armeabi-v7a.apk`.
3. Dedicated signing keystore before relying on auto-update.

### Why `gh` cannot cut the release (diagnosed 2026-07-26)

Not a scope problem — an identity one:

```
gh authenticated as : InDaSky314   (scopes: gist, read:org, repo)
orgs visible        : Wiesbaden-Cyber, velocit-ee
nk-sys-ops          : type "User"  <- a separate personal account, not an org
repos/nk-sys-ops/wholphin -> 404
```

`InDaSky314` is simply not a collaborator on that repo. GitHub returns 404
rather than 403 for private repos you cannot see, which is why it looked
like the repo did not exist. The `github-wholphin` deploy key can push
because it is an SSH key scoped to the repo; deploy keys have no REST API
access, so they can never create releases.

Fix, any one of:

- **A** — add `InDaSky314` as a collaborator with Write from the
  `nk-sys-ops` account. Everything on pve-01 then works unchanged.
- **B** — `sudo gh auth login --hostname github.com --web` as
  `nk-sys-ops`. Both accounts coexist via `gh auth switch`.
- **C (best for automation)** — fine-grained PAT from `nk-sys-ops`, scoped
  to only `nk-sys-ops/wholphin` with **Contents: Read and write**, stored
  at `/root/.wholphin-release-token` (mode 600) and used as
  `GH_TOKEN=$(cat /root/.wholphin-release-token) gh release create …`.
  Smallest blast radius if it leaks, and works unattended.

---

## 9. Self-update is blocked: the fork is private (2026-07-26 evening)

The first release is published and correctly formed:

- tag `v1.0.3-33-g2114dcd8`, name **`1.0.3-33-g2114dcd8`**
- asset **`Wholphin-debug-armeabi-v7a.apk`**

Both names matter and were verified against the code:

- `UpdateChecker.getRelease` parses the release **name** with
  `Version.tryFromString`, which uses `matchEntire` on
  `v?(\d+)\.(\d+)\.(\d+)(-(\d+)-g([a-zA-Z0-9]+))?`. A descriptive title
  such as "v1.0.3 - Live TV fixes" does **not** parse and the update is
  silently never offered. The name must be exactly a version string.
- `getDownloadUrl(assets, BuildConfig.DEBUG)` picks assets by preference:
  for a debug build `Wholphin-debug-<abi>.apk`, then `Wholphin-debug.apk`.
  Plain **`Wholphin.apk` is only matched for non-debug builds** — an
  earlier plan to name it that would never have matched.
- Ordering works on `major.minor.patch` then `numCommits`, so
  `1.0.3-33-…` > installed `1.0.3-32-…`.

**But it cannot work as configured.** `nk-sys-ops/wholphin` is private and
`UpdateChecker` issues a plain unauthenticated OkHttp request:

```
GET https://api.github.com/repos/nk-sys-ops/wholphin/releases/latest -> 404
```

Embedding a token in the APK is not an option. Upstream's design assumes a
public repo. Consequences today: the check fails every 12 h, logs
"Update check failed 404", and is otherwise harmless — no user-visible
breakage and no wrong build installed.

### Options

- **A — make the fork public.** Zero infra, works immediately. It is a
  fork of an open-source app, so the code is not secret; check commit
  messages first.
- **B — self-host the feed on the LAN** (*recommended for privacy*).
  `updateUrl` is a normal preference, and the parser only needs JSON with
  `name` plus `assets[]` of `{name, browser_download_url}`. A small nginx
  container on CT105 serving `latest.json` + the APK would do it, reachable
  on the LAN and over Tailscale. **No web server exists on the stack yet**
  — nothing is listening on 80/443/8080 in CT105 — so this needs one
  standing up.
- **C — manual installs.** Leave it as is and `adb install -r` new builds.
  Zero work, no automation.

Nothing else in the plan changes; §4's release procedure is correct and
proven, it just needs a reachable host.

---

## 10. Repo sanitised, made public, self-update live (2026-07-26 night)

### History rewrite
All 12 of our commits were authored `root <root@pve-01.jetta.tech>` —
a real domain plus infra naming, and GitHub shows commit emails publicly.
Rewritten with `git filter-branch` to
`nk-sys-ops <nk-sys-ops@users.noreply.github.com>`, which also:

- removed `PlayerFactory.kt.pre-20260725-declfix` (an accidental editor
  backup) from every commit
- scrubbed "Media-Core"/"mediacore" from commit messages

Branding renamed **Wholphin Custom**, `applicationIdSuffix = ".custom"`,
package `com.github.damontecres.wholphin.custom`.

### Old repo deleted, not force-pushed
Force-pushing leaves the old commits as dangling objects, fetchable by SHA
until GitHub garbage-collects on no guaranteed schedule. Since the repo was
standalone (`isFork: false`) it was deleted and recreated instead, so the
old identity was never published. **Verified** by cloning the public repo
anonymously and grepping the full working tree and history:

```
jetta / pve-01 / media-core / mediacore / karras / nathan
teltv / 192.168 / tail8f3e6 / root@   ->  0 files, 0 commits
commit authors: nk-sys-ops, plus upstream's own public contributors
```

The repo is now **public**. `user.name`/`user.email` are pinned in the
repo's local git config so future commits from pve-01 cannot silently
reintroduce the old identity.

### Token scoping gotchas (both hit, both fixed)
Fine-grained tokens grant by repository **ID**, not name. Deleting and
recreating the repo left the token with "no access to any repositories" —
it had to be re-pointed at the new repo. Editing the scope keeps the same
token value; regenerating is unnecessary. Pushing then failed again with
`refusing to allow a Personal Access Token to create or update workflow`
because upstream ships `.github/workflows/main.yml`, so the token also
needs **Workflows: Read and write**.

### Self-update is now live
Release **`v1.0.3-34-g693c0e3c`**, name `1.0.3-34-g693c0e3c`, asset
`Wholphin-debug-armeabi-v7a.apk`. Verified against an unauthenticated
request (exactly what the device makes): reachable, name parses as a
Version with `numCommits: 34`, asset name matches what a debug build looks
for. §9's blocker is resolved by the repo being public.

### Device state
- `com.github.damontecres.wholphin.custom` — **Wholphin Custom**
  `1.0.3-34-g693c0e3c`, both fixes, enabled by default
- `com.github.damontecres.wholphin` — upstream v1.0.3, kept as the MPV
  fallback
- the old `.mediacore` and `.debug` packages are uninstalled

Archived: `/root/wholphin-backups/Wholphin-Custom-1.0.3-34-g693c0e3c-armeabi-v7a.apk`

**Still to do:** sign in to Wholphin Custom (new package = fresh data) and
play ch100/102 to confirm. Then optionally upstream the fixes — the
Wholphin `mediaSourceId` bug is the highest-value one, since it breaks Live
TV for every user with a tuner.

---

## 11. Where things stand at end of 2026-07-26

**Both bugs fixed, shipped, and running on the TV.**

| Repo | State |
|---|---|
| `nk-sys-ops/wholphin` | **public**, history sanitised, `main` and `fix/jellyfin-live-tv-ts-transcode` both at `693c0e3c` (identical — no merge needed) |
| branch `pr/livetv-mediasourceid` | the upstream PR candidate, cherry-picked onto upstream `f16a7bb6`, compiles clean |
| release `v1.0.3-34-g693c0e3c` | asset `Wholphin-debug-armeabi-v7a.apk`, verified reachable unauthenticated |

**On the Chromecast**

- `com.github.damontecres.wholphin.custom` — **Wholphin Custom**
  `1.0.3-34-g693c0e3c`, both fixes on by default
- `com.github.damontecres.wholphin` — upstream v1.0.3, MPV fallback

**Infrastructure left as found:** Threadfin stock (`buffer: -`, `-c copy`),
Jellyfin untouched, exported debug receiver removed, verbose profile dump
quietened.

### Open items, priority order

1. **Sign in to Wholphin Custom and play ch100/102.** New package means
   fresh app data. Expected to work — same code as the build that was
   confirmed, plus hardening — but unverified since the rename.
2. **Capture `hls_codecs_patched`** (see the upstreaming doc). Turns the
   audio fix from strong inference into a direct sighting, and unblocks the
   media3 report.
3. **Upstream the findings** — see `livetv-upstreaming-20260726.md`. The
   Wholphin PR is drafted and ready; the Jellyfin issue is the highest
   value; the media3 issue is blocked on item 2.
4. **Dedicated signing keystore** before relying on auto-update. Current
   builds are debug-signed with root's keystore on pve-01, so all future
   builds must happen on that box or upgrades will refuse to install.
5. **Optional cleanup** — the `tsDirectPlay` toggle and unconditional `ts`
   injection target a non-cause and can be reverted. Keep the
   `WHOLPHIN_DIAG` track logging; it is what made this class of bug
   diagnosable.

### Rebuild-and-release loop

```bash
cd /srv/media-core/wholphin
sudo ./gradlew assembleDefaultDebug
# name the asset exactly Wholphin-debug-armeabi-v7a.apk
# name the release exactly like 1.0.3-<n>-g<hash>  (must parse as a Version)
sudo sh -c 'export GH_TOKEN=$(cat /root/.wholphin-release-token); \
  gh release create <tag> --repo nk-sys-ops/wholphin --target main \
  --title "<version>" /path/to/Wholphin-debug-armeabi-v7a.apk'
```

Both names are load-bearing — get either wrong and the updater silently
does nothing. Details and the reasoning are in §9.

---

## 12. MPV settings archive (upstream build uninstalled 2026-07-29)

The upstream Wholphin build (`com.github.damontecres.wholphin`) was
uninstalled to free storage — the Chromecast was at 93% (316 MB free of
4 GB). These settings took several iterations to arrive at and are
recorded here because they lived in that app's private data.

**Playback Backend:** MPV
**MPV: Use hardware decoding:** ON
**MPV: Use gpu-next:** OFF

**mpv.conf as last applied** (Settings → Advanced Settings → Edit mpv.conf):

```
hwdec=mediacodec-copy
framedrop=vo
video-sync=audio
cache=yes
demuxer-max-bytes=32MiB
demuxer-readahead-secs=10
```

Reasoning, so it is not re-derived:

- `hwdec=mediacodec-copy` — the default hardware path renders straight to
  a surface, which puts the video pipeline outside mpv's clock and causes
  progressive A/V drift on Amlogic. Copy mode brings frames back under
  mpv's timing. **This is the line that produced the audible improvement.**
- `framedrop=vo` — drop late frames to hold sync rather than drifting.
- Cache values are deliberately modest. An earlier attempt used
  `demuxer-max-bytes=64MiB`, `demuxer-readahead-secs=20`, `cache-secs=30`
  and was *worse* — on a 32-bit device the larger buffer adds memory
  pressure and latency without helping sync.

**Recommended but never tested** (next thing to try if MPV is revisited):

```
profile=fast
vo=gpu-next
```

`profile=fast` disables mpv's expensive default scaling, dithering and
debanding, which is where a weak Mali GPU spends its budget — likely the
bigger lever of the two. `vo=gpu-next` is the libplacebo renderer with
better frame timing. Also untested: `hwdec=no`, which is the *diagnostic*
that would confirm whether hwdec is the jitter source at all (if jitter
disappears, hwdec is the culprit; if 1080p becomes a slideshow, the CPU
cannot software-decode and hwdec must be made to work).

**Status when uninstalled:** MPV played the affected channels *with audio*
— which is what proved the bug was ExoPlayer-side — but was never as smooth
as ExoPlayer on this hardware. With the ExoPlayer AAC fix working, MPV is
no longer needed for correctness. Reinstall from
`https://github.com/damontecres/Wholphin/releases` (armeabi-v7a) if it is
ever wanted again.

---

## 13. Working settings, and the AC3 trap (2026-07-29)

### Known-good configuration — audio works on ALL channels

Wholphin Custom (`com.github.damontecres.wholphin.custom`), Advanced Settings:

| Setting | Value |
|---|---|
| Device supports AC3/Dolby Digital | **Disabled** |
| Always downmix to stereo | Disabled |
| Direct play Dolby Vision Profile 7 | Disabled |
| AV1 software decoding | Disabled |
| Experimental settings | **Enabled** |

Experimental settings:

| Setting | Value |
|---|---|
| Video tunneling | Disabled |
| **IPTV Audio Track Recovery** | **Enabled** ← carries the AAC fix; required |
| **Use AC3 for surround sound audio** | **Disabled** ← required, see below |
| Direct play TS | Enabled |

### The AC3 trap

With **"Use AC3 for surround sound audio" enabled**, audio failed on some
channels with:

```
MediaCodecAudioRenderer error, format=Format(..., audio/ac3, ...),
format_supported=NO_UNSUPPORTED_SUBTYPE
Decoder init failed: [-49999]   (ERROR_CODE_DECODER_INIT_FAILED)
```

Cause, confirmed in `DeviceProfileUtils.createDeviceProfile`:

```kotlin
if (preferAc3ForSurround) {
    transcodingProfile {
        ...
        audioCodec(Codec.Audio.AC3)   // ONLY AC3 — no AAC fallback
    }
}
```

The flag makes the video transcoding profile advertise **AC3 as the only
acceptable audio codec**, so Jellyfin transcodes every channel's audio to
AC3. This device has **no AC3 decoder** (none registered in
`dumpsys media.player`) and **our build has no software fallback** — the
media3 ffmpeg decoder extension (`libavcodec` etc.) is behind the private
Maven credential, so our APK ships only `libass`. Upstream's build has it,
which is why AC3 channels worked there.

Note this is a *different* setting from "Device supports AC3/Dolby
Digital" (`ac3Supported`), which only adds AC3 to the supported list and
can stay enabled.

**Latent risk closed (2026-07-29):** "Device supports AC3/Dolby Digital"
was also disabled. With `ac3Supported = false`, AC3 and EAC3 are filtered
out of both the direct-play and transcoding profiles, so Jellyfin can
never hand this client AC3 at all — it transcodes to AAC, which the device
decodes natively. Correct for this hardware, which has no AC3 decoder and
no ffmpeg software fallback in our build.

Re-enable both settings only if the TV is ever fed through a receiver that
does Dolby Digital passthrough, or if the build gains the media3 ffmpeg
decoder extension. Until then the cost is that multichannel audio is
transcoded to AAC rather than passed through as AC3 — inaudible on stereo
TV speakers, relevant only with a surround setup.

### Correction to earlier advice

§12 said dropping the upstream build was safe because "ExoPlayer is
confirmed working". That was reasoned from channels 100/102 only, which
are AAC. Our build is **strictly less capable than upstream** for AC3 and
AV1 content because it lacks the decoder extensions. Removing upstream
gave up a genuine fallback.

### Outstanding cleanup

`WholphinMediaSourceFactory` sets `setAllowChunklessPreparation(false)`.
That was added speculatively, is not needed for the AAC fix, and changes
preparation behaviour for **every** HLS stream. It did not cause the AC3
failure, but it is an unreviewed change and should be removed.

---

## 14. NextPVR A/B experiment — three Jellyfin bugs isolated (2026-07-29 evening)

Both Threadfin and NextPVR serve **the same two channels from the same
upstream URLs** (`cf.teltv.xyz/live/.../430234.ts` for ch100), reaching
Jellyfin by two completely different code paths. That makes a controlled
A/B possible on one server, and it paid off.

Jellyfin has duplicate channel items — same name, same `SortName`
(`00100.0-Madison: ABC 27 (WKOW)`), so they appear as indistinguishable
twins in the guide:

| Item id | ExternalId | Source |
|---|---|---|
| `dedb9f8f-6d3d-29a1-f404-614b84546247` | `7148` | NextPVR (plugin) |
| `52dd5eb0-f5f0-3933-0245-5b8ae619cad8` | `hdhr_100` | Threadfin (tuner host) |

**Finding the twin:** the NextPVR copy has no EPG data, so a guide
*view-option filter* hides it. Enable "show details" in the guide's filter
menu to reveal it. (An earlier guess that it appears at channel 103 was
wrong — 103 is `channel_source_number`, internal to NextPVR.)

### Bug 1 — tuner-host probe misses audio (now localised)

Same channel, same stream, same server, minutes apart:

| | Threadfin (TunerHost) | NextPVR (ILiveTvService) |
|---|---|---|
| probe result | video only | **`h264` + `aac` detected** |
| ffmpeg audio | *(no `-codec:a`)* | **`-codec:a:0 copy`** |
| client patch needed | yes | **no** (`hls_codecs_patched` never fired) |

This rules out the stream, the codec, the provider and ffprobe itself. The
defect is in the **HDHomeRun tuner-host media-source path**, not shared
probe code. That is a far stronger issue report than "the probe sometimes
fails".

### Bug 2 — DirectPlay of a plugin-sourced live channel returns HTTP 500

```
System.FormatException: Unrecognized Guid format
   at System.Guid.Parse(String input)
   at Jellyfin.Api.Helpers.StreamingHelpers.GetStreamingState(...)
   at Jellyfin.Api.Controllers.VideosController.GetVideoStream(...)
```

The NextPVR MediaSource Id is **`"34"`** — a bare integer. Jellyfin
generates it, then rejects it when handed back to `/Videos/{id}/stream`.
Client sees `ERROR_CODE_IO_BAD_HTTP_STATUS, Response code: 500`, retries,
then falls back to Transcode — observed as **buffering/stalling** on the
ch102 twin.

**The two bugs mask each other.** Threadfin never hits Bug 2 because its
probe fails, so DirectPlay is never chosen. Fixing Bug 1 *exposes* Bug 2.

Workaround if NextPVR channels are wanted now: disable **Direct play TS**
in Experimental Settings, forcing transcode instead of the broken direct
path. Untested.

### Bug 3 — username whitespace (unrelated, but real)

Username/password login fails on the Chromecast and Android phone apps;
Quick Connect works. Cause:

```
DB username:       'family'    (len 6, no spaces)
client transmits:  "family "   <- trailing space
log:               Authentication request for "family " has been denied
```

Android's keyboard word-suggestion appends a space. Jellyfin does not trim,
so the user lookup finds nothing. `InvalidLoginAttemptCount` stayed **0**
for `family` because the request never resolves to that account — which is
why it looked like nothing was reaching the server. Web works because
browsers do not auto-space.

Fix: backspace before leaving the username field, or disable predictive
text. Arguably Jellyfin should trim — an easy, reportable upstream bug.

---

## 15. OPEN — recordings playback failure (undiagnosed)

Reported 2026-07-29 late: **recordings will not play back**. No evidence
captured — the logcat capture had ended and the server log window had
rolled past it. **Do not speculate; reproduce and capture.**

Plausible-but-unverified: NextPVR recordings arrive through the same
plugin, so they may carry the same non-GUID media source ids as Bug 2 and
fail identically. That is a hypothesis only.

To capture next time — start the capture **before** pressing play:

```bash
adb -s 192.168.9.203:5555 logcat -c
adb -s 192.168.9.203:5555 logcat -v time > /tmp/rec.log &
# play a recording, then:
grep -aE "playback_error|Response code|Playback decision|WHOLPHIN_DIAG" /tmp/rec.log
sudo /usr/sbin/pct exec 105 -- docker logs jellyfin --since 5m 2>&1 \
  | grep -B2 -A6 "Unrecognized Guid format\|Error processing request"
```

Note whether the recording is a **NextPVR** recording (Recordings tab) or a
Jellyfin DVR one — they take different paths.

---

## 16. Pick-up list

1. **Diagnose recordings playback** (§15) — capture first, then diagnose.
2. **Draft the Jellyfin issues.** Bug 2 is the most reportable: clean stack
   trace, one-line cause, no dependence on this setup. Bug 1 has the
   strongest evidence. Bug 3 is trivial and easy to land.
3. **Submit the Wholphin PR** — branch `pr/livetv-mediasourceid`, drafted in
   `livetv-upstreaming-20260726.md`, compiles against upstream. Needs a
   fork of `damontecres/Wholphin` under `nk-sys-ops`.
4. **media3 issue** — now unblocked: the real codecs string was captured,
   `avc1.640028` (video-only, not empty).
5. **Remove `setAllowChunklessPreparation(false)`** from
   `WholphinMediaSourceFactory` — added speculatively, affects all HLS.
6. **Dedicated signing keystore** before relying on auto-update.
