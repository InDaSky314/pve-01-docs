# Runbook: changing the channel lineup

How numbering, naming and artwork actually work across both ecosystems, so a
lineup change does not have to be re-learned. Written 2026-08-02 after a
renumber that went mostly right and taught two expensive lessons.

Read `lessons-learned.md` first. Icon detail lives in
`channel-icons-runbook.md`; this file covers the lineup itself.

> **CT 112 (NextPVR) has its own runbook: `nextpvr-stack-runbook.md`.**
> It covers renumbering, renaming, artwork build/organisation and the
> verification gates for that stack end to end. The two ecosystems store
> artwork and channel identity differently enough that mixing the procedures
> has caused real damage — use the one that matches the stack you are on.


---

## The one rule that matters most

> **Never rename a channel in the same change that moves it.**

`tvg-name` **is** the channel's identity — in Threadfin (`_uuid.key`), and in
Jellyfin, which matches channels by name. Change the name and you have not
edited a channel, you have deleted one and created another. It loses its
number history, its Jellyfin entry, and any locally cached artwork.

Worse, **a rename is not cleanly reversible.** On 2026-08-02 five channels were
renamed, a sync ran, and Jellyfin dropped the originals. Reverting the names
made them *new again* — Jellyfin had already forgotten them. Recovery took
three sync/refresh cycles and left orphaned entries behind.

Move first. Verify. Rename later, on its own, if at all.

---

## Where the lineup is defined

Everything comes from **one file**:

    /srv/media-core/sync/config.json   on CT 105

`live_selections` is a list of groups, each with a `start_chno` block. There is
one lineup for the whole estate — CT 112 consumes the same generated playlist.
Editing this is a production change.

Channel numbers are assigned by walking each group in order from its
`start_chno`. Never hand-edit numbers anywhere else: the 12:25 job rewrites
them from this file.

### The three shapes of `ids`

| shape | meaning | renames? |
|---|---|---|
| `{"324963": "US: CNN 4K"}` | dict: id -> **display name** | **yes — the value IS the name** |
| `[324963, 324922]` | list: ids only, provider name kept | no |
| absent, with `category` | whole provider category by regex | no |

**The dict form renames.** That is the trap. If you move channels into a
dict-shaped group you must supply their *existing* provider names verbatim, or
you have silently renamed them. Get them from the playlist:

```bash
pct exec 105 -- grep -oE 'tvg-name="[^"]+"' /srv/media-core/threadfin/conf/playlist.m3u
```

Group names must also be unique-ish: two selections sharing a group name make
the overflow checker compare the group against itself and emit a false
"overflows its block" warning. `German Cable & Entertainment` does this today.
Harmless, but do not chase it.

---

## The pipeline, in order

```
config.json  (CT 105)
   │  media-core-sync.service        12:01 daily
   ▼
playlist.m3u + epg.xml               tvg-chno, tvg-name, tvg-logo
   │  media-core-xepg.service        12:25 daily
   ▼
Threadfin xepg.json                  rewrites x-channelID + x-group-title only
   │  Jellyfin "Refresh Guide"
   ▼
production Jellyfin channel list
```

Run by hand after a config edit:

```bash
pct exec 105 -- systemctl start media-core-sync.service    # ~90s
pct exec 105 -- systemctl start media-core-xepg.service    # restarts Threadfin
# then trigger Jellyfin's Refresh Guide (key: /srv/media-core/.jellyfin_api_key)
```

`media-core-xepg` restarts Threadfin through `threadfin_ctl`, which refuses
while a recording is in progress and verifies :34400 comes back. It routinely
needs 2 attempts — that is normal, not a fault.

**Jellyfin's Refresh Guide takes ~20 minutes on production** and adding new
channels can need more than one cycle. Do not assume one pass is enough.

---

## Verifying a change — at the right layer

Each layer can agree with the one below and still be wrong. Check all four.

```bash
# 1. config parses and the group is right
pct exec 105 -- python3 -c "import json;d=json.load(open('/srv/media-core/sync/config.json'));print(len(d['live_selections']))"

# 2. playlist: total AND per-group counts must reconcile
pct exec 105 -- journalctl -u media-core-sync.service -n 50 -o cat | grep '^playlist:'

# 3. xepg: "0 not in current playlist" is the number that matters
pct exec 105 -- journalctl -u media-core-xepg.service -n 20 -o cat | grep '^xepg:'

# 4. Jellyfin: compare its channel names against the playlist
```

**Reconcile the totals, not just the samples.** A renumber on 2026-08-02 looked
perfect at the top of every block while the playlist had silently dropped 8
channels — caught only because the group breakdown summed to 989 instead of
997. Two separate bugs that day were found this way and by nothing else.

`xepg: N not in current playlist` is the rename detector. Non-zero after a
lineup edit means something was renamed, deliberately or not.

---

## Artwork, per ecosystem

The two stacks store artwork completely differently. This is the single
biggest source of surprise.

### Production (Threadfin + Jellyfin)

* Artwork is a **URL** in `tvg-logo`, pointing at the provider
  (`photo-tmdb.com`), passed straight through by `xtream-sync.py`.
* All 996 xepg entries carry **`x-update-channel-icon: False`**, so Threadfin
  never re-offers new icons.
* Jellyfin stores each image as **either a local file or the remote URL** —
  roughly 730 local / 252 remote. Only the local ones are truly cached.
* **Renumbering cannot break it**: `renumber-xepg.py` touches only
  `x-channelID` and `x-group-title`.

**The fragile part:** the provider has since replaced many logo URLs with the
DirecTV GO placeholder (5,586 bytes, shared by 134 channels). For 14 channels —
including Green Bay ABC 2, Big Ten and ESPN — Jellyfin still serves *better*
art it cached before that happened. That art exists **nowhere else**. A
Jellyfin image-cache wipe would lose it permanently.

Hence `icon-archive`. Run it before anything that can trigger a re-fetch.

### CT 112 (NextPVR + Jellyfin)

* Artwork is **files on disk**, name-keyed:
  `nextpvr/config/media/channels/<name>.png`, with `:` `/` `|` stripped.
* NextPVR **populates icons once at channel import and never re-fetches**.
  Clearing the directory leaves you with nothing.
* Jellyfin here keeps ~856 of 997 as NextPVR URLs and ~141 as local files.
* Because storage is name-keyed, **renumbering is safe** — but **renaming
  orphans the icon file**, since the filename is derived from the name.

---

## Tools

```bash
icon-archive export              # snapshot all stacks; content-addressed, name-keyed
icon-archive list                # summary + how many channels' art exists on one stack only
icon-archive extract <dir>       # write name-keyed PNGs, ready to install
icon-verify check <stack>        # unchanged / degraded / improved vs the archive
icon-verify repair <stack>       # NextPVR stacks only -- see limitation below
lineup-watch (Sun 12:40)         # weekly new/dropped/renamed channels + new movies
```

**Superseded 2026-08-03:** this said repair worked only on NextPVR stacks.
It now works on production too — via the icon host, which is exactly the
"local hosting with `tvg-logo` rewritten" route this note anticipated. See
*Procedure: install artwork on production* below. `icon-verify repair` itself
is still NextPVR-only; production is repaired by republishing through the
icon host.

---

## Icon backup and restore — do this around every lineup change

Artwork is the only thing here that can be lost irreversibly. Numbers and
names can always be rebuilt from `config.json`; a channel logo that existed
only in Jellyfin's cache cannot.

### Before any change that can trigger a re-fetch

```bash
icon-archive export
cp /root/icon-archive/manifest.json /root/icon-archive/manifest.pre-<change>.json
```

"Can trigger a re-fetch" means: a guide refresh, a channel re-import, a
Jellyfin restart or cache clear, a NextPVR playlist replacement, or any
rename. When unsure, run it — it is read-only, takes seconds, and
content-addressed storage means repeat runs cost nothing.

### After the change

```bash
icon-verify check production-jellyfin
icon-verify check ct112-nextpvr
```

Three outcomes per channel:

* **unchanged** — matches the archive
* **degraded** — live is now a shared placeholder where the archive has unique
  art. This is the loss case.
* **improved** — live is unique where the archive had a placeholder; the
  archive is stale, so re-run `icon-archive export` to capture it.

### Restoring

```bash
icon-verify repair ct112-nextpvr        # writes archived files back
icon-archive extract /root/icon-archive/extracted   # name-keyed PNGs for a rebuild
```

`extract` prefers CT 112's artwork, then production's, and where several
stacks hold art for the same channel **the largest file wins** — consistently
the better image here. Filenames already have `:` `/` `|` stripped, so the
output drops straight into a NextPVR icon directory.

**Production is now restorable too**, through the icon host — see
*Procedure: install artwork on production*. `icon-verify repair` still writes
files only on NextPVR stacks; production is restored by publishing the art to
the icon host and republishing the playlist.

### What the archive holds

Content-addressed blobs under `/root/icon-archive/blobs`, keyed by md5, with
`manifest.json` mapping channel name -> blob per stack. Keyed by **name**, so
it survives any renumbering. Roughly 560 unique images, ~10 MB, covering all
three stacks.

As of 2026-08-02, **257 channels' custom artwork exists only in production's
Jellyfin** and nowhere else. That is what this protects.

### Rebuilding a stack from the archive

1. `icon-archive extract /root/icon-archive/extracted`
2. Copy into the new stack's NextPVR icon directory
3. Follow `channel-icons-runbook.md` to make Jellyfin pick them up — clear
   **all** channel image rows and run **one** guide refresh to completion.
   Clearing a subset does not re-fetch.

---

## Procedure: install artwork on production

**This works now.** The runbook previously said repair was "detection only,
not built" for production — that was true while artwork arrived as provider
URLs. The icon host closed it. Done end to end on 2026-08-03; both stacks now
render byte-identical artwork for all 996 shared channels.

The route is: **CT 112 is the source of truth, the icon host publishes it, and
production's playlist points at the icon host.**

```
CT 112 media/channels/<name>.png     curated, verified
   │  (export the *effective* file per channel — NextPVR prefers .jpg)
   ▼
/root/icon-archive/extracted/        served by icon-host.service :8100
   │  xtream-sync pick_logo() prefers a curated URL over the provider's
   ▼
playlist.m3u + epg.xml               tvg-logo / <icon src>
   │  media-core-xepg repoints xepg.json
   ▼
production Jellyfin
```

```bash
# 1. export CT 112's effective artwork, keyed the way pick_logo() looks it up
pct exec 112 -- python3 /root/ct112-effective-icons.py      # scripts/
pct pull 112 /root/ct112-icons.tgz /root/ct112-icons.tgz
tar xzf /root/ct112-icons.tgz -C /root/
cp -a /root/icon-archive/extracted /root/icon-archive/extracted.bak-$(date +%Y%m%d)
cp /root/ct112-effective-icons/* /root/icon-archive/extracted/
curl -s http://192.168.9.11:8100/healthz          # expect icons == channel count

# 2. regenerate and republish
pct exec 105 -- systemctl start media-core-sync.service    # "icons: N channels using curated artwork"
pct exec 105 -- systemctl start media-core-xepg.service    # "N channels repointed at the icon host"

# 3. clear production Jellyfin's channel artwork -- rows, files AND cache
pct exec 105 -- docker stop jellyfin
pct exec 105 -- python3 /root/prod-clear-channel-images.py
pct exec 105 -- rm -rf /srv/media-core/jellyfin/cache/images
pct exec 105 -- docker start jellyfin
# then trigger Refresh Guide; ~20 minutes for 996 channels

# 4. verify at the layer the user sees
pct exec 105 -- python3 /root/prod-verify-icons.py         # expect {"MATCH": 996}
python3 scripts/icon-contact-sheet-prod.py 100 250 /tmp/p.png   # then LOOK at it
```

### Three traps, each of which cost a full 20-minute cycle

**Clear the HTTP image cache too.** `config/cache/images` is Jellyfin's
processed-image cache and no database row references it, so the usual
"clear both halves" rule does not catch it. Left in place it will re-serve
the old logo after a correct re-fetch.

**Watch the icon host's `Cache-Control`.** It used to send `max-age=86400`.
113 of 996 channels came back with pre-fix logos through a *complete*
clear-and-refresh, because Jellyfin served them from its HTTP cache and never
asked. The source, the playlist, the XMLTV and Jellyfin's own parsed copy were
all correct — only the bytes were stale. Now `max-age=60, must-revalidate`.

**Do not let Threadfin's restarts race the refresh.** Run sync and xepg to
completion, confirm Threadfin serves the new logos
(`curl :34400/m3u/threadfin.m3u | grep -c 8100`), and only then clear and
refresh. A refresh started during xepg's Threadfin restarts produced 342
stale channels.

### Production's own artwork is not a safe reference

The obvious merge rule — "keep production's image where it is unique and
CT 112's is shared" — imported production's *own* misalignment: `US: MTV HD`
was serving Antenna TV's logo, uniquely and confidently. Of five candidates
the rule selected, one was actively wrong and three were the same image
CT 112 already had.

**Take CT 112 wholesale and check the exceptions by eye**, or verify each
candidate against what the channel should look like. Uniqueness is not
correctness.

---

## Adding a channel

1. Find its stream id — the weekly `lineup-watch` mail lists new ones, or
   query `get_live_streams` (needs `User-Agent: MediaCoreSync/1.0`, else 403;
   run it **inside CT 105**, whose Swiss egress the account expects).
2. Check `epg_channel_id` is non-empty. Empty means no guide data — fine for
   team feeds, but decide knowingly.
3. Add the id to the right group in `config.json`, respecting the block's
   headroom. **List form unless you have a reason** — it avoids the rename trap.
4. Run sync, then xepg, then Jellyfin Refresh Guide.
5. Verify at all four layers above; reconcile the totals.
6. Artwork: `scripts/icon-match.py` against tv-logos first. No match and it
   matters? Generate via agy in batches of **6** — 12 produced cropped text and
   invented words.
7. Install per `channel-icons-runbook.md`, then `icon-archive export` so the
   new art is captured.

## Moving a block

Change `start_chno` only. Do **not** touch names. Then sync → xepg → refresh,
and confirm `xepg: 0 not in current playlist`.

---

## Still unknown

* **CT 112's `playlist.m3u` has nothing refreshing it** (stale since Aug 1).
  Propagating it makes NextPVR re-import channels, and whether that preserves
  or overwrites the 998 icon files is **untested**. Snapshot with
  `icon-archive export` first, then measure with `icon-verify`.
* Whether Jellyfin reliably adds renamed-then-reverted channels, or needs the
  tuner re-probed. Observed taking three refresh cycles on 2026-08-02.
