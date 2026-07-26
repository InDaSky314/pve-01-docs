# Live TV: NoCompatibleStream (fixed) + no-audio (root-caused)

Sessions: evening 2026-07-25 and morning 2026-07-26. Host `pve-01`,
Jellyfin 10.11.9 in CT 105, Chromecast with Google TV at
`192.168.9.203:5555`. All log timestamps Europe/Berlin.

**Current state: both bugs root-caused. Bug A is fixed and shipped. Bug B
has a working day-to-day answer (MPV) and a candidate code fix that is
committed but NOT yet verified on device.**

---

## 1. Bug A — `NoCompatibleStream` on all Live TV — FIXED, SHIPPED

**Symptom:** every Live TV channel failed instantly with
`Error in PostedPlaybackInfo: NoCompatibleStream`.

**Root cause — not codecs.** In Jellyfin 10.11 that error has exactly one
trigger, in `MediaInfoHelper.GetPlaybackInfo`:

```csharp
mediaSources = mediaSourcesList
    .Where(i => string.Equals(i.Id, mediaSourceId, StringComparison.OrdinalIgnoreCase)).ToArray();
if (mediaSources.Length == 0)
    result.ErrorCode ??= PlaybackErrorCode.NoCompatibleStream;
```

Wholphin sent the media source id carried on the item DTO — the item id
with dashes stripped (`52dd5eb0f5f0393302455b8ae619cad8`). Live TV source
ids are generated server-side as
`"native_" + MD5(channelId) + "_" + MD5(tunerUrl)`, so the id sent could
never match and every source was filtered out.

**Fix:** omit `mediaSourceId` for `BaseItemKind.TV_CHANNEL`, inside
`changeStreams` so all six call sites are covered. Commit `bddf59cc`,
pushed to `nk-sys-ops/wholphin`, branch
`fix/jellyfin-live-tv-ts-transcode`. Verified: channel 100 goes
`sourceId=null` → `Playback decision: Transcode` → plays.

---

## 2. Bug B — no audio on some Live TV channels — ROOT-CAUSED

Affected: **100, 102, 133, 134**. Working: **101, 103**.

### Root cause: two independent faults that compound

**Fault 1 (server).** Jellyfin's probe of the opened live stream reports
the source as **video-only**. Consequence:
`TranscodingInfo(audioCodec=null, isAudioDirect=false, audioChannels=null)`,
versus a working channel's `audioCodec=aac, isAudioDirect=true`.

**Fault 2 (client).** `DefaultHlsExtractorFactory.createTsExtractor`:

```java
if (!MimeTypes.containsCodecsCorrespondingToMimeType(codecs, MimeTypes.AUDIO_AAC)) {
    payloadReaderFactoryFlags |= DefaultTsPayloadReaderFactory.FLAG_IGNORE_AAC_STREAM;
}
```

When the HLS variant does not advertise AAC, ExoPlayer instructs its TS
demuxer to **ignore AAC elementary streams outright**. The stream Jellyfin
serves *does* contain a valid AAC track with its PID in the PMT — it is
deliberately discarded. Hence no audio track group at all, which is why
the pre-existing `onTracksChanged` audio fallback never fires:
`audioGroups` is empty, so there is nothing to select.

Either fault alone would be harmless. Together they produce silence.

This explains every observation:

| Observation | Explanation |
|---|---|
| `WHOLPHIN_DIAG` shows `type=2` video + `type=5` id3, no `type=1` | AAC reader never created |
| Audio present in segments on disk, PMT declares PID 0x101 | Demuxer told to ignore it |
| ch101 works, diag shows `mp4a.40.2` | Its codecs string contained AAC |
| **MPV plays it** | Does not use ExoPlayer's HLS extractor |
| jellyfin-web plays it | hls.js has no such guard |
| A Jellyfin recording plays it | Not HLS |
| TiViMate plays it | Direct TS, bypasses Jellyfin entirely |

### Ruled out, with evidence — do not re-chase

- **AAC Main profile.** Every silent channel is AAC Main at source, every
  working one AAC-LC — a real correlation but **not the cause**. Proven by
  forcing Threadfin to deliver AAC-LC: still silent. ffmpeg normalises
  Main→LC anyway, so what reaches the device was always LC.
- **Stream/PID order.** Silent channels had video PID first, working ones
  audio first. Forced audio-first via `-map 0:a:0 -map 0:v`: still silent.
- **Stale Jellyfin cache.** MediaSource id is byte-identical across
  Threadfin restarts (it is `MD5(channelId)+MD5(tunerUrl)`), so a cached
  bad probe was plausible. Restarted Jellyfin: still silent.
- **Missing `-codec:a:0 copy`.** jellyfin-web gets the byte-identical
  ffmpeg command and has audio. With no `-codec:a` and no `-an`, ffmpeg's
  default stream selection still maps and re-encodes the audio. Verified
  on synthetic h264+aac and on the real ch100 stream.
- **HLS playlist `CODECS`.** `live.m3u8` is a *media* playlist with no
  `EXT-X-STREAM-INF` and no CODECS attribute.
- **Segment-0 A/V start gap.** ch100 showed 2.02 s vs ch101's 4 ms, which
  looked decisive at n=2 — but the other silent channels are aligned
  (102: 44 ms, 133: 53 ms, 134: 19 ms). One-off from GOP join point.
  ffmpeg timestamp-arg variants got it to 880 ms at best and fix nothing.
- **ffprobe / probe parameters.** Could not make ffprobe fail on this data
  in any configuration: the buffer file Jellyfin itself probes at
  200 KB/1 MB/3 MB/10 MB/whole-file, probesize swept 5 MB→50 KB,
  analyzeduration 3 s→1 s, and Jellyfin's exact ffprobe command against
  the live endless stream. All found `aac,Main,audio` on PID 0x101.
- **Jellyfin source-level suspects.** `MediaSourceManager`'s post-probe
  filter keeps the first audio stream; `MediaEncoder.GetMediaInfoInternal`
  has no protocol branching, so the bogus `Protocol: Udp` on hdhomerun
  sources does not alter the command.

**Still unexplained:** *why* Jellyfin's probe misses the audio, given the
audio is trivially findable in the identical data. That is Fault 1 and it
is a genuine Jellyfin bug. Fixing Fault 2 makes it moot for us.

---

## 3. Where things stand

### Working answer today: MPV
The upstream Wholphin release ships `libmpv.so` plus the media3 ffmpeg
software decoders; **our custom build does not** — those are gated behind
`extensionsRepoActive`, which needs a private Maven credential
(`WholphinExtensionsUsername`). Our build silently links the *stub*, so
selecting MPV in it does nothing.

Installed **upstream v1.0.3 armeabi-v7a** as package
`com.github.damontecres.wholphin`, coexisting with our `.debug` build.
Chromecast is 32-bit userspace despite 64-bit silicon — the arm64 APK
fails with `INSTALL_FAILED_NO_MATCHING_ABIS`.

MPV plays the affected channels **with audio**. Current `mpv.conf`:

```
hwdec=mediacodec-copy
framedrop=vo
video-sync=audio
cache=yes
demuxer-max-bytes=32MiB
demuxer-readahead-secs=10
```

Smoother than the defaults but still short of ExoPlayer. Next tweaks not
yet tried, in order: add `profile=fast` (disables expensive scaling /
dithering / debanding — the biggest remaining lever on a weak Mali GPU),
add `vo=gpu-next` (libplacebo, better frame timing), and as a pure
diagnostic set `hwdec=no` (if judder vanishes hwdec is confirmed; if
1080p becomes a slideshow the CPU cannot software-decode).

### Candidate real fix: committed, NOT verified
Commit `99f0b692`, pushed. Adds:

- `util/player/AacAwareHlsExtractorFactory.kt` — rewrites the codecs
  string to include AAC before delegating to `DefaultHlsExtractorFactory`,
  so `FLAG_IGNORE_AAC_STREAM` never gets set. The flag cannot be cleared
  through configuration; it is OR-ed in internally and the public
  constructor only *adds* flags. Blank codecs strings are deliberately
  left alone, because the same method also sets `FLAG_IGNORE_H264_STREAM`
  when H.264 is absent — injecting an audio-only codecs string where there
  was none would suppress *video*.
- `util/player/WholphinMediaSourceFactory.kt` — `DefaultMediaSourceFactory`
  exposes no hook for the HLS extractor factory, so this handles
  `CONTENT_TYPE_HLS` itself and delegates everything else unchanged.
- Gated on the existing **IPTV Audio Track Recovery** toggle.

**It compiles and is installed on the device but has not been exercised
on an affected channel.** That is the single outstanding task.

---

## 4. Next steps, in priority order

1. **Verify `99f0b692`.** In the `.debug` build ensure *Settings →
   Experimental → IPTV Audio Track Recovery* is ON, play ch100, and check
   `adb logcat | grep WHOLPHIN_DIAG` for a `type=1` audio track group and
   the new `hls_codecs_patched` line. If audio returns, this is the real
   fix and MPV becomes unnecessary.
2. **If it does not work**, the likely reason is that `Format.codecs` is
   blank rather than video-only, in which case the guard is still set and
   the factory must instead reimplement `createTsExtractor` without the
   flag. Confirm by reading the `hls_codecs_patched` log line — its absence
   means the codecs string was blank.
3. **Report Fault 2 upstream** to androidx/media. Unusually strong report:
   valid TS, AAC PID declared in the PMT, ExoPlayer emits no audio track,
   MPV on the same device and same bytes does.
4. **Report Fault 1 upstream** to Jellyfin. Related and already fixed:
   [#15479](https://github.com/jellyfin/jellyfin/issues/15479) is the same
   bug class inverted (probe missed the *video* codec → black screen on
   ExoPlayer clients, VLC fine). A 10.12 nightly may already fix ours.
5. **Optional cleanup:** the `tsDirectPlay` toggle and unconditional `ts`
   injection in `19048035` target a cause that turned out not to be real
   and can be reverted. **Keep** the `DEVICE PROFILE` and `WHOLPHIN_DIAG`
   logging from that commit — the track logging is what cracked this.

---

## 5. Infrastructure notes

- Jellyfin's only tuner is **Threadfin registered as `hdhomerun`** at
  `http://127.0.0.1:34400`, `tuner: 1`. NextPVR is installed but was not
  serving these channels.
- **Threadfin returns HTTP 404 when its single tuner is busy** — looks
  exactly like a missing stream. Free the tuner before trusting any probe.
- Threadfin stream ids **change on restart**; re-fetch `lineup.json` or you
  get `EC: 1203 (Streaming URL could not be found in any playlist)`.
- Threadfin config was temporarily set to `buffer: ffmpeg` with audio
  normalisation during testing and has been **fully reverted** to
  `buffer: -` / `-c copy`. Backup at
  `settings.json.bak-20260726-084846`.
- Wireless debugging does not survive a Chromecast reboot; re-enable in
  *Settings → System → Developer options*, then
  `adb connect 192.168.9.203:5555`.

## 6. Reproduction commands

```bash
# Source audio profile per channel (free the tuner first)
curl -s http://192.168.9.50:34400/lineup.json | python3 -c "
import json,sys
for c in json.load(sys.stdin):
    if c['GuideNumber']=='100': print(c['URL'])"

sudo /usr/sbin/pct exec 105 -- docker exec jellyfin \
  /usr/lib/jellyfin-ffmpeg/ffprobe -v quiet -analyzeduration 3000000 \
  -probesize 1000000 -show_entries "stream=codec_type,codec_name,profile" \
  -of csv=p=0 "http://192.168.9.50:34400/stream/<ID>"

# What Jellyfin's probe decided
sudo /usr/sbin/pct exec 105 -- docker logs jellyfin --since 5m 2>&1 \
  | grep "Live stream opened" | tail -1

# The ffmpeg command Jellyfin built (check for -codec:a:0 copy)
sudo /usr/sbin/pct exec 105 -- docker logs jellyfin --since 5m 2>&1 \
  | grep -oE "jellyfin-ffmpeg/ffmpeg .*" | tail -1

# What the HLS output segments actually contain
sudo /usr/sbin/pct exec 105 -- docker exec jellyfin sh -c \
  'f=$(ls -t /cache/transcodes/*[0-9].ts | head -1); \
   /usr/lib/jellyfin-ffmpeg/ffprobe -v quiet \
   -show_entries "stream=index,codec_type,codec_name,profile" -of csv=p=0 "$f"'

# What ExoPlayer built (type=1 is audio; absent on affected channels)
adb -s 192.168.9.203:5555 logcat -v time | grep WHOLPHIN_DIAG
```

## 7. Repo state

- `/srv/media-core/wholphin`, branch `fix/jellyfin-live-tv-ts-transcode`,
  **pushed** to `nk-sys-ops/wholphin`
- `bddf59cc` — Bug A fix, verified working
- `99f0b692` — Bug B candidate fix, compiles, **unverified on device**
- Installed on the Chromecast: `...wholphin.debug` (our build, has both
  fixes) and `...wholphin` (upstream v1.0.3, has MPV)
