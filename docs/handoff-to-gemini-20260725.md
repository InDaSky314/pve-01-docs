# Handoff prompt for Gemini (Antigravity) — 2026-07-25

Copy everything below the line into Gemini.

---

You are picking up work on the **pve-01 Proxmox homelab** (Project
Media-Core). Claude Code did the previous shift; we alternate to spread
token usage. Read `/root/pve-01-docs/README.md` and
`/root/pve-01-docs/CLAUDE.md` first — they are the source of truth and
were just updated. Key docs:

- `docs/wholphin-fork-status.md` — the current focus
- `docs/router-rebuild-runbook.md` — router backup/restore + gotchas
- `docs/router-security-audit-20260725.md` — open security items

## Current objective: get the forked Wholphin APK running on the Chromecast

**Context.** The Live TV **no-audio** bug is NOT server-side. Three
server-side theories were tested and disproven:
1. DVB-subtitle interference — `ffprobe` shows no subtitle stream on
   affected channels.
2. Swapping channels 100/102 to "clean" provider streams — no change.
3. **NextPVR** was deployed and channels 100/102 served to Jellyfin
   through it instead of Threadfin — **audio problem persisted**.

So effort moved to the client. NextPVR is **parked, not removed** — leave
it alone for now; we will revisit only after the client fix is proven.

**The fork** lives at `/srv/media-core/wholphin` on the pve-01 host (not
CT 105). Three custom commits on top of upstream `cf3c00a1`:
`a5e605fe` (TiviMate-grade TsExtractor flags + ExoPlayer
`onTracksChanged` audio-fallback listener), `96b2f41b` (IPTV Audio Track
Recovery toggle under Settings → Experimental Settings), `ec562b4e`
(default that toggle ON).

## What Claude just did — do not redo

1. **Fixed the build.** `./gradlew assembleDebug` was failing on all three
   flavours with `Unresolved reference 'iptvRecovery'` at
   `PlayerFactory.kt:105`. Cause: `patch_pf_pref.py` anchored the
   `iptvRecovery` declaration to the `tunneling` read at ~line 137, but
   it is consumed at ~line 105 — a use-before-declaration, which Kotlin
   rejects. The declaration was relocated to sit beside `decodeAv1`
   (~line 92, before the use). Original saved as
   `PlayerFactory.kt.pre-20260725-declfix`.
   A rebuild was started; check `/root/wholphin-build2.log` for
   `BUILD SUCCESSFUL` / `BUILD FAILED` and re-run
   `cd /srv/media-core/wholphin && ./gradlew assembleDebug` if needed.

2. **Corrected two wrong claims in the docs:**
   - The fork is **NOT on GitHub**. All three commits were missing from
     `/root/wholphin` (the only clone with a GitHub remote), and
     `nk-sys-ops/wholphin` does not resolve with any credential on this
     box (the `github-mirror` deploy key is scoped to `pve-01-docs`; `gh`
     is authed as `InDaSky314`). Mitigated: pushed as branch
     `iptv-audio-fix` into `/root/wholphin`, plus a verified `git bundle`
     in `/root/wholphin-backups/`. **Do not claim GitHub is up to date.**
   - **The APK path in the old docs does not exist.** The project has
     build flavours, so `assembleDebug` emits
     `app/build/outputs/apk/<flavour>/debug/app-<flavour>-debug.apk`.
     Use the **`default`** flavour for the Chromecast (`firetv` is for
     Amazon devices, `appstore` is the Play Store build). List the
     directory rather than assuming a filename.

## Your next steps

1. Confirm the build succeeded and locate the **`default`**-flavour APK.
2. `adb connect 192.168.9.203:5555` — note **wireless debugging does not
   survive a Chromecast reboot** and the port is not stable; if it
   refuses, it must be re-enabled by hand in Settings → System →
   Developer options.
3. `adb -s 192.168.9.203:5555 install -r <default-debug apk>`
   (both the old Wholphin and official `org.jellyfin.androidtv` were
   already uninstalled, so the device is clean).
4. Sign in to Jellyfin, confirm **Settings → Experimental Settings →
   IPTV Audio Track Recovery** exists and is **ON** by default.
5. **Test audio on the known-bad channels: 133, 134, 313** (and 100/102).
   If audio works, that confirms the client-side root cause.
6. Commit any code changes in `/srv/media-core/wholphin`, and push to
   `/root/wholphin` so the work is not single-copy.

## Rules for this project

- **Verify, don't assume.** Claude found several documented claims that
  were untrue (GitHub state, APK path, "everything up to date"). Test
  things and report what you actually observed.
- **Confirm before touching shared infra.** CT 105 runs the live media
  stack the household uses. Nothing is streaming right now, but check
  `/root/bin/check-iptv-stream.sh` before restarting Threadfin/Jellyfin.
- **Never commit secrets.** Provider credentials live only in
  `/srv/media-core/.env` (600). Router snapshots in
  `/root/router-backups/` contain WireGuard private keys — 600, never
  committed.
- **Pinned image tags only**, never `:latest`.
- **`AGENTS.md` is a symlink to `CLAUDE.md`** — edit `CLAUDE.md` only.
- `origin` in `/root/pve-01-docs` pushes to **two** GitHub repos; after
  merging a PR run `git checkout main && git pull && git push origin main`
  to propagate to the mirror.
- **On the router**, `route_policy` `from_mac` must be a UCI **`list`**
  (`uci add_list`), never a scalar `option` — a scalar both crashes the
  Lua backend and leaves the firewall ipset empty, which **fails open**
  and silently leaks traffic outside the VPN. Apply route_policy changes
  with `/usr/bin/rtp2.sh apply`; `network reload` does not repopulate
  ipsets.

## Deferred — do not lose track of these

The owner wants these done later, not forgotten:

1. **Prune stale Tailscale nodes and add tailnet ACLs** (MEDIUM-HIGH).
   The tailnet is a fully trusted zone — any peer reaches the router
   admin UI, SSH, and the whole LAN — while **16 of 21 peers are
   offline**, several 500–972 days, three still advertising as exit
   nodes. Each retains valid credentials. Owner action in the Tailscale
   console.
2. **WireGuard keys in Loki history** (MEDIUM). Present from before the
   2026-07-25 redaction fix. LAN-only, ~30-day retention. Options:
   accept retention / purge via Loki delete API / rotate keys.
3. **Active WAN is the WiFi repeater** (`apclix0` = logical `wwan`, SSID
   `GIOT`) at metric 1, with ethernet at metric 2 — confirm that is
   intended. It also means the ~104 Mbps "direct WAN" baseline was
   measured through the repeater.

## When your tokens run low

Push all documentation and status to the server and both GitHub remotes,
then produce a handoff prompt for Claude in the same shape as this one:
what you did, what you verified vs assumed, exact stopping point, next
steps, and anything deferred.
