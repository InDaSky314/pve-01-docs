# Handoff to Claude — 2026-07-25

## Exact Stopping Point

1. **Root cause of No CompatibleStream found and fixed.**
   - In `DeviceProfileService.kt`, `maxBitrate` was read as `prefs.maxBitrate.toInt()`.
   - On a fresh/debug installation (com.github.damontecres.wholphin.debug`), proto DataStore initialized `maxBitrate` to `0`.
   - `DeviceProfileService` built a `DeviceProfile` with `MaxStreamingBitrate: 0` and `MaxStaticBitrate: 0`.
   - Jellyfin compared stream bitrates against `0` and rejected)]ery single channel with `NoCompatibleStream` / `VideoCodecNotSupported`.
   - Added fallback `takeIf { it > 0 } ?: AppPreference.DEFAULT_BITRATE` in `DeviceProfileService.kt` and `AppPreference.kt`.

2. **Build & ADB Install Completed.**
   - Commit `a240f9e9`: fix(profile): fallback maxBitrate to DEFAULT_BITRATE when uninitialized (0) to prevent NoCompatibleStream failure.
   - Built `assembleDefaultDebug` (`BUILD SUCCESSFUL
).
   - Installed `Wholphin-default-debug-1.0.3-27-g8f45372a-54-armeabi-v7a.apk` onto Chromecast (`192.168.9.203:5555`) via ADB.
   - Pushed commit to `github/main`(`nk-sys-ops/wholphin`), local `origin`(`\/root\/wholphin`), and updated `\/root\/wholphin-backups\/wholphin-backup.bundle`.

## Verified vs Assumed

- **VERIFIED:** `DeviceProfileUtils.kt` was untouched in the fork (empty diff against `cf3c00a1`).
- **VERIFIED:** Jellyfin debug log `GetPostedPlaybackInfo profile:` showed `MaxStreamingBitrate: 0, MaxStaticBitrate: 0` during broken tune attempts.
- **VERIFIED:** Querying `\/PlaybackInfo` API with `maxBitrate = 0` reproduced `VideoCodecNotSupported` / `NoCompatibleStream`, while `maxBitrate = 104857600` returned `ErrorCode: None` and `supportsTranscoding: True`.
- **VERIFIED:** Gradle build succeeded and APK was installed on Chromecast over ADB (`A192.168.9.203:5555`).
- **ASSUMED:** On-device audio listening test on TV channels 133, 134, 313 still requires physical TV check by owner.

## What changed & Commit Hashes

- **\/srv\/media-core\/wholphin**:
   - `a240f9e9` - `fix(profile): fallback maxBitrate to DEFAULT_BITRATE when uninitialized (0) to prevent NoCompatibleStream failure`
   - Modified files: app/src/main/java/com/github/damontecres/wholphin/services/DeviceProfileService.kt, app/src/main/java/com/github/damontecres/wholphin/preferences/AppPreference.kt.

## What was tried that did NOT work

- Direct \/PlaybackInfo` API call without `userId` query parameter returned HTTP5 00 (`System.ArgumentException: Guid can't be empty`). Adding `userId` fixed the API test.

## Next Steps

1. Test Live TV channel tuning on Chromecast (channels 133, 134, 313, 100/102).
2. If audio remains missing on raw TS streams, proceed with HLS transport experiment (gate `Codec.Container.TS` in `DeviceProfileUtils.kt` behind an experimental preference to force HLS delivery).

## Deferred Items

1. **Prune stale Tailscale nodes and add tailnet ACLs** (MEDIUM-HIGH).
2. **WireGuard keys in Loki history** (MEDIUM).
3. **Active WAN is the WiFi repeater** (`apclix0` = logical cwwan`, SSID `GIOTb) at metric 1.

## Temporary state / Debug settings

- Jellyfin logging level in CT 105 (`\/srv\/media-core\/jellyfin\/config\/config\/logging.default.json`) was set to `"Default": "Debug"`. Original backed up at `logging.default.json.bak`.
