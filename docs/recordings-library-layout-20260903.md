# Recordings library: definite content type and where artwork comes from

**Changed 2026-09-03.** The Jellyfin `Recordings` library is now
`CollectionType=movies` pointing at **`/media/recordings/Commercial Free`** —
the comskip output — rather than the whole recordings tree.

## Why the tree was a mess

`Recordings` had no content type (`CollectionType: None` = mixed). Items classified
unpredictably, and MCT captures came back with no artwork, no genres and no plot.

Setting the type to `movies` alone did **not** fix it. Classification is driven by
folder shape, not library type:

| path | classified as |
|---|---|
| `Other/<title>/file` | Movie |
| `Commercial Free/**/file` | Movie |
| `Sports/<title>/file` | **Episode** |

`Sports/` holds many dated subfolders, so Jellyfin's series heuristic claimed the whole
folder as a Series and everything under it became Season/Episode. Worse, every recording
exists **twice on disk** — raw under `Sports/`, processed under `Commercial Free/` — so
Jellyfin indexed each one under both classifications. Before: 28 items, 8 Episodes,
7 Seasons, duplicates throughout. After: **5 Movies, 3 category folders, no duplicates.**

The content type is a zero-byte marker file in the library folder
(`movies.collection` / `tvshows.collection`), not an element in `options.xml`.

## Two things that had to be fixed first

Pointing the library at `Commercial Free` would otherwise have made things worse:

1. **comskip only ever moved the `.mkv`.** A Commercial Free folder held the video and
   nothing else. That was invisible while the library read metadata from the raw copy, but
   once it points here, artwork and plot come from here or not at all. `copy_sidecars()`
   in `process-queue.py` now carries `poster.*`/`folder.*`/`fanart.*` and the matching NFO
   across, renaming the NFO to the output basename — Jellyfin matches sidecars by filename.

2. **A recording with no detected commercials produced no output at all.** The old code
   logged `NO COMMERCIALS detected — no cut version made` and returned. With the library
   pointing here that means the recording silently never appears. It now passes through
   uncut via the same cut/concat path, so the MKV timeline fix still applies.

Existing Commercial Free folders were backfilled with 7 sidecar files from their raw
counterparts.

## Trade-off accepted

A recording appears in the library only **after comskip finishes**. For overnight sport
that is fine. Anything you want to watch immediately will not be there yet.

## Verification

Files were untouched across two library rebuilds: 20 media files,
41,823,237,755 bytes, identical before and after.

A plain library scan does **not** ingest NFO/artwork for items whose classification
changed — a full refresh is required:

```
POST /Items/<libraryId>/Refresh?metadataRefreshMode=FullRefresh&imageRefreshMode=FullRefresh&replaceAllMetadata=true&recursive=true
```

After that all 5 items carry `Primary` artwork, with genres and plot populated wherever
the source NFO had them (`1. Bundesliga`, `['Sports','Football']`). Items still showing
empty genre/plot are ones whose source NFO was itself empty — the stitched Brewers game
has only a poster and no NFO, and the old MCT test wrote `<plot />`.

## Back-out

Delete the marker and repoint at the whole tree:
```
rm "/srv/media-core/jellyfin/config/root/default/Recordings/movies.collection"
```
then recreate the virtual folder with `paths=/media/recordings` and no `collectionType`.
`process-queue.py.pre-comfree-20260903` restores the previous comskip behaviour.
