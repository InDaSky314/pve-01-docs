# Three-way split: build status (2026-08-01)

Companion to `dual-backend-design-20260801.md`, which is the design. This
is what actually got built and what is left.

## Built and verified

| CT | Name | IP | Services |
|---|---|---|---|
| 110 | `jellyfin-live` | 192.168.9.195 | jellyfin-live :8096, threadfin-live :34400 |
| 111 | `jellyfin-vod` | 192.168.9.134 | jellyfin-vod :8096 |
| 112 | `jellyfin-npvr` | 192.168.9.219 | jellyfin-npvr :8096, nextpvr-test :8866 |

Each is its own LXC, so `:8096` on all three does not collide.

Verified independently, not taken from the build report:

- All five web UIs answer (302 = fresh-install setup redirect).
- **Threadfin on CT 110: 996 channels, 996 unique names, 0 duplicates.**
- **NextPVR on CT 112: 997 channels**, XMLTV attached.
- `onboot=1` on all three CTs and `restart: unless-stopped` in every
  compose file — required, because the host mains-cycles daily.
- CT 111 sees 236,394 `.strm` files.

### The duplicate-channel finding

Threadfin's `urls.json` already held 957 unique channels with **no**
duplicates. The twin channels in §14 were never a lineup problem — they
were an artefact of two tuners feeding one Jellyfin. One tuner per stack
makes de-duplication inherent rather than something to maintain.

## VPN routing (wgclient1, Swiss)

All three new stacks now egress **156.146.62.37, Zürich**, same as CT 105.

This took two changes on the router, and the second is the non-obvious one:

1. Added the three CT MACs to `route_policy.@rule[1].from_mac`
   (`media-core(ch)` → `wgclient1`).
2. **Also added them to `route_policy.@rule[0].from_mac`.** Rule 0 is a
   *negated* match (`from='!src_mac9810'`) that sends everything **not**
   listed to `wgclient3` (US). Being absent from rule 0 meant the new CTs
   matched it first and never reached rule 1. CT 105 works precisely
   because its MAC is in rule 0's exclusion list.

Adding to rule 1 alone changed nothing — verified by egress IP before and
after. Applied with `uci add_list` + `uci commit`, then
`/etc/init.d/vpn-client restart`. `/etc/init.d/route_policy` does not
exist on this firmware.

Backup: `/etc/config/route_policy.bak-20260801` on the router.

**Caveat:** these were made over UCI, not the web UI. Per the standing note
about GL.iNet UI/CLI divergence, check the UI reflects them before relying
on it, and re-check after any firmware update.

## Production changes

**NextPVR plugin disabled in production Jellyfin.** Moved, not deleted:

```
/srv/media-core/jellyfin/config/plugins/NextPVR_13.0.0.0
  -> /srv/media-core/jellyfin/config/plugins-disabled-20260801/
```

Jellyfin restarted, healthy. Production is now Threadfin-only, which is
the single-backend world its automation (`media-core-guard`,
`threadfin-tuner-watchdog`) was written for. To revert: move the directory
back and restart.

The production `nextpvr` container is still running but is no longer
attached to Jellyfin. Left up deliberately — decommission only after CT 112
has proven itself.

## Still open

1. **CT 112 has not recorded anything yet.** It is configured, not proven.
   Record something end-to-end before trusting it.
2. **VOD on CT 111 is a periodic copy, not a live mount.** CT 105's thin LV
   cannot be double-mounted, so 236k `.strm` files are rsynced to a host
   directory and bind-mounted in. A timer refreshes it daily at 13:45
   (`vod-sync-ct111.timer`, deliberately not overnight — the host is off
   22:24–04:57). New VOD appears within a day, not immediately.
3. **New stacks have no libraries configured yet** beyond the tuner wiring.
4. **No automation on the new stacks**, by design. Do not extend
   `media-core-guard` to them without first fixing its single-recorder
   assumption for the new paths.
5. **Provider concurrency is still unmeasured.** Four stacks can now reach
   the provider through one tunnel. Test one at a time until measured.

## Note on the guard fix

`pre-recording-guard` was fixed earlier today (commit `1c0c96c`) so it no
longer "recovers" recordings it cannot measure. That fix is what makes a
second recorder safe to run at all — without it, any NextPVR recording
visible to production Jellyfin got cancelled and recreated every minute.
Verified in production: an 800 MB single-file recording where the previous
attempt produced 18 fragments.

---

## Afternoon session (2026-08-01, later)

### CT 112 recorded successfully — the split is proven

`Second Chance Pets`, ch102, 15:00-15:30. Result: **one file,
1,133,419,666 bytes, status Ready, no "cancelled early" reason.**

The same test on production the previous night produced 18 fragments. The
isolated stack behaves as a DVR should. CT 112 is no longer unproven.

### Shared recordings layout

CT 105's `/srv/media-core` is on a thin LV that cannot be double-mounted,
so the shared area lives on the Proxmox host:

```
/srv/shared-recordings/
    nextpvr/     <- CT 112 writes (only writer)
    threadfin/   <- CT 110 writes (only writer)
```

Mounted into CTs 110/111/112 and added as a Jellyfin library "Recordings"
on each, so a recording made by one stack is playable from any of them.
Separate writers, shared readers: merging the *write* paths would recreate
the ambiguity that made the guard bug so hard to attribute.

**Known gap:** read-only is enforced on CT 111 only (`ro=1`). CTs 110 and
112 mount the parent read-write because each needs write access to its own
subdirectory. Consequence: CT 110's Jellyfin can delete CT 112's
recordings and vice versa. The correct shape is two mounts per CT — parent
read-only, own subdirectory read-write. Not yet done.

### Recording persistence — a near miss

CT 112's NextPVR was writing to `/root/recordings` **inside the
container's writable layer**, not a bind mount. `docker compose up
--force-recreate` destroys that, and the container was recreated twice
that afternoon. The 1.13 GB recording survived only because it was made
after the last recreate. Copy-then-verify-bytes was made an explicit
precondition before any further recreate.

Check the recording directory is on a bind mount whenever a DVR container
is built. NextPVR does not default to one.

### Clocks

`nextpvr-live` now reads CEST, matching the host, in both the container
and NextPVR's own rendering.

Two separate faults, found in sequence:
1. The container ignored `TZ=Europe/Berlin` in compose. It needed
   `/etc/localtime` bind-mounted. Note `docker restart` does NOT apply new
   env vars — that needs `up -d --force-recreate`.
2. Even then NextPVR still rendered UTC, because it stores its **own**
   timezone captured at setup time. That is a NextPVR setting, not a
   container one.

**Recordings were never at risk from this.** Scheduling is epoch-based and
internally consistent — the 15:00 CEST recording fired correctly while
NextPVR displayed 13:00. The risk was EPG *ordering*: `EPGUpdateTime`
fires on NextPVR's internal clock, so 12:35 was ingesting at ~14:35,
after Jellyfin's 12:50 guide pull, leaving Jellyfin with day-old data.

**Verify EPG timing by observation, not config values.** Config numbers
were wrong twice in one day — the sync schedule and the container TZ — and
in both cases the value looked right while behaviour differed.

### Production

Duplicate channels are gone: 998 -> **996 channels, WKOW x1, WMSN x1**.
Removing the NextPVR plugin does not purge channels already in Jellyfin's
database; that needs a guide refresh, which was triggered manually.
Production tuner config verified intact (`hdhomerun -> 127.0.0.1:34400`
plus the `xmltv` provider).

### Still open

1. Read-only mount refinement (above).
2. Production recordings are not in the shared parent; CT 105 was
   deliberately kept read-only to agy.
3. New servers not yet added to Wholphin, so no A/B has happened on the TV.
4. Only ONE provider stream exists estate-wide. TiviMate, production, CT
   110 and CT 112 all contend for it. **Comparisons must be sequential.**
   With one stream, three Live-TV-capable stacks is more than can be used
   at once — once a backend is chosen, retiring the other simplifies this.

---

## CT 110 retired (2026-08-02)

`jellyfin-live` is **stopped**, with `onboot=0` so it stays down through the
nightly mains cycle. Nothing was deleted.

Reason: its Threadfin backend duplicates production, and the thing it existed
to be a control for — the recording-fragmentation mystery — turned out to be
the `pre-recording-guard` bug, not Threadfin. With that answered, a clean-room
Threadfin control buys little, while costing disk (3.4 GB of guide artwork
already), a share of the single provider stream, and surface area in every
guard. It had also never recorded anything.

Retired from monitoring in the same change, deliberately. `stack-monitor.py`
reports `epg_age_hours = 999.0` when it cannot stat the file and `stack_up = 0`
when the endpoint is down, so leaving CT 110 in its target lists would have
fired `epg-freshness-stale` and `stack-health-down` every cycle for an outage
we chose — training the owner to ignore alerts that had only just been
repaired. The three `jellyfin-live` entries are commented in place.

**To revive:**

```
pct set 110 --onboot 1 && pct start 110
```

then restore the `jellyfin-live` entries in `/root/bin/stack-monitor.py`
(backup: `.bak-20260802`) and give it an EPG propagation job like
`epg-sync-ct112`, which it never had.

`dvr-clean-shutdown` needs no change — it detects a stopped container and
skips it explicitly rather than treating it as unmeasurable.
