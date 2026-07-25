# Wholphin custom fork — status & handoff (2026-07-25)

Client-side attempt at the Live TV **no-audio** bug, after server-side
routes were exhausted. See §10 history for the full chain; the short
version is that the audio problem is *not* server-side:

- The DVB-subtitle-track theory was disproven (ffprobe shows no subtitle
  stream on affected channels).
- Swapping channels 100/102 to "clean" provider streams did not fix it.
- **NextPVR was deployed and tested** — channels 100 and 102 were pushed
  to Jellyfin via NextPVR instead of Threadfin, and **the audio problem
  persisted**. That result is what moved effort to the client.

NextPVR is therefore **parked, not removed** — left deployed and working
so it can be revisited after the client work lands. Do not spend more
time on it for now. (Original evaluation stands: adopting it wholesale
would still hit the single-connection tuner limit, and its original
justification was a root cause that has since been disproven.)

## The fork

| | |
|---|---|
| Working repo | `/srv/media-core/wholphin` on **pve-01** (the host, not CT 105) |
| Intended GitHub remote | `git@github.com:nk-sys-ops/wholphin.git` (private) |
| Upstream | `https://github.com/damontecres/wholphin.git` |
| Branch pushed locally | `iptv-audio-fix` in `/root/wholphin` |

### Custom commits (all on top of upstream `cf3c00a1`)

| Commit | Change |
|---|---|
| `a5e605fe` | TiviMate-grade `TsExtractor` flags (`FLAG_ALLOW_NON_IDR_KEYFRAMES` + `FLAG_DETECT_ACCESS_UNITS`) and an ExoPlayer `onTracksChanged` audio-fallback listener that forces audio-track binding when `AudioTrack` defaults to `NONE` — in `PlayerFactory.kt` |
| `96b2f41b` | Native **IPTV Audio Track Recovery** toggle under *Settings → Experimental Settings* (`ExperimentalPreference.kt`, `WholphinDataStore.proto`, `strings.xml`) |
| `ec562b4e` | Default that toggle **ON** for maximum audio compatibility |

Total footprint is small (~57 insertions across 5 files) but it is the
actual candidate fix for the audio bug.

## ⚠️ The work is not on GitHub

**Corrects an earlier claim in `CLAUDE.md` that the private GitHub repo
was "fully up to date" — it is not.** Verified 2026-07-25:

- All three custom commits were **absent** from `/root/wholphin`, the
  only clone that carries a GitHub remote — so they were never pushed.
- `nk-sys-ops/wholphin` **does not resolve** with either credential
  available on this box: the `github-mirror` deploy key
  (`id_ed25519_pve01docs_mirror`) is scoped to `pve-01-docs` only, and
  the `gh` CLI is authenticated as `InDaSky314`, which cannot see it.
  GitHub returns "Repository not found", which for a private repo means
  either it doesn't exist yet or neither credential has access.

Mitigations applied in the meantime (both on pve-01):

1. Pushed to `/root/wholphin` as branch **`iptv-audio-fix`** — the
   commits now exist in two repos rather than one.
2. **`git bundle`** of `cf3c00a1..HEAD` in
   `/root/wholphin-backups/` (verified restorable; the base commit is
   public upstream, so the bundle is sufficient to reconstruct the work
   anywhere).

**Owner action needed to close this properly:** either create
`nk-sys-ops/wholphin` and add a deploy key with write access, or provide
a credential that can push there. Until then the fork lives only on
pve-01.

## Build status — stopped mid-build

Toolchain on pve-01 is ready: **Java 21**, Android SDK platform +
build-tools **36**, licences accepted. `./gradlew assembleDebug` was
running when the previous session ended; no APK had been produced.
Restarted 2026-07-25 and confirmed actively compiling (`kspAppstoreDebugKotlin`,
`kspDefaultDebugKotlin`, `kspFiretvDebugKotlin`).

**Correction to the documented APK path.** This project has build
**flavors** — `appstore`, `default`, `firetv` — so `assembleDebug`
produces several APKs and the previously documented
`app/build/outputs/apk/debug/app-debug.apk` **will not exist**. Expect:

```
app/build/outputs/apk/default/debug/app-default-debug.apk
app/build/outputs/apk/appstore/debug/app-appstore-debug.apk
app/build/outputs/apk/firetv/debug/app-firetv-debug.apk
```

For the **Chromecast with Google TV**, use the **`default`** flavor —
`firetv` targets Amazon devices and `appstore` is the Play-Store build.
Verify by listing the directory rather than assuming.

## Chromecast state

- Both the previous `wholphin` build and the official
  `org.jellyfin.androidtv` were uninstalled, so the device is clean for
  a fresh install.
- ADB over TCP at `192.168.9.203:5555`. **Wireless debugging does not
  survive a device reboot** — it must be re-enabled by hand in
  *Settings → System → Developer options*, and the connect port is not
  stable. Expect to re-pair.

## Next steps

1. Confirm the build produced APKs; identify the `default`-flavor path.
2. Install: `adb -s 192.168.9.203:5555 install -r <path-to-default-debug.apk>`
3. Sign in to Jellyfin, confirm *Settings → Experimental Settings →
   IPTV Audio Track Recovery* is present and **on** by default.
4. Test audio on the known-bad channels — **133, 134, 313** (and 100/102).
   See the affected-channel log in the history table.
5. If audio works, that confirms the client-side root cause; then decide
   whether to revisit NextPVR at all.

## Deferred, explicitly not forgotten

- **Prune stale Tailscale nodes + add ACLs** (MEDIUM-HIGH). 16 of 21
  peers offline, several 500–972 days, three still advertising as exit
  nodes; the tailnet is a fully trusted zone with full router admin and
  whole-LAN access. See `docs/router-security-audit-20260725.md`.
- **WireGuard keys in Loki history** (MEDIUM). Present from before the
  2026-07-25 redaction fix; LAN-only, ~30-day retention. Options:
  accept retention / purge via Loki delete API / rotate.
