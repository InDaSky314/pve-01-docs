# Runbook: the NextPVR ecosystem (CT 112)

Everything needed to change numbers, change names, build and organise artwork,
and prove the result — for the **NextPVR + Jellyfin** stack specifically.

Production (Threadfin + Jellyfin) behaves differently enough that mixing the
two procedures has caused real damage. Production lives in
`lineup-runbook.md`; artwork mechanics common to both live in
`channel-icons-runbook.md`. Read `lessons-learned.md` before any of them.

Written 2026-08-03, after repairing 997 channels' artwork end to end.

---

## What is different about this stack

| | Production (CT 105) | NextPVR (CT 112) |
|---|---|---|
| Channel identity | `tvg-name` in Threadfin | `CHANNEL.name` in `npvr.db3` |
| Numbers | `renumber-xepg.py` rewrites xepg | in-place `UPDATE` on `CHANNEL.number` |
| Artwork storage | URL in `tvg-logo` | **files on disk**, name-keyed |
| Artwork refresh | provider, on demand | **once, at channel import — never again** |
| Renumber risk | low | low (OIDs untouched) |
| Rename risk | high | **high — orphans the icon file** |

The one-line summary: **numbers are cheap here, names are expensive, and
artwork never re-fetches itself.**

---

## Where things live

```
CT 112  /srv/jellyfin-npvr/
  nextpvr/config/npvr.db3                  CHANNEL, EPG_EVENT, SCHEDULED_RECORDING
  nextpvr/config/playlist.m3u              pushed from CT 105; NextPVR reads at import
  nextpvr/config/epg.xml                   pushed daily by epg-sync-ct112.timer (12:28)
  nextpvr/config/media/channels/<name>.png artwork, name-keyed
  jellyfin/config/data/jellyfin.db         BaseItems + BaseItemImageInfos
  jellyfin/config/metadata/livetv/<guid>/  Jellyfin's own cached copies
```

NextPVR API: `http://<ct112>:8866`. PIN `0000`, no username.
Jellyfin API key `claude-iconfix`. Guide-refresh task id
`bea9b218c97bbf98c5dc1303bdb9a0ca` (stable per instance).

The lineup itself is still defined **only** in `/srv/media-core/sync/config.json`
on CT 105. CT 112 consumes the playlist that produces. Never define a second
source of truth here.

---

## The four layers

Artwork and lineup both have to be right at every layer, and each one can agree
with the layer below and still be wrong. This is the single most expensive
mistake in this estate; it has been made at least four times.

```
1. config.json (CT 105)     the lineup
2. playlist.m3u (CT 112)    tvg-chno, tvg-name, tvg-logo
3. npvr.db3 / icon files    what NextPVR believes and serves
4. Jellyfin                 BaseItemImageInfos row + cached file  <- what the user sees
```

**A change is not done until layer 4 has been queried and matches.**

---

## Procedure: change channel numbers

Numbers are safe to change. OIDs, recordings, EPG mappings and icon files all
survive, because the number column is updated in place and matched on name.

```bash
# 0. snapshot
icon-archive export
pct exec 112 -- cp -a /srv/jellyfin-npvr/nextpvr/config/npvr.db3 \
                     /srv/jellyfin-npvr/nextpvr/config/npvr.db3.bak-renumber-$(date +%Y%m%d)

# 1. edit start_chno in config.json on CT 105, then regenerate
pct exec 105 -- systemctl start media-core-sync.service     # ~90s
# 2. push the new playlist to CT 112 (see "Feeding CT 112" below)

# 3. apply the numbers — NextPVR must be stopped, it writes on shutdown
pct exec 112 -- docker stop nextpvr-live
pct exec 112 -- python3 /root/ct112-renumber.py             # dry run first
pct exec 112 -- python3 /root/ct112-renumber.py --apply
pct exec 112 -- docker start nextpvr-live

# 4. Jellyfin picks numbers up from a guide refresh
```

`ct112-renumber.py` normalises the provider's country prefix on both sides
(`US: CNN 4K` vs `CNN 4K`) — without that, 574 of 997 fail to match and the
collision guard aborts. If it reports unmatched channels, **stop and read
them**; a non-empty unmatched list after a lineup edit usually means something
was renamed.

**Do not use `ct112-reimport.py` to renumber.** It deletes every CHANNEL row,
which changes every OID, orphans recordings, and leaves Jellyfin holding
hundreds of icon URLs that 404. It exists for a rebuild, not an edit.

---

## Procedure: change channel names

> Rename on its own. Never in the same change as a move.

A rename here does three things at once:

1. **Orphans the icon file.** The filename is derived from the name, so the
   old `<name>.png` no longer matches and the channel loses its artwork
   silently.
2. **Breaks the renumber match.** `ct112-renumber.py` matches on name.
3. **Deletes and recreates the Jellyfin channel**, losing its UserData
   (favourites, last-watched) — which on production also breaks guide ordering.

Sequence that works:

```bash
# 1. snapshot artwork FIRST -- this is the step that is actually irreversible
icon-archive export
cp /root/icon-archive/manifest.json /root/icon-archive/manifest.pre-rename.json

# 2. record the old->new mapping in a file. You will need it in step 4.
# 3. rename in config.json (dict form -- the value IS the display name),
#    then sync, then push the playlist, then rename in npvr.db3 by OID:
#      UPDATE CHANNEL SET name = ? WHERE oid = ?
#    Matching by OID, not by name, is what makes this reversible.

# 4. rename the icon files to match, applying the illegal-character strip
# 5. verify every epg_mapping blob still parses as XML
pct exec 112 -- python3 /root/epg-mapping-check.py    # scripts/epg-mapping-check.py
# 6. clear ALL Jellyfin channel image rows + files, one guide refresh
```

An unescaped `&` in a channel name once made ten `epg_mapping` blobs invalid
and aborted EPG ingest for **all 997 channels** while reporting success. After
any bulk rename, step 5 is not optional.

---

## Procedure: build and organise artwork

### The naming rule

NextPVR looks for `<channel name>.png` (or `.jpg`) with **filesystem-illegal
characters stripped**: `:` `/` `\` `|` `*` `?` `"` `<` `>`. Write the file with
the character still in it and NextPVR ignores it in silence.

```python
import re
filename = re.sub(r'[:/\\|*?"<>]', '', channel_name) + ".png"
```

### The extension rule — the trap of 2026-08-03

**When both `<name>.jpg` and `<name>.png` exist, NextPVR always serves the
`.jpg`.** Its `StreamIcon` routine tests `File.Exists(<name>.jpg)` first and
branches straight to serving it; the `.png` is never read.

The provider import writes `.jpg` placeholders. Curated installs write `.png`.
So installing good artwork as `.png` over a channel that already has a
provider `.jpg` **changes nothing anyone can see** — and 69 channels sat like
that for a day looking like a Jellyfin bug.

Always check before and after installing:

```bash
pct exec 112 -- bash -c 'cd /srv/jellyfin-npvr/nextpvr/config/media/channels && \
  ls | sed "s/\.[^.]*$//" | sort | uniq -d'
```

Expect **no output**. Move offenders aside (do not delete):

```bash
pct exec 112 -- mkdir -p /srv/jellyfin-npvr/nextpvr/config/media/channels.quarantine-$(date +%Y%m%d)
```

The change takes effect on the next request. No restart — the lookup hits the
filesystem every time.

### Sourcing

1. **Index once, match offline.** Pull the `tv-logos` file list via the GitHub
   trees API (~10,800 PNGs) and match locally. `scripts/icon-match.py` does
   this. Guessing per-channel URLs was tried: ~8 requests per channel, almost
   all 404, far too slow to finish.
2. **Search the index by keyword before accepting "no match".** The automatic
   matcher scored 5 of 48 on one batch; keyword-searching the same index by
   hand found 35. Brands rename — CSN became NBC Sports Regional, Decades
   became Catchy Comedy, Hallmark Drama became Hallmark Family — and no
   string-similarity metric will bridge that. A human pass is worth it.
3. **A stretched fuzzy match is worse than nothing.** It looks deliberate.
   `MDR HD SACHSEN` matching `mdr-sachsen-anhalt` is a different Land.
4. **Where nothing exists upstream, generate a wordmark.** A plain name card is
   honest. Leaving a channel wearing a different network's logo is not.

### Compositing

Jellyfin re-encodes channel images and flattens alpha **onto white**, so a
white-on-transparent logo vanishes. Composite onto a backdrop chosen from the
artwork's own mean luminance: `#141414` for light logos, `#f2f2f2` for dark.
Cap the long edge at 400 px.

### Keeping them organised

* **One file per channel, `.png`, no `.jpg` twin.** Enforced by the `uniq -d`
  check above.
* **Name-keyed, so renumbering is free** — never introduce number-keyed paths.
* `icon-archive export` after every install, so the new art is captured
  content-addressed. This is the only thing here that cannot be rebuilt.
* Leave **dynamic event slots alone** — `NBA 07`, `Soccer PPV 42`, `UEFA 16`,
  `SKY SPORT BUNDESLIGA 7`. The channel's identity changes per fixture, so a
  bespoke logo would be wrong by the next event. On CT 112 that is 567 of the
  625 channels that share an image; treating them as defects overstates the
  problem tenfold.
* **Never run `icon-archive export` while a known-bad state exists.** On
  2026-08-02 it captured misaligned logos, the icon host served them, CT 112
  re-imported from it, and the corruption was laundered into the source of
  truth. Later checks validated against the corrupted source and passed.

---

## Procedure: make Jellyfin pick up changed artwork

Jellyfin caches artwork separately, keyed by its own item GUIDs. It does not
notice that a source file changed.

```bash
pct exec 112 -- docker stop jellyfin-npvr
pct exec 112 -- cp -a /srv/jellyfin-npvr/jellyfin/config/data/jellyfin.db \
                      /srv/jellyfin-npvr/jellyfin/config/data/jellyfin.db.bak-icons-$(date +%Y%m%d)
pct exec 112 -- python3 /root/icon-clear-jellyfin-channels.py    # push it first
pct exec 112 -- docker start jellyfin-npvr

curl -X POST -H "Authorization: MediaBrowser Token=$KEY" \
  http://192.168.9.219:8096/ScheduledTasks/Running/bea9b218c97bbf98c5dc1303bdb9a0ca
```

~25 minutes for 997 channels. Three rules, each learned the hard way:

* **Clear the rows *and* the files, together.** Deleting the files alone leaves
  a `BaseItemImageInfos` row pointing at a missing path; Jellyfin concludes it
  already has the image and never re-fetches. 997 channels blank, permanently,
  surviving restarts. If you cannot clear both halves, clear neither.
* **Clear all of them, not a subset.** A partial clear re-fetches nothing and
  leaves those channels with no artwork at all.
* **`POST /Items/{id}/Refresh` does not populate Live TV channel artwork**,
  whatever `imageRefreshMode` you pass. Only the guide-refresh path does.

---

## Verification — the gate

Three hashes localise any artwork fault in one pass: the file on disk, the
bytes `channel.icon` returns, and the bytes Jellyfin renders.

| disk vs served | served vs rendered | diagnosis |
|---|---|---|
| equal | equal | correct |
| equal | **differ** | Jellyfin cache stale → clear all + one refresh |
| **differ** | equal | NextPVR picked another file → the `.jpg` trap |

The scripts live on the **host**, in `pve-01-docs/scripts/`; push them in
before running, since CT 112 has no copy of the repo:

```bash
for s in icon-audit-three-layer icon-verify-enduser epg-mapping-check \
         icon-clear-jellyfin-channels; do
  pct push 112 /root/pve-01-docs/scripts/$s.py /root/$s.py
done

# layers 1-3: file on disk vs what NextPVR serves
pct exec 112 -- python3 /root/icon-audit-three-layer.py

# layer 4: what Jellyfin actually renders, over HTTP, for all 997
pct exec 112 -- python3 /root/icon-verify-enduser.py
```

Expect `{"MATCH": 997}` and an empty mismatch list.

**Then look at it.** After 997/997 matched byte-for-byte, a contact sheet still
found eight channels wearing another network's logo — LAFF TV showing PLTV,
DECADES showing Newsy, AFV Family showing WLIW21. Hash equality proves the
pipeline is consistent; it says nothing about whether the artwork is *right*.

```bash
# renders a contact sheet of what Jellyfin serves, ordered by channel number
python3 /root/pve-01-docs/scripts/icon-contact-sheet.py 100 250 /tmp/sheet.png
# (runs on the host -- it talks to Jellyfin over HTTP)
```

Sweep the blocks the household actually watches, in ranges of ~150.

### Checking the probe itself

Two failure modes have masqueraded as total breakage:

* `channel.icon` wants `CHANNEL.oid`, **not the channel number**. Passing
  numbers returns 404 for everything and reads as "NextPVR is serving nothing".
* A `200` is not enough. Two channels returned `200` at exactly 5,586 bytes —
  the placeholder. **Compare byte counts, not status codes.**

A result that says *everything* is broken usually means the measurement is
broken.

---

## Feeding CT 112

* **EPG**: `epg-sync-ct112.timer` on the host, 12:28 daily, pushes the XMLTV
  generated on CT 105.
* **Playlist**: there is **no timer**. `playlist.m3u` is pushed by hand and was
  stale for a day without anyone noticing. Propagating it makes NextPVR
  re-import at its next scan, and whether that preserves the icon files is
  **still untested** — snapshot with `icon-archive export` first, then measure
  with `icon-verify`.
* **Icon host** (`icon-host.service` on the host) serves curated artwork so a
  re-import pulls good logos rather than provider placeholders. That inversion
  is the whole reason it exists.

### Config traps that have bitten twice

* **Trailing whitespace in paths.** NextPVR stored `/config/epg.xml ` and then
  failed `File.Exists` silently, reporting `[0 inserted]`. Check with `cat -A`.
* **Identifiers derived from content change when the content changes.**
  Removing that trailing space changed the EPG source id *and* every
  per-channel mapping id beneath it.
* `EPGUpdateTime` fires on NextPVR's internal clock, which has its own
  timezone. Verify timing by observation, not by reading the setting.

---

## Rollback

Every step above is reversible if the snapshots were taken.

| Broke | Restore |
|---|---|
| numbers / names | `npvr.db3.bak-<purpose>` (stop NextPVR first) |
| icon files | `media/channels.bak-precontam/`, or `icon-verify repair ct112-nextpvr` |
| a quarantined `.jpg` | `media/channels.quarantine-<date>/` |
| Jellyfin image rows | `jellyfin.db.bak-icons-<date>` (stop Jellyfin first) |
| artwork generally | `icon-archive extract /root/icon-archive/extracted` |

Move files, do not delete them. Copy databases to `.bak-<purpose>` before
edits. Every destructive step in this area has been reversible so far, and
that is the only reason the 2026-08-02 corruption was recoverable.

---

## Current state, 2026-08-03

* 997 channels, new numbering, guide ordering correct, favourites-first off
* EPG present across the lineup
* **Artwork: 997/997 rendered images byte-identical to source**, verified over
  Jellyfin's HTTP API, then eyeballed by contact sheet across 100–760
* 129 duplicate `.jpg` files quarantined
* 43 curated logos installed this session (CSN → NBC Sports regionals, 6 CW
  affiliates by call sign, 44 Bally Sports regionals unblocked by the
  quarantine, Kabel 1, MDR/SWR regionals, Curiosity, Spiegel, and 3 generated
  wordmarks where no upstream logo exists)
* Remaining shared images: 594, of which **567 are dynamic event pools** and
  correctly shared. ~27 are same-brand variants (PBS affiliates, BR regional
  feeds, Bally Cincinnati/Prime Ticket) with no per-feed logo upstream.
