# Driving NextPVR from the command line

NextPVR looks like it has no API for channel management. It does — the web UI
is a thin client over HTTP endpoints, and every one of them is reachable with
curl. Worked out 2026-08-02 while renumbering CT 112, after a day of assuming
the web UI was the only route.

## The thing that wasted the most time

There are **two** paths, and they are not the same:

    /service?method=...        the documented API: sessions, recordings, channels
    /services/service?method=  what the web UI calls: scanning, settings, EPG

`channel.scan`, `device.scan`, `scan.start` and four other guesses all returned
empty on `/service`. They do not exist. The real methods live on
`/services/service` and are named `setting.scan.*` and `system.epg.*`.

To find any endpoint, read the UI's own source inside the container:

```bash
pct exec 112 -- docker exec nextpvr-live sh -c \
  'grep -rhoE "service\?method=[a-zA-Z.]+" /app/wwwroot | sort -u'
```

`/app/wwwroot/iptv.html` covers channel scanning; `settings.html` the rest.

## Authentication

Same session flow as the documented API — PIN `0000`, no username needed:

```python
init = GET /service?method=session.initiate&ver=1.0&device=<anything>
# take <sid> and <salt> from the XML
md5  = md5(":" + md5("0000") + ":" + salt)
GET /service?method=session.login&md5=<md5>&sid=<sid>
# then pass &sid=<sid> on every later call
```

The web UI's `admin` login is **not** required for any of this.

## Rescanning an IPTV source

The sequence, all on `/services/service`:

```
setting.scan.start   &format=json&source_id=<id>&m3u=<path>&xmltv=<path>
setting.scan.status  &format=json                     poll until complete:true
setting.scan.save    &format=json&groups=all          POST, JSON channel list as body
```

Four traps, each of which cost a round trip:

1. **`m3u=existing` does not re-read the file.** It reuses NextPVR's cached
   channel data and returns the *old* numbers. Pass the real path
   (`/config/playlist.m3u`, URL-encoded).
2. **`save` is a POST**, with the `channels` array from the scan status as the
   JSON body. A bare GET returns `{"stat":"Failed","code":2,"msg":"Invalid Args."}`.
3. **`groups` is a keyword, not a list.** The UI sends `all` or `none`, or
   specific group names. A comma-joined list of every group is rejected.
4. **Scan status is consumed by reading it.** Poll once, keep the payload — a
   second read returns empty and the channel list is gone.

## Renumbering: existing channels never change

`tvg-chno` is honoured **only for channels NextPVR does not already have** —
identical to Threadfin's behaviour with `x-channelID`. A rescan over an
existing lineup reports the old numbers and changes nothing.

To adopt a renumbered lineup, the channels must be new:

```bash
# back up first: npvr.db3, and the icon directory
sqlite3: delete from EPG_EVENT; delete from CHANNEL;
# then scan, then save
```

Deleting alone is not enough — NextPVR does **not** re-import on restart. It
needs the scan call above. That combination is what finally worked.

## After a fresh import, EPG needs rebuilding by hand

A scan imports channels with **empty `epg_source` and `epg_mapping`**, so
`system.epg.update` reports the notorious `[0 inserted, 0 updated, 0 skipped]`
— the tell that a job ran and did nothing.

`setting.epg.automap` exists but rejected every argument shape tried. What
works is writing the mappings directly, built from the playlist's `tvg-id`:

```xml
<epg>
  <source>XMLTV</source>
  <file>/config/epg.xml</file>
  <mapping_id>{tvg-id}</mapping_id>
  <mapping_name>{channel name}</mapping_name>
</epg>
```

XML-escape both values and parse each blob before storing it. One unescaped
`&` in a channel name aborts the entire EPG update for every channel while
still reporting success — that failure cost a day on 2026-08-01.

Then:

```
system.epg.update   &format=json
system.epg.status   &format=json
```

**Judge it by the insert count, never the status.** `[32683 inserted]` is
success; `[0 inserted]` with `"stat": "ok"` is failure.

## Other useful endpoints

```
setting.epg.sources    &format=json    lists configured EPG sources and ids
system.epg.empty                       clears all guide data
setting.channel.update                 per-channel edits
channel.icon           &channel_id=<OID>   note: OID, not channel number
```

`channel.icon` takes the **CHANNEL.oid**, not the displayed number. Passing
numbers returns 404 for everything and looks exactly like a total failure.
