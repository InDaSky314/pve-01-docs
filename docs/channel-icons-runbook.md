# Runbook: channel icons (NextPVR + Jellyfin)

How channel artwork actually flows, how to change it, and how to verify it at
each layer. Written 2026-08-02 after repairing all 997 channels on CT 112.

Applies to CT 112 (`jellyfin-npvr`). The same layering applies to any
Jellyfin+NextPVR pair; paths differ.

---

## The three layers

Artwork has to be correct at all three. A green check at one says nothing
about the next — that is the single most expensive mistake in this area.

| # | Layer | Where | Fails how |
|---|---|---|---|
| 1 | Icon files | `/srv/jellyfin-npvr/nextpvr/config/media/channels/<Name>.png` | wrong filename → silently ignored |
| 2 | NextPVR API | `channel.icon?channel_id=<OID>` | 404 if layer 1 name is wrong |
| 3 | Jellyfin | `BaseItemImageInfos` row + file under `config/metadata/livetv/<guid>/` | stale/orphan DB row → blank forever |

**Icons are files on disk, not database rows.** `CHANNEL` has no icon column
(`oid, name, number, epg_source, epg_mapping, minor`).

---

## Filename rules (layer 1)

NextPVR looks for `<channel name>.png` (or `.jpg`) with **filesystem-illegal
characters stripped**. Known strips:

| In channel name | In filename |
|---|---|
| `:` | removed — `Madison: ABC 27 (WKOW)` → `Madison ABC 27 (WKOW).png` |
| `/` | removed — `HALLMARK MOVIES/MYSTERIES 4K` → `HALLMARK MOVIESMYSTERIES 4K.png` |
| `\|` | removed — `IT\| PIRATI DEI CARAIBI CHANNEL 4K` → `IT PIRATI DEI CARAIBI CHANNEL 4K.png` |

Write the file with the character still in it and NextPVR ignores it in
silence. This started as "colons are stripped"; it is really "illegal
characters are stripped", and the rule was rediscovered twice — once for 36 of
the first 37 installs, once for the last 2 of 997.

Both `.png` and `.jpg` work. **Never create both for one channel** — the
`.jpg` silently wins and the `.png` is never read. See the next section.

## Extension precedence (layer 1) — the trap that cost 2026-08-03

`.png` and `.jpg` both work, but **when both exist for one channel NextPVR
always serves the `.jpg`** and never looks at the `.png`. Its `StreamIcon`
routine tests `File.Exists(<name>.jpg)` first and branches straight to serving
it.

This matters because the provider import writes `.jpg` placeholders and
`logofix`-style installs write `.png`. Installing good artwork as `.png` over a
channel that already has a provider `.jpg` **changes nothing that anyone can
see**, and the file you just wrote is sitting there to prove you did the work.

Find them before installing anything:

```bash
pct exec 112 -- bash -c 'cd /srv/jellyfin-npvr/nextpvr/config/media/channels && \
  ls | sed "s/\.[^.]*$//" | sort | uniq -d'
```

Move the `.jpg` aside rather than deleting it. The change takes effect on the
next request — no restart, because the lookup hits the filesystem every time.

As of 2026-08-03 all 129 dual-extension files on CT 112 were quarantined to
`media/channels.quarantine-jpgdup-20260803/`.

**NextPVR never re-fetches icons.** They are populated once, at channel import.
Clearing `media/channels` does not trigger a re-fetch — it leaves you with
nothing (verified: `channel.icon` returned 404 across the board). Restore from
backup and write files directly.

---

## Jellyfin's copy (layer 3)

Jellyfin caches artwork **separately** and keyed by its own per-instance item
GUIDs: `config/metadata/livetv/<guid>/metadata/poster.png`. This is why
metadata cannot be shared between Jellyfin instances — the same programme
lands under a different directory on every server.

### The orphan-row trap — read this before deleting anything

`rm -rf config/metadata/*` deletes the image *files* but leaves a
`BaseItemImageInfos` row for every channel still pointing at them. Jellyfin
then believes it already has the image and **never re-fetches it**. Result:
997 channels blank, and no amount of guide refreshing fixes it.

That is exactly what happened on 2026-08-01 23:13, and the symptom ("icons are
not updating") looks nothing like the cause.

**If you clear Jellyfin's channel artwork, you must clear the database rows
too.** Otherwise do neither.

Also note: `POST /Items/{id}/Refresh` does **not** populate Live TV channel
artwork, whatever `imageRefreshMode` you pass. Only the guide refresh path
does. Verified 2026-08-02.

---

## Procedure: install or update icons

Stop and take a backup first; every step below is reversible.

### 1. Put the files in place (layer 1)

Write to `/srv/jellyfin-npvr/nextpvr/config/media/channels/` using the
stripped-name rule above.

Composite transparent logos onto a solid backdrop chosen from their own
luminance (`#141414` for light artwork, `#f2f2f2` for dark). Jellyfin
re-encodes channel images and flattens alpha onto white, so a white-on-
transparent logo (the CBS one, for instance) vanishes entirely otherwise.
`scripts/logofix.py` already does this.

Verify by byte count, not by existence:

```bash
pct exec 112 -- ls -la "/srv/jellyfin-npvr/nextpvr/config/media/channels/Madison ABC 27 (WKOW).png"
```

### 2. Confirm NextPVR serves it (layer 2)

Use the channel **OID**, not the channel number — passing the number returns
404 for everything and looks like a total failure:

```bash
pct exec 112 -- python3 -c "
import sqlite3
c=sqlite3.connect('file:/srv/jellyfin-npvr/nextpvr/config/npvr.db3?mode=ro',uri=True)
for r in c.execute(\"select oid,name from CHANNEL where name like '%WKOW%'\"): print(r)"

pct exec 112 -- curl -s -o /dev/null -w '%{http_code} %{size_download}\n' \
  'http://localhost:8866/service?method=channel.icon&channel_id=7439'
```

Expect `200` and a byte count matching the file. A `200` alone is not enough —
two channels once returned `200` at exactly 5,586 bytes, the placeholder size.

### 3. Make Jellyfin pick it up (layer 3)

For channels that **never had** an image, a guide refresh is sufficient.

For channels whose image is being **replaced**, delete the image rows first or
Jellyfin will keep serving the old one:

```bash
pct exec 112 -- docker stop jellyfin-npvr
pct exec 112 -- cp -a /srv/jellyfin-npvr/jellyfin/config/data/jellyfin.db \
                      /srv/jellyfin-npvr/jellyfin/config/data/jellyfin.db.bak-icons-$(date +%Y%m%d)
# delete BaseItemImageInfos rows joined to BaseItems where type like '%LiveTvChannel%'
pct exec 112 -- docker start jellyfin-npvr
```

Then trigger the guide refresh (task id is stable per instance):

```bash
curl -X POST -H "Authorization: MediaBrowser Token=$KEY" \
  http://192.168.9.219:8096/ScheduledTasks/Running/bea9b218c97bbf98c5dc1303bdb9a0ca
```

It takes ~12 minutes for 997 channels.

### 4. Verify at the layer the user sees

Never stop at layer 2. Check what Jellyfin actually renders:

```bash
curl -s -H "Authorization: MediaBrowser Token=$KEY" \
  "http://192.168.9.219:8096/LiveTv/Channels?limit=997" \
  | python3 -c "import json,sys; d=json.load(sys.stdin)['Items']; \
      m=[i for i in d if not i.get('ImageTags')]; \
      print(len(d),'channels,',len(d)-len(m),'with image'); \
      [print(' missing:',i['Name']) for i in m]"
```

Then pull a few actual images and compare byte counts against the source
files.

---

## When the lineup changes

The provider's lineup shifts, so this is routine rather than exceptional.

**Channels added:** they arrive with whatever logo the provider supplies —
usually a shared placeholder. Run the generic-icon audit below, then install
artwork per the procedure above.

**Channels removed:** their icon files are harmless leftovers, but they make
the "shared image" audit noisier. Remove files with no matching `CHANNEL` row.

**Channels renamed:** this is the dangerous one. The filename is derived from
the name, so every rename orphans its icon. After any bulk rename, re-run
step 1 *and* verify every `epg_mapping` blob still parses as XML — an
unescaped `&` in a channel name once aborted EPG ingest for all 997 channels
while reporting success.

### Finding channels that still have a generic icon

Size checks do not work — the 5,586-byte placeholder is only 1 of **45**
distinct shared images. Group by content hash instead:

```bash
pct exec 112 -- python3 /root/icon-candidates.py
```

As of 2026-08-02: 998 files, 403 unique images, 640 files sharing an image,
of which 369 are dynamic event slots and **271 are worth researching**.

Dynamic event slots (`NBA 07`, `Soccer PPV 42`, `UEFA 16`, `SKY SPORT
BUNDESLIGA 7`) are deliberately left alone — the channel's identity changes
per fixture, so a bespoke logo would be wrong by the next event.

### Local affiliates

Preferred style is the affiliate plus its network and number, as the channel
name already reads: `WMSN FOX 47`, `WKOW ABC 27`. Many already look like this.
When sourcing a logo for one, prefer artwork that carries the call sign.

---

## Sourcing artwork

**Index first, match locally.** The working method is to pull the `tv-logos`
file list once via the GitHub trees API (10,777 files), match offline, and
download only what exists. The first attempt guessed URLs and fetched
speculatively — roughly 8 requests per channel, almost all 404 — and was far
too slow to finish.

`scripts/logofix.py` implements the indexed matcher, the luminance backdrop,
and the stripped-filename rule.

**Do not assume production is better.** All 996 rendered images were exported
from production's Jellyfin and compared by MD5: only **29** were better. Its
Threadfin image directories are empty and 134 of its channels share one
placeholder. CT 112 is ahead of production on icons.

---

## Disk

Guide artwork is measured in gigabytes: ~33,000 programmes produced 7.6 GB in
Jellyfin plus 1.6 GB in NextPVR, which filled a 16 GB disk until Jellyfin
refused to start (`Required: 2GiB`). See `lessons-learned.md` for container
sizing; 40 GB is the floor for a Live TV stack.
