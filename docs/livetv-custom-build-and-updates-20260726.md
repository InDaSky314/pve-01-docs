# Live TV: state, custom build, and the update plan

Updated **2026-07-31**. Supersedes `livetv-audio-rootcause-20260726.md`
for *status*; that doc remains the reference for the investigation and
the ruled-out list.

---

## START HERE — current state (2026-08-09)

This doc grew by accretion and several sections each read as "the
current state". They are not. Read this section, then §18. Everything
between is history, useful for *why* but not for *what is true now*.

**Everything works, including in-progress recording playback now**:
recordings play (finished and in-progress), Live TV plays on both the
Threadfin and NextPVR copies of ch100/102, both have guide data,
recordings can be deleted from the TV, and DVR Schedule / the
Recordings library both load correctly. See `handoff-20260809.md` for
that day's full narrative.

| Section | Status |
|---|---|
| §1–§11 | history — the audio + `NoCompatibleStream` investigation |
| §12 | **superseded by §13** — do not act on its "safe to drop upstream" advice |
| §13 | settings still broadly correct, but see §17.2 on Direct play TS |
| §14 | still accurate; Bug 2 is still live on Jellyfin 10.11.9 |
| §15, §16 | **the recordings hypothesis in these sections is disproved** — see §17.1 |
| §16 ADB note | **wrong** — see §17.6, rediscovery is trivial and now automated |
| §17 | four new bugs, the NextPVR guide chain, the noon schedule, power management (2026-07-30/31) |
| §18 | current: NoCompatibleStream fixed for in-progress recordings, and five real bugs in DVR Schedule / the Recordings library, all found live against real household data (2026-08-09) |

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

> **SUPERSEDED BY §13.** The reasoning below for dropping the upstream
> build ("ExoPlayer is confirmed working") was drawn from ch100/102
> only, which are AAC. Our build is strictly *less* capable than
> upstream for AC3 and AV1. The MPV settings archived here are still
> accurate and worth keeping; the removal advice is not.

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

## 15. ~~OPEN~~ RESOLVED — recordings playback failure

> **The hypothesis in this section is disproved.** Recordings were not
> failing via Bug 2. The cause was a client-side crash in Wholphin's
> track sorting — `NumberFormatException: "1/257"`. Fixed in `2f38f828`.
> Full diagnosis in §17.1. The `PlayMethod=DirectPlay` data point below
> was a coincidence, not evidence. Kept for the record.

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

### §15 addendum — recordings: one data point, still undiagnosed

Attempted reproduction 2026-07-30 ~18:12 ("Space Chase USA"). Captured
before the device was restarted:

```
PlayMethod=DirectPlay      <- and nothing else; no error in the window
```

That is *consistent with* the Bug 2 hypothesis (DirectPlay → non-GUID
media source id → `Guid.Parse` → HTTP 500), because recordings take the
same DirectPlay route that failed on the NextPVR live channel. **It is not
confirmation** — no `Unrecognized Guid format` was captured for a
recording. Reproduce and capture before treating it as the same bug.

### ADB access after a reboot — read this first next time

> **CORRECTED 2026-07-31 — the advice below is wrong.** Port-scanning
> works fine, and mDNS is better still. Neither needs anyone at the TV.
> ADB reconnection is now automated. See §17.6.

Wireless debugging does not survive a reboot, and on Android 11+ the
**connect port rotates every time it is toggled**. Pairing worked
repeatedly (`adb pair <ip>:<pair-port> <code>` → "Successfully paired")
while `adb connect` kept failing on ports found by scanning, leaving the
device stuck `offline`. Port-scanning to guess the connect port did not
work — several ports are open simultaneously and only one is correct.

Take the connect port from the **main Wireless debugging screen** (not the
pairing dialog), and expect it to differ from the pairing port. If the
device shows `offline`, `adb kill-server` then reconnect.

**Do not block diagnosis on ADB.** Both live-TV bugs were found entirely
in Jellyfin's server log, which is readable without touching the device
and survives device reboots.

### Device sluggishness after mass app updates

After updating all apps via Play Store and rebooting, the Chromecast became
very slow. Ping from pve-01 showed `min/avg/max = 1.5/32/93 ms` on the LAN
with 43 ms mdev — the device was too busy to answer ICMP promptly, i.e.
CPU/IO starved rather than a network fault. Cause is almost certainly
background `dexopt` recompiling updated apps, made worse by storage sitting
at ~85%. It settles on its own in 20-45 minutes; leave it idle.

---

## 17. Four bugs, the NextPVR guide chain, and the noon schedule (2026-07-30/31)

Everything in this section is verified against logs, not inferred.
Commits `282a4eae`, `2f38f828`, `9cff6a76`, `0e3e89ca` are pushed to
`nk-sys-ops/wholphin` (`main` and `fix/jellyfin-live-tv-ts-transcode`).

### 17.1 Bug 4 — recordings crashed the app on a TS track id

**This is what §15 was actually about.** Playing any Jellyfin DVR
recording killed the process:

```
FATAL EXCEPTION: main
java.lang.NumberFormatException: For input string: "1/257"
  at TrackSelectionUtils.sortedById(TrackSelectionUtils.kt:162)
  at PlaybackViewModel$changeStreams$…onTracksChanged
  at androidx.media3.exoplayer.ExoPlayerImpl.updatePlaybackInfo
```

`sortedById` assumed `Format.id` is always Jellyfin's
`"<source>:<stream>"`. On **direct play of a raw container** the id comes
from ExoPlayer's own extractor instead: `TsExtractor` emits
`"<program>/<pid>"`, and `1/257` is program 1, PID 0x101 — a textbook TS
video PID. `split(":")` yields one element, `toInt()` throws, and because
this runs on `onTracksChanged` on the main looper it is an uncaught
exception, so the process dies rather than playback failing.

Fixed in `2f38f828`: accept `:` or `/`, use `toIntOrNull`, pad both sort
slots. A single-element id would also have thrown
`IndexOutOfBoundsException` in the comparator — that latent second crash
is closed too.

**It was never Bug 2.** The media source id was
`a1a47eb7ba6abff7984f235e124c0fa1` — a well-formed GUID. And there are no
NextPVR recordings on this system at all, so the plugin path was never
involved. Both tracks probed fine (H.264 + AAC-LC stereo English); the
app crashed *after* a correct probe.

Upstream-worthy: this hits any user direct-playing DVR recordings.

### 17.2 Bug 5 — "Direct play TS" was a dead switch

The setting wrote its preference correctly and did nothing. Two separate
faults, found a day apart:

1. `DeviceProfileService` hardcoded `tsDirectPlay = true` instead of
   reading the preference — fixed in `2f38f828`.
2. `createDeviceProfile` **accepted the parameter and never used it**.
   `tsDirectPlay` appeared only in the signature; line 214 did
   `containers.add(Codec.Container.TS)` unconditionally — fixed in
   `0e3e89ca`.

Fixing (1) without (2) is worthless, and cost a diagnostic cycle: the
toggle was flipped, the profile still advertised `ts`, and the observed
behaviour contradicted the setting. **If a setting appears inert, trace
it all the way to its point of use before trusting either half.**

§13's table says Direct play TS = Enabled. That was recorded while the
switch did nothing, so it describes no real configuration. It is now a
live setting — see §17.3 for what it actually controls.

### 17.3 Bug 6 — client direct-plays against the server's decision

Jellyfin evaluates the request and returns a `PlayMethod`. Wholphin
ignores it, reading `MediaSourceInfo.SupportsDirectPlay` — which live-TV
sources set to `true` unconditionally — instead. Captured on the NextPVR
ch100 twin:

```
server 21:20:34  DirectPlay Result: PlayMethod: null,
                 Reasons: ContainerNotSupported, VideoCodecNotSupported
server 21:20:34  Transcode Result:  PlayMethod: Transcode  →  master.m3u8
client 21:20:38  Playback decision for dedb9f8f…: DirectPlay
```

The client then requests `/Videos/{id}/stream` with `MediaSourceId: "25"`
— a bare integer — which is exactly the value that trips Bug 2's
`Guid.Parse`. Result: HTTP 500, and the player hangs on a blank surface
with no error logged.

Why the server refused DirectPlay is also visible: at decision time the
probe had not run (`Waiting 3000ms before probing the live stream`), so
every `MediaStream` had `Codec: null`.

**Not fixed.** The workaround is the now-functional Direct play TS
toggle: with `ts` absent from the direct-play container list the client's
own check fails and it uses the transcode URL. Honouring the server's
`PlayMethod` is the real fix and the best upstream candidate of the
three — it explains a whole class of live-TV hangs.

### 17.4 Bug 7 — display mode switch destroys the activity

Presented as "it crashed, white screen". **It is not a crash** — zero
`FATAL EXCEPTION` across 34k log lines. Wholphin requests a display mode
change to match the stream, Android treats it as a configuration change,
and `MainActivity` is destroyed and rebuilt "without playback":

```
21:32:03.486  Found display mode: modeId=17, 1280x720@59.94  | current=3840x…
21:32:03.490  Switch preferredDisplayModeId to 17
21:32:04.284  onPause      21:32:04.482  onDestroy
21:32:04.552  onCreate     21:32:04.574  Restoring back stack without playback
```

100% correlation across every attempt captured:

| Stream | Mode | Result |
|---|---|---|
| NextPVR copy | 17 = 1280x720 **@59.94** | activity destroyed |
| Threadfin copy | 14 = 1920x1080 @25.0 | `Waiting for non-seamless switch` → plays |

Both twins carry the same upstream stream; they differ only because
Jellyfin's two paths report different resolution/framerate, and
720p59.94 is a mode this TV cannot switch to without a teardown. That is
why it looked channel-specific and sent the diagnosis chasing NextPVR.

**Workaround (applied): Advanced Settings → Playback → disable both
"Resolution switching" and "Refresh rate switching".** Verified after:
zero mode switches, zero `onDestroy`, sustained `PLAYING`.

Cost: no refresh-rate matching, so 25 fps content may judder slightly.
The real fix is client-side — declare the relevant `configChanges` or
persist playback across recreation.

### 17.5 NextPVR guide data — the full chain

Channels 7148 (ch100) / 7149 (ch102) had no guide, so nothing could be
recorded. Four separate problems, in the order they were hit:

1. **The configured XMLTV path did not exist.**
   `IPTV_RECORDER.xmltv_url = /epg/threadfin.xml`. Wrong twice: `/epg` is
   mounted into the **Jellyfin** container, not NextPVR's (which gets
   only `/config` and `/buffer`), and `threadfin.xml` does not exist on
   disk under any mount. NextPVR failed silently.
2. **Threadfin's XMLTV is not the guide source.**
   `http://localhost:34400/xmltv/threadfin.xml` has `<channel>` entries
   for both channels but **zero `<programme>` entries**. The real guide is
   `/srv/media-core/epg/epg.xml` (generated by `xtream-sync.py`), whose
   channel ids `mc430234` / `mc429409` match the M3U's `tvg-id` exactly.
3. **A trailing space in the path.** The UI stored
   `<XmltvSources>/config/epg.xml </XmltvSources>`, so `File.Exists`
   failed and the update reported `[0 inserted, 0 updated, 0 skipped]`.
   Same class as §14's Bug 3 username whitespace. **Check for trailing
   whitespace with `cat -A` before believing any path is correct.**
4. **The source id is derived from the path.** Removing the trailing
   space changed it from `xmltv--147538803` to `xmltv-303203766`, and the
   per-channel mapping ids changed with it.

**Do the channel mapping in the UI, not the database.** Direct DB writes
to `CHANNEL.epg_source` / `epg_mapping` do not work — the API's
`setting.epg.sources` handle is not the stored value. The real schema is:

```
epg_source  = XMLTV                       ← literal string
epg_mapping = <epg><source>XMLTV</source>
                <file>/config/epg.xml</file>
                <mapping_id>mc430234</mapping_id>
                <mapping_name>Madison: ABC 27 (WKOW)</mapping_name></epg>
```

Settings → EPG → **Auto Map** does this correctly in seconds. The API
route is blocked anyway: a PIN session returns `allow_settings=false`, so
`setting.epg.automap` fails with `Not Allowed` (code 13).

Result: 230 `EPG_EVENT` rows; Jellyfin ingested 78 programmes for WKOW
and 86 for WMSN.

**Staleness fix.** `epg.xml` is regenerated daily and NextPVR cannot read
its directory, so a drop-in copies it after every sync:

```
/etc/systemd/system/media-core-sync.service.d/nextpvr-epg-copy.conf
  ExecStartPost=/bin/cp /srv/media-core/epg/epg.xml \
                        /srv/media-core/nextpvr/config/epg.xml
```

**Consequence to expect:** these channels are duplicates of the Threadfin
ones, previously hidden only because they lacked EPG. With a guide they
now appear as indistinguishable twins.

### 17.6 ADB reconnection is automated

§16's claim that port-scanning "did not work" is wrong. The device
advertises its current port over mDNS and the pairing key persists, so
rediscovery is all that is needed — no re-pairing, nobody at the TV.
`adb mdns services` does not see it on this host, but avahi does.

```
/usr/local/bin/chromecast-adb-keepalive          (script)
/etc/systemd/system/chromecast-adb-keepalive.{service,timer}
timer: every 2 min, plus 2 min after boot
```

If the link is up it exits in ~20 ms; otherwise it queries
`_adb-tls-connect._tcp` and reconnects. Verified through the systemd path
— the device comes back `device`, not `unauthorized`, so root's
`~/.android` key resolves correctly under systemd. Only wireless
debugging being switched off on the device defeats it.

Manual fallback, if ever needed:
```bash
nmap -Pn -p 30000-49999 --open -T4 <device-ip>     # 3 ports appear
adb connect <device-ip>:<port>                     # exactly one succeeds
```

### 17.7 Delete recordings — no patch needed

Wholphin already handles `BaseItemKind.RECORDING` in its delete path. It
is hidden because `AppPreference.ManageMedia` defaults to false.

**Settings → Advanced Settings → Interface → "Show media management
options"** (it is in `advancedPreferences`, *not* the basic Interface
screen). The `family` user already has `EnableLiveTvManagement` and
`EnableContentDeletion`.

### 17.8 Scheduling moved to noon (power timer)

The host is on a power timer and typically off overnight, so the whole
04:00 media cascade either never ran or all fired at once at power-on.
Moved to midday, preserving dependency order:

| Time (CEST) | Job | Was |
|---|---|---|
| 12:00 | `media-core-sync` — playlist, EPG, VOD | 04:00 |
| 12:25 | `media-core-xepg` — Threadfin XEPG | 04:25 |
| 12:35 | NextPVR EPG update | 02:44 |
| 12:50 | Jellyfin *Refresh Guide* | 04:30 |
| 13:05 | Jellyfin *Scan Media Library* | 04:45 |

`media-core-ppv` now skips hour 12 instead of hour 04.

This ordering is better than the original: sync writes `epg.xml` and the
drop-in copies it to NextPVR, *then* NextPVR ingests at 12:35, *then*
Jellyfin pulls from both tuners. Previously NextPVR loaded at 02:44,
before the 04:00 sync, so it always ingested the previous day's file.

**`Persistent=true` vs Jellyfin's `DailyTrigger`.** The systemd timers
catch up missed runs at boot; **Jellyfin's daily triggers do not**. Every
day the box was off at 04:30, Refresh Guide and Scan Media Library simply
did not run. That is the likeliest explanation for guide data repeatedly
looking stale.

Implementation: systemd drop-ins named `noon.conf` (revert = delete +
`daemon-reload`); NextPVR `config.xml` `EPGUpdateHour`/`EPGUpdateTime`
(needs the container stopped, or NextPVR rewrites it on shutdown);
Jellyfin `config/config/ScheduledTasks/<id>.js` `TimeOfDayTicks`
(ticks = seconds since midnight x 10,000,000; needs Jellyfin stopped).
Backups alongside each as `.bak-sched-*`.

Host-side jobs were moved too, once the power window was known
(**on ~04:57, off ~22:24**):

| Time | Job | Was |
|---|---|---|
| 13:30 | `media-core-config-backup` | 03:30 |
| 14:00 | `pve-daily-update` | 04:59 |
| Sun 09:00 | `router-backup` | Sun 03:30 |
| Sun 09:30 | `vzdump` (CT105 + VM102, via `pvesh`, not a jobs.cfg edit) | Sun 02:00 |
| Sun 11:00 | `xfs_scrub_all` | Sun 03:10 |

Sunday timings are not arbitrary: 07:00 local is 01:00 ET, which can still
be inside a late Saturday college game on nights the mains timer is
overridden. Everything heavy now starts after 08:30.

**Why noon is the right slot, having checked.** 12:00 CET = 06:00 ET, the
only dead zone in the US sporting day. Early morning is worse, not better
— that is when overnight games *end* (an SNF recording runs to ~06:05
CET), so maintenance there would collide precisely on the nights the timer
was overridden. Evening is worse still: 19:00 CET is the early Sunday NFL
slate.

`logrotate` (00:00) still falls in the gap. It is `Persistent=yes` so it
catches up at boot; left alone rather than churn a stock Debian unit.

### 17.9 Power management: BIOS, the reminder, the shutdown guard

**BIOS is set to "Power On" — verified, not assumed (2026-07-31).**

This mattered and was nearly missed. Every prior boot followed an
*unclean* mains cut, where the last power state was "on" — so **Power On**
and **Last State** both restart the machine and the logs cannot tell them
apart. The test that distinguishes them is a *clean* shutdown, which
leaves the last state "off":

```
20:17:44  systemd-shutdown: Syncing filesystems … Journal stopped   (clean)
20:21:02  kernel start                                             (unattended)
```

Under *Last State* it would have stayed dark. It did not. **Do not infer
AC-restore behaviour from recovery after an unclean cut.**

There is no remote path to the BIOS on this board: no BMC (`dmidecode`
type 38 empty), no `/dev/ipmi0`, all DMI fields read `Default string`.
`OsIndicationsSupported = 0x03` (bit 0 set), so
`systemctl reboot --firmware-setup` does boot straight into setup — but a
monitor is still needed to see it. A KVM-over-IP dongle is the durable fix
if this comes up again.

**The router shares the same timer.** This is the right way round and
worth preserving: streams come from the provider over the internet through
the router's VPN, so server-on + router-off records nothing. One timer
means overriding it covers both. Verified across a full power cycle — both
tunnels (`wgclient1`, `ovpnclient1`) re-established unaided, Tailscale
reconnected, clock synced 60 s in to a 1 ms offset. Consequence: **no
remote access at all between 22:24 and 04:57**, since Tailscale dies with
the router.

**`/usr/local/bin/dvr-power-reminder`** (timer 09:00 daily,
`Persistent=true`). Mails when something worth recording falls in the
power-off gap; silent otherwise, so a mail means something. Sources:
ESPN's public site API (no key) for Packers, Badgers football, Badgers
basketball, Bucks; NextPVR `recording.list`; Jellyfin
`data/livetv/timers.json` read straight off disk via `pct exec`, so there
is no API key to store. 48-hour lookahead. Timezone conversion is real
(`zoneinfo`), not a fixed offset — verified against the late-Oct/early-Nov
week where the US has not yet fallen back and the gap is 5 hours, not 6.

**`/usr/local/bin/dvr-clean-shutdown`** (timer 22:10, deliberately
**not** `Persistent`, so a missed run can never fire a shutdown at an
arbitrary later time such as just after a morning boot). Checks both DVRs
for a recording in progress or starting before 04:57. Nothing due → clean
`shutdown -h +2`. Something due → stays up and mails. **If it cannot reach
a DVR it treats that as a reason to stay up**: a box left running costs one
unclean cut, a box shut down over an unseen recording costs the recording.

Two things it deliberately does not do: it **cannot hold the mains timer
open** (if a recording is due and the physical timer is still armed, power
dies at 22:24 regardless — the 09:00 mail is the real mechanism), and it
does not protect **active playback**, only recordings.

**Which slot the sport actually fits.** Against 04:57–22:24, with
ET = CET+6:

| NFL slot | ET | CET | Fits? |
|---|---|---|---|
| International | 9:30 AM | 15:30 | yes |
| Early Sunday slate | 1:00 PM | 19:00 | **no** — runs to ~22:45 |
| Late Sunday slate | 4:05/4:25 PM | 22:05/22:25 | no |
| TNF / SNF / MNF | 8:15/8:20 PM | 02:15/02:20 | no |

NBA is worse: typical tip-offs 7:00–10:30 PM ET land at 01:00–04:30 CET.
Only weekend afternoon games fit. A run against real 2026 data flagged
**17 games** in the season needing the timer left on. There is no timer
setting that covers US prime time — those games *end* around 05:30–06:00
CET, after the 04:57 power-on — so the box simply has to stay up.

**Keepalive exit codes.** `chromecast-adb-keepalive` originally exited 1
when the TV was not advertising over mDNS. The TV shares the power timer,
so that marked the unit failed every night and would have buried a real
failure. It now exits 0 for "device absent" and reserves non-zero for
genuine faults.

### 17.10 Method notes worth keeping

- **Capture before pressing play.** Every diagnosis here came from a
  logcat started beforehand. Two capture windows closed empty because
  they were too short — use an hour, not fifteen minutes.
- **"It crashed" is a symptom, not a diagnosis.** Of the failures
  reported that way, one was a real crash (§17.1), one was an activity
  teardown (§17.4), and one was a silent hang (§17.3).
- **A/B against the working twin.** Threadfin vs NextPVR copies of the
  same channel isolated §17.4 in one comparison.
- **Trace settings to their point of use** (§17.2), and **check paths
  with `cat -A`** (§17.5).

### 17.11 Open items

1. **Upstream the three client bugs** — §17.3 is the highest value,
   then §17.1, then §17.4. The Wholphin `mediaSourceId` PR
   (`pr/livetv-mediasourceid`) is still drafted and unsubmitted.
2. **Jellyfin issues** — Bug 2 (§14) is still live on 10.11.9, 8
   occurrences in 30 minutes. Cleanest report of the set.
3. **Cut a release** for `1.0.3-38-g0e3e89ca`. The device runs it but the
   published release is still `-34-`, so self-update advertises an older
   build. Asset must be `Wholphin-debug-armeabi-v7a.apk` and the release
   name exactly a version string (§9).
4. **Dedicated signing keystore** — unchanged from §16.
5. **Move the remaining overnight jobs** (§17.8).
6. **Remove `setAllowChunklessPreparation(false)`** — still outstanding
   from §13.

### 17.12 Dashboard and where the automation lives

**`/usr/local/bin/dvr-dashboard`** — one responsive page plus a JSON API,
stdlib only (no framework, no build step). Reachable on the LAN at
`http://192.168.9.11:8099/` and over Tailscale at
`http://100.125.154.95:8099/`.

Shows upcoming games (10-day horizon, ESPN cached 30 min so page loads do
not hammer the API), scheduled recordings from **both** DVRs with a
RECORDING badge for anything live, and flags whatever falls outside the
power window. The **Keep awake tonight** button writes
`/var/lib/dvr-dashboard/override-until`, which `dvr-clean-shutdown` checks
before anything else.

The page states plainly that the button **does not hold the mains timer
open** — that is a physical switch. Without saying so the control implies a
power it does not have.

**Schedules tab.** Full season per team (ESPN, cached 6 h), grouped by
month with a team filter; past games dim and carry the score. Each game
shows whether a DVR timer overlaps its slot. **The match is time-overlap
only** and the pill carries the recording's name: neither DVR records "the
Packers game", it records a channel for a span, so the honest thing is to
report the overlap rather than assert coverage. Name-matching would produce
confident wrong answers.

**Shut down now** (`POST /api/shutdown`) is two-step: the first tap arms
and explains, the second acts, and it disarms itself after 15 s. It returns
**409** if a recording is live or starts within 2 h, or if a DVR could not
be reached, naming what it found and offering an explicit override. The
warning is precise about recovery: the box returns on its own only when the
wall timer next restores power (~04:57) — if the timer has been switched to
always-on, it must be powered on by hand.

**Cancelling an override after 22:10 re-runs the guard.** Otherwise
"cancel" would do nothing until the following night, since the timer fired
hours earlier. The response carries `rechecked: true` and the page says so.

No authentication: LAN and tailnet only, and the worst an unauthorised
press can do is leave the server switched on — though note the shutdown
button raises the stakes slightly, so this is the point at which basic auth
starts to be worth adding.

**Everything now lives in `scripts/` in this repo** — the four scripts,
their unit files, and every timer drop-in for both host and CT105. Before
this they existed only in `/usr/local/bin` and `/etc/systemd/system`, so a
dead boot disk would have taken them with it. Copies here are the source of
truth; re-deploy with `install -m 755 scripts/<name> /usr/local/bin/`.

## 18. NoCompatibleStream fixed for in-progress recordings, and five DVR Schedule / Recordings bugs (2026-08-09)

Full session narrative in `handoff-20260809.md`. This section is the technical
reference for the bug family itself, for whoever hits something in this area next.

### 18.1 NoCompatibleStream still fired for the one case it was meant to fix

The original `PlaybackViewModel.kt` fix (this doc's §1, Bug A) checked
`item.type == TV_CHANNEL || mediaSource.isInfiniteStream`. `isInfiniteStream` is
**only ever `true` for a live-tuner Channel source, never for a Recording, in-progress
or not** — confirmed via direct `GET /Items/{id}` comparison of an in-progress vs
finished Recording. So the fix never covered the actual "watch recording in progress"
path added later (§17's `ProgramDialog.kt` addition). Added a third condition,
`isInProgressRecording`: `item.type == RECORDING` and (`status == "InProgress"` or
`timerId != null` or `runTimeTicks == null` — unset while still growing, populated
once finalized). Commit `5d55fae2`.

### 18.2 Recordings library hang: `EnableUserData=true` is pathological for this one folder

`CollectionFolderView.kt`'s `createGetItemsRequest` (used by every library browse
screen) never set `enableUserData` explicitly, so it inherited the API default
(`true`). For the Recordings folder specifically — `CollectionType == null`, unlike
every typed `movies`/`tvshows` library — this triggers an expensive recursive
`GetUnplayedItemCount` evaluation server-side against the household's full 550k+
item database. Measured directly: **40.67s with it on, 16ms with it off**, for a
folder with 10 actual items in it. Not query complexity (stripped to bare
`?ParentId=X`, still hung), not a real slow SQL query (the folder has zero indexed
child rows — `SELECT COUNT(*) FROM BaseItems WHERE ParentId = '<recordings-guid>'`
returns 0), not host load (reproduced identically at load average 8.5 and 47).
Fixed by explicitly setting `enableUserData = false` when `collectionType == null`
— scoped narrowly so typed libraries keep their watched/favorite indicators. Commit
`ea7c7b14`.

### 18.3 DvrSchedule.kt crashed the whole screen on any ad-hoc timer

`DvrScheduleViewModel.init()` force-unwrapped `timer.programInfo!!` when building the
scheduled list. Any Timer created by channel+time rather than by EPG `ProgramId` —
which is *every* timer this household's own `sports-dvr-auto` and the dashboard's
Record button create, both deliberately — has `programInfo: null`. One bad timer
crashed the list for every timer, not just itself. Confirmed live: real Brewers and
Packers timers both had `ProgramInfo: null`. Fixed with a fallback `BaseItemDto`
built from the Timer's own fields (`name`/`channelId`/`startDate`/`endDate`, plus
`timerId` — see 18.4) via `it.id?.toUUID()` (note: `TimerInfoDto.id` is a bare
32-char hex string, not dashed — `UUID.fromString()` throws on it, use the SDK's
`org.jellyfin.sdk.model.serializer.toUUID()` extension instead, found from the
existing `AppDatabase.kt` usage). Commit `ea7c7b14`.

### 18.4 Cancel Recording missing `timerId`, and both button rows gated by stale `endDate`

Two related bugs in `ProgramDialog.kt`, both found live:

- The 18.3 fallback item didn't set `timerId`, so `isRecording =
  dto.timerId.isNotNullOrBlank()` was false for ad-hoc timers even though they were
  genuinely already scheduled — the dialog offered "Record Program" again instead of
  "Cancel Recording". Fixed by setting `timerId = it.id` on the fallback.
- Both the Watch Live/Watch-recording-in-progress row and the Record/Cancel row are
  gated by `dto.endDate?.isAfter(now)` — correct for a live guide program, wrong for
  anything reached via "Active Recordings", where being in that list already means
  it's genuinely still `InProgress` server-side and can legitimately run past its
  originally-scheduled end. Caught live against a real recording ("Weather AM") that
  was still actively growing ~75 minutes after its nominal end — confirmed via real
  file-size growth (632MB → 640MB over 17s), not just timer status, before treating
  it as a genuine ongoing recording rather than a stuck one. Both gates now also fire
  whenever `isRecording` is true, regardless of `endDate`. Commit `918fa129`.

### 18.5 "Unsupported item type: Recording"

`DestinationContent.kt`'s central item-type router had no `BaseItemKind.RECORDING`
case at all — fell through to the generic `else`, showing the literal error text.
Added it alongside `VIDEO`/`MUSIC_VIDEO` in the existing `MovieDetails` case: a
Recording (finished or in-progress) behaves the same as any other playable video
here, and `MovieDetails`' own Play button already routes through
`Destination.Playback` → `PlaybackViewModel`, which already correctly detects the
in-progress case (18.1). Verified end-to-end, not just visually — actually deleted a
real test recording through this path and confirmed both the file and the library
entry were gone afterward. Commit `918fa129`.

### 18.6 "Watch recording in progress" from DVR Schedule did nothing

`DvrScheduleViewModel.watchRecordingInProgress()` (a separate copy of the same-named
method in `LiveTvViewModel`, which is called from a different context — the Guide's
program dialog, where the clicked item is a Program and the Recording genuinely does
need to be looked up by channel) redundantly re-queried `/LiveTv/Recordings` filtered
by channel, for an item that was **already** the real Recording — it came straight
from `DvrScheduleViewModel`'s own `active` list. Confirmed live: that filtered query
returns empty even for a timer showing `Status: InProgress` with a real `ChannelId` —
so `recordings.items.firstOrNull()` was always null and the function silently did
nothing (or, depending on which guard was hit, threw inside an
`ExceptionHandler(autoToast = true)` that's easy to miss). Simplified to navigate
directly with the item already in hand — no re-fetch needed. Commit `918fa129`. Not
yet re-confirmed on-device after this exact commit (see `handoff-20260809.md` Open) —
code-reviewed and high-confidence (pure simplification onto an independently-proven
path), not eyeballed working.

### 18.7 Reference: reproducing an in-progress-recording test correctly

An ad-hoc timer (channel+time range, the `sports-dvr-auto` pattern) never links a
`ProgramId`, so it never shows "Watch recording in progress" in the Guide's dialog at
all — that's a test-setup gotcha, not a bug. To reproduce the in-progress-recording UI
path specifically, use the real Record Program flow: `GET
/LiveTv/Timers/Defaults?programId=<id>` then `POST` that payload back, matching what
the app's own "Record Program" button does.
