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
