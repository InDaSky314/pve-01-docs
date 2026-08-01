# NextPVR guide data and channel icons: what was actually wrong (2026-08-01)

Continues `split-build-status-20260801.md`. Everything here is CT 112
(`jellyfin-npvr`, 192.168.9.219) unless stated.

## 1. EPG: 240 channels -> 997, and why every earlier theory was wrong

**Symptom:** most channels had no guide data, and the count kept *falling*
(240 -> 45) as existing events expired.

**Root cause:** ten channel names contained a raw `&`:

```
<mapping_name>A&E HD</mapping_name>                  <- invalid XML
<mapping_name>HISTORY & WARFARE</mapping_name>
<mapping_name>CRIME & INVESTIGATION HD</mapping_name>
```

NextPVR stores each channel's EPG mapping as an **XML blob in
`CHANNEL.epg_mapping`** and parses it with `XmlDocument.LoadXml`. One
unescaped ampersand throws `XmlException: An error occurred while parsing
EntityName` and **aborts the entire EPG update** — while the API cheerfully
reports `EPG Update complete. [0 inserted, 0 updated, 0 skipped]`.

Ten bad rows out of 997 destroyed guide data for all of them, silently.

Fix: escape to `&amp;` (regex that skips already-valid entities), verify
every blob parses, restart. Result: `[32982 inserted]`, **997/997 channels**.

Origin: the bulk channel rename wrote `mapping_name` without XML-escaping.
**After any bulk channel rename, verify every `epg_mapping` blob parses as
XML before trusting the next EPG update.** It fails silently otherwise.

### Theories that were wrong, and the lesson

Three plausible hypotheses were stated with more confidence than the
evidence supported:

1. *"The provider doesn't publish guide data."* No — `epg.xml` had
   programmes for 807 channels all along.
2. *"`PreferredLangXMLTV=nor` filters it out."* Never actually tested. The
   count drop cited as evidence was just expiry, because ingest was
   inserting **zero** either way. (Setting is now `eng`; still untested,
   left as-is since coverage is complete.)
3. *"NextPVR skips unchanged files."* Pushing a fresh `epg.xml` changed
   nothing.

What cracked it: **reading the log during an update** instead of reasoning
about counts. The exception was there the whole time.

> Do not infer a mechanism from a metric without confirming the mechanism
> ran. `[0 inserted]` is the tell — the number moving (or not) says nothing
> if the job never did any work.

## 2. Channel icons: three separate traps

Icons are **files on disk**, not database rows. `CHANNEL` has no icon
column — it is `oid, name, number, epg_source, epg_mapping, minor`.

```
/srv/jellyfin-npvr/nextpvr/config/media/channels/<Channel Name>.png
```

**Trap 1 — filenames are colon-stripped.** NextPVR looks for
`Madison ABC 27 (WKOW).png`. Write `Madison: ABC 27 (WKOW).png` and it is
silently ignored. 36 of the first 37 installs were invisible for this
reason.

**Trap 2 — Jellyfin flattens alpha.** The CBS logo is pure white
(255,255,255) on transparency. Jellyfin re-encodes channel images and
flattens onto white, so it vanished entirely. Every installed logo is now
composited onto a backdrop chosen from its own luminance (`#141414` for
light artwork, `#f2f2f2` for dark) so it cannot disappear downstream.

**Trap 3 — NextPVR never re-fetches.** Icons are populated once at channel
import. Clearing `media/channels` does **not** trigger a re-fetch — it
leaves you with nothing (verified: `channel.icon` returned 404 across the
board). Restore from backup and write files directly instead.

**And Jellyfin caches separately.** A guide refresh updates programmes but
not channel artwork. To make new icons appear:

```
stop jellyfin, rm -rf config/metadata/channels/* cache/images/* cache/channels/*, start
```

This is why icons "did not take" despite NextPVR serving them correctly —
verification was happening at the NextPVR layer, not at the layer the user
actually sees.

### Result

37 -> **439 channels with real artwork** (of 996). Everything routinely
watched: locals, ABC/NBC/CBS/FOX affiliates across markets, Bally/FanDuel,
B1G, NFL Network, MLB Network.

Method: index `tv-logos` once via the GitHub trees API (10,777 files), match
offline, download only what exists. The first attempt guessed URLs and
fetched speculatively — ~8 requests per channel, almost all 404 — and was
far too slow. **Index first, match locally.**

Scripts: `scripts/logofix.py` (indexed matcher, flattening, correct
filenames).

### Do not chase the remaining ~557

They are overwhelmingly `Soccer PPV 42`, `UEFA 16`, `NBA 05`,
`SKY SPORT BUNDESLIGA 7 (SAT)` — dynamically assigned event slots and
obscure foreign feeds with no logo in any public source.

**Production has the identical placeholder on 503 of the same channels.**
Verified by exporting all 996 rendered images from production's Jellyfin
and comparing by MD5: only **29** had better artwork (copied across). The
belief that "production has icons figured out" does not hold — production
uses the same raw provider logos, its Threadfin image directories are
empty, and 134 of its channels share one DGO placeholder. CT 112 is now
*ahead* of production on icons.

## 3. Disk: fixing the EPG filled the disks

Going from ~2,000 to ~33,000 programmes made Jellyfin and NextPVR download
artwork for every one:

```
7.6 GB  jellyfin/config/metadata      (guide artwork)
1.6 GB  nextpvr/config/media/shows
```

CT 112's 16 GB disk hit 100% and **Jellyfin refused to start**:
`The path /config/data has insufficient free space. Available: 94MiB,
Required: 2GiB`. CT 111 hit the same wall harder — disk 100%, **Docker
died and both containers exited**, unnoticed for about an hour.

This is normal Jellyfin behaviour, not a leak: production carries **130 GB**
of metadata including **9.4 GB** of Live TV artwork.

Disks grown: CT 110 40 GB, **CT 111 79 GB** (236k VOD items), CT 112 40 GB.

**Guide artwork cannot be shared between Jellyfin instances.** It is stored
as `metadata/livetv/<instance-specific-item-GUID>/poster.jpg`, so the same
programme lands under a different directory name on every server. Bind
mounting one directory into several instances would not dedupe anything.
The real mitigations are: grow the disk, disable guide image downloads, or
retire one Live TV stack once a backend is chosen.

## 4. Still open

1. **CT 110 has never recorded.** Threadfin is the incumbent backend and
   remains unproven on the new stack. Scheduled manually by the owner.
2. **VOD scan incomplete** — 2,965 of ~236k when last checked, resumed
   after the disk grew. Verify it progresses rather than assuming.
3. **~557 channels on placeholder artwork** — see §2, deliberately not
   pursued.
4. **`PreferredLangXMLTV=eng`** on CT 112 is an untested change.
5. **Monitoring alerts exist but have never been observed firing.** A rule
   that never fires is worse than none. Force a condition and confirm the
   email arrives before trusting them.
