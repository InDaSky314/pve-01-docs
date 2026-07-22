# Audio Troubleshooting Report - ExoPlayer on Jellyfin with Threadfin (Channels 100/102)

## Problem Statement
The user is experiencing a lack of audio on specific local Madison IPTV channels (e.g., Channel 100 / ABC 27, Channel 102 / FOX 47) when using the Wholphin (Jellyfin Android TV) app with the ExoPlayer backend. 
Other channels play perfectly. The issue persists specifically with ExoPlayer; switching to MPV provides audio but results in terrible video playback (likely due to missing hardware acceleration for this stream's format).
The streams are provided via Threadfin which proxies an Xtream API IPTV provider.

## Troubleshooting Efforts & Theories Tested

### 1. The Threadfin Buffer Theory (VLC vs FFmpeg)
**Theory:** Threadfin's buffer was modifying or stripping the audio track when proxying the stream to Jellyfin.
**Action:** The previous agent switched the buffer from FFmpeg to VLC. 
**Result:** No change; audio was still missing on Wholphin. I later disabled the buffer entirely (`buffer: "-"`), which also failed to produce audio in Wholphin, although probing the provider URL directly yielded perfect audio and video.

### 2. The FFmpeg Audio Transcoding Theory
**Theory:** The source audio codec (AAC) or its timestamp metadata was malformed, causing ExoPlayer to reject it or Jellyfin's Live TV probe to miss it.
**Action:** Configured Threadfin's internal FFmpeg to explicitly transcode the audio to AAC (`-c:a aac -ac 2 -b:a 192k -muxdelay 0 -muxpreload 0`). 
**Result:** Failed. Jellyfin's initial probe STILL failed to detect the audio track, causing the transcoder to generate an HLS manifest (`MediaStreams`) that completely omitted the audio track. ExoPlayer faithfully respected the manifest and played silent video.

### 3. The `analyzeduration` / `probesize` Theory
**Theory:** Local IPTV broadcast affiliates often have a slight delay before the audio track starts multiplexing into the MPEG-TS stream, or the first few seconds of the stream contain corrupted headers. Jellyfin's hardcoded 3-second Live TV probe (`-analyzeduration 3000`) was timing out before it saw the audio track.
**Action:** Increased Threadfin's `probesize` and `analyzeduration` to 5MB/10MB to ensure Threadfin didn't output a stream prematurely. 
**Result:** Jellyfin still missed the audio.

### 4. The Corrupted Video Header (H.264 SPS/PPS) Theory (The `-ss 3` Workaround)
**Theory:** The raw provider stream had severe video header corruption (missing SPS/PPS packets) at the very beginning of the HTTP stream. ExoPlayer natively crashes when it hits these (`DirectPlayError`), and Jellyfin's short 3-second probe gets stuck on the corrupt headers and misses the audio track entirely.
**Action:** Re-enabled Threadfin's FFmpeg buffer and added `-ss 3` to the input arguments to literally drop the first 3 seconds of the corrupted provider stream, outputting a clean stream starting from a keyframe.
**Result:** Manual testing confirmed that Jellyfin's probe instantly found both the video AND audio track when `-ss 3` was used! However, when the user tested it in Wholphin, it still failed. 

### 5. The Jellyfin Live TV Cache Poisoning Theory
**Theory:** During earlier troubleshooting (or due to provider limits), Threadfin served a "Too many connections" dummy stream (which was 1080p 25fps and silent). Jellyfin probed this stream and cached the `MediaSourceInfo` (1080p 25fps, NO AUDIO) in its internal SQLite database (`library.db`). Even after fixing Threadfin with `-ss 3`, Jellyfin refused to re-probe the Live TV stream, blindly reusing the cached "No Audio" profile and stripping the audio during Transcoding/DirectStream.
**Action:** Asked the user to forcefully refresh the Live TV Guide Data in Jellyfin to wipe the cache, while simultaneously asking them to force Wholphin's Max Bitrate to "Original" / 200 Mbps.
**Result:** The user reported it did not work, even after refreshing the guide data. 

## Current State
- All Threadfin configurations have been reverted to their original state (`buffer: "ffmpeg"`, no `-ss 3`, original `ffmpeg.options`).
- The troubleshooting images uploaded by the user have been deleted from `/root`.

## Recommendations for Claude

If you are picking this up, here are the remaining theories and next steps:

1. **VOD vs Live TV Manifest Differences:**
   The user explicitly tested *recording* Channel 100 via Jellyfin. The resulting `.ts` VOD file played back in Wholphin with **perfect audio**. This conclusively proves that ExoPlayer *is capable* of decoding the audio track. The issue lies entirely in how Jellyfin packages the stream for *Live TV* playback (likely the HLS manifest). You should investigate why Jellyfin's Live TV HLS manifest (`stream.m3u8`) is incompatible with ExoPlayer for this specific stream format, whereas direct playback of the recorded `.ts` file works perfectly.

2. **Jellyfin's Hardcoded `analyzeduration`:**
   Even if the guide data was refreshed, Jellyfin's Live TV probe (`LiveTvMediaSourceProvider.cs`) uses a hardcoded `-analyzeduration 3000000` (3 seconds). If the provider's stream requires more than 3 seconds for the audio track to appear, Jellyfin will ALWAYS mark it as `Audio: null`. 
   *Test:* Have the user play the channel, then manually check Jellyfin's logs to see if the newly generated `MediaStreams` JSON still says `"Codec": "h264"` with no audio track listed.

3. **Client-Side ExoPlayer FFmpeg Extension:**
   The user's screenshot (`diag2.jpeg`) showed: `Use FFmpeg decoder module: Only use FFmpeg if no built-in decoder exists`. It is possible that the built-in Android `MediaCodec` is failing to demux the specific TS stream structure. 
   *Test:* Ask the user to change this setting to **"Always use FFmpeg"** or completely disable the ExoPlayer backend in favor of libVLC if Wholphin supports it (since MPV was too slow).

4. **Threadfin Stream Remuxing:**
   Instead of just `-c copy`, try forcing Threadfin to remux the container perfectly using `-f hls` or passing the stream through a dedicated proxy like `tvheadend` or `xteve` which might generate cleaner TS packets than Threadfin/FFmpeg.
