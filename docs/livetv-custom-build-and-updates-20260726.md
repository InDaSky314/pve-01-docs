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
