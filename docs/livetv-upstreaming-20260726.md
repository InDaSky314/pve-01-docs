# Upstreaming the Live TV fixes — drafts and strategy

Written 2026-07-26 night. Companion to
`livetv-custom-build-and-updates-20260726.md` (build/release state) and
`livetv-audio-rootcause-20260726.md` (the investigation).

Nothing here has been submitted. No fork created, no issue or PR opened.

---

## Why two findings need three different destinations

The two bugs are not equally straightforward to contribute.

**`mediaSourceId` — unambiguous, entirely Wholphin's bug.** The client
sends an id that can never match a server-generated live-TV source id. The
fix is obviously correct and self-contained. Good PR.

**Live TV no-audio — a workaround for two *other* projects' bugs.** It
needs Jellyfin advertising bad metadata *and* media3 acting on it. Our
`AacAwareHlsExtractorFactory` deliberately defeats a guard media3 added on
purpose. A maintainer could fairly say "that is Jellyfin's problem".

Known weakness in our implementation, worth fixing before proposing it
upstream: `WholphinMediaSourceFactory` routes **all** HLS through the
AAC-aware factory, not just Live TV, so it could surface phantom audio
tracks elsewhere. Ours is gated behind the experimental toggle, which makes
it acceptable locally, but as an upstream default it is a harder sell.
Scoping it to `TV_CHANNEL` would make it much more defensible.

Priority order: **Jellyfin issue** (root cause, fixing it makes both client
workarounds unnecessary) → **media3 issue** (needs evidence we do not yet
have, see below) → **Wholphin PR** (the workaround, framed as working
around the Jellyfin bug).

---

## 1. Wholphin PR — ready to submit

Branch **`pr/livetv-mediasourceid`**, pushed to `nk-sys-ops/wholphin`.
Cherry-picked onto upstream `f16a7bb6`, no conflicts, one file, +10/−2.
`compileDefaultDebugKotlin` → **BUILD SUCCESSFUL**. Diff scanned: no
identifying strings.

**Mechanical blocker:** `nk-sys-ops/wholphin` is standalone
(`isFork: false`), and GitHub only allows PRs within a fork network. To
submit, fork `damontecres/Wholphin` under `nk-sys-ops`, push this branch
there, and open the PR from it. That publicly ties `nk-sys-ops` to the
contribution — pseudonymous, but visible.

**Title**

```
fix(playback): don't send mediaSourceId for Live TV channels
```

**Body**

> ## Problem
>
> All Live TV playback fails immediately with `NoCompatibleStream` against
> Jellyfin 10.11.x when the server has a tuner configured:
>
> ```
> E/PlaybackViewModel$changeStreams: Error in PostedPlaybackInfo: NoCompatibleStream
> ```
>
> ## Root cause
>
> `changeStreams` sends the media source id taken from the item DTO:
>
> ```kotlin
> mediaSourceId = sourceId,
> ```
>
> For a `TV_CHANNEL` that id is just the item id with dashes stripped (eg
> `52dd5eb0f5f0393302455b8ae619cad8`). But Jellyfin generates live-TV media
> source ids server-side, per request, from the tuner host — the HDHomeRun
> host uses `"native_" + MD5(channelId) + "_" + MD5(tunerUrl)`, eg
> `native_e501dca4…_9e5ef5af…`.
>
> `MediaInfoHelper.GetPlaybackInfo` then filters by the requested id:
>
> ```csharp
> mediaSources = mediaSourcesList
>     .Where(i => string.Equals(i.Id, mediaSourceId, StringComparison.OrdinalIgnoreCase)).ToArray();
> if (mediaSources.Length == 0)
>     result.ErrorCode ??= PlaybackErrorCode.NoCompatibleStream;
> ```
>
> The id we send can never match, so every source is filtered out and the
> server returns `NoCompatibleStream`. This is the only place Jellyfin 10.11
> sets that error — despite the name it is unrelated to codec negotiation.
>
> ## Fix
>
> Leave `mediaSourceId` unset for `BaseItemKind.TV_CHANNEL` and let the
> server select the source. Applied inside `changeStreams` so every call
> site is covered.
>
> ## Notes
>
> `Codec` being null on live-TV media streams is normal before
> `/LiveStreams/Open` — the server fills it in at open time — so it is not
> evidence of a device-profile mismatch. `StreamBuilder` is never reached
> in this failure path.
>
> ## Testing
>
> Chromecast with Google TV, Jellyfin 10.11.9, HDHomeRun-type tuner host.
> Before: every channel fails instantly. After: `sourceId=null` →
> `PlayMethod=Transcode` → channel plays.

---

## 2. Jellyfin issue — highest value, evidence is solid

Not yet drafted. The evidence is already airtight and entirely server-side:

- Jellyfin's probe of the opened live stream reports **video only**
- ffprobe finds `aac,Main` on PID 0x101 in **the same buffer file Jellyfin
  probed** (`/cache/transcodes/<id>.ts`) at 200 KB / 1 MB / 3 MB / 10 MB
  heads and whole-file, with probesize swept 5 MB → 50 KB and
  analyzeduration 3 s → 1 s
- **Jellyfin's exact ffprobe command** run against the live tuner stream
  also finds both streams
- consequence: the generated ffmpeg command has no `-codec:a`, and clients
  that trust the advertised codecs get silence
- [#15479](https://github.com/jellyfin/jellyfin/issues/15479) is the same
  class inverted (probe missed the *video* codec) and is marked fixed, so
  there is precedent and appetite

Setup: Jellyfin 10.11.9, `hdhomerun` tuner host pointed at Threadfin.
Affected channels carry AAC **Main**; working ones carry AAC-LC — a strong
correlation, though the causal link was never established and should be
described as correlation only.

---

## 3. media3 issue — blocked on one missing observation

The guard is real and quoted verbatim in
`DefaultHlsExtractorFactory.createTsExtractor`:

```java
if (!MimeTypes.containsCodecsCorrespondingToMimeType(codecs, MimeTypes.AUDIO_AAC)) {
    payloadReaderFactoryFlags |= DefaultTsPayloadReaderFactory.FLAG_IGNORE_AAC_STREAM;
}
```

**What we still do not know:** the actual value of `Format.codecs` for the
affected streams. Reasoning says it cannot be empty — the same method sets
`FLAG_IGNORE_H264_STREAM` when H.264 is absent, and video plays fine — but
that is inference, never observed.

**Do not file this issue until that is captured.** A report built on
inference risks being wrong. It is a two-minute job:

```bash
adb -s 192.168.9.203:5555 logcat -c
adb -s 192.168.9.203:5555 logcat -v time > /tmp/cap.log &
# play ch100 in Wholphin Custom, then:
grep -aE "hls_codecs_patched|WHOLPHIN_DIAG track" /tmp/cap.log
```

Start the capture **before** pressing play — the line is emitted at player
creation, which is why three earlier attempts missed it.

Expected: `hls_codecs_patched from=… to=…` showing the real codecs string,
plus a `type=1` audio track group confirming the patch is what restored
audio (currently strong inference, not a direct sighting).
