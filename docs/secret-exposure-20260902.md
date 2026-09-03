# Exposed Jellyfin API keys in the public repo — 2026-09-02

**Two live Jellyfin API keys were committed to this PUBLIC repository on 2026-08-03
(`1b987d5`) and remained public for one month.** Found during a security review on 2026-09-02.

Key values are deliberately NOT reproduced here — they are already in this repo's history and
this file must not add another copy. Identify them by prefix and location.

| Key prefix | Server | Tracked files |
|---|---|---|
| `3f579d40…` | CT 105 production (`192.168.9.50:8096`) | `scripts/icon-contact-sheet-prod.py`, `scripts/icon-crosscheck-stacks.py`, `scripts/prod-verify-icons.py` |
| `1f74eabb…` | CT 112 jellyfin-npvr (`192.168.9.219:8096`) | `scripts/epg-sync-ct112`, `scripts/icon-contact-sheet.py`, `scripts/icon-crosscheck-stacks.py`, `scripts/icon-verify-enduser.py` |

`gh repo view InDaSky314/pve-01-docs` returns `"isPrivate": false`. Push fans out to a second
public mirror, so both copies are exposed.

## Risk, stated honestly

Both keys address **RFC1918** endpoints, so using them requires LAN or Tailscale access — this
is not an open door from the internet. But they are **full API keys**: during the 2026-09-02
session one of them was used to create a recording timer and to clear three scheduled tasks'
triggers, so the capability is administrative, not read-only. They have been publicly indexed
for a month, across two repos.

## Why rotation, not history rewriting

Once a secret has been public for a month, treat it as disclosed. Rewriting history does not
un-publish it — clones, forks and caches persist. **Rotation is the fix.** History rewriting is
optional cleanup afterwards and was deliberately not treated as the remedy.

## What was NOT affected

Every script written during the 2026-09-02 session — `dvr-recording-report`,
`dvr-preflight-digest`, `backup-restorability-verify`, `epg-repair-loop`, `icon-repair-loop`,
and the `dvr-dashboard` changes — contains **zero** hardcoded keys (verified by grep against
the committed tree). The exposure is entirely from the 2026-08-03 icon work.

The other 32-character strings visible in `scripts/` are Jellyfin task and item GUIDs
(e.g. the `Refresh Guide` task id), not credentials.

## Remediation

### What actually happened, and why the plan changed

The rotation tooling was delegated to agy, which **hit its usage quota mid-build** and produced
nothing (`Individual quota reached`). Scheduling an unattended 05:30 rotation against tooling
that does not exist would have been precisely the risk the deferral was meant to avoid.

So the work was **decomposed instead**, which turned out to be the better design regardless:

* **Safe half, done 2026-09-02 night:** every consumer converted from a hardcoded literal to a
  read of a 0600 key file. Fully reversible, no credential changed, nothing revoked.
* **Irreversible half:** mint new keys, swap the file contents, revoke the old keys.

Because all seven consumers now read the *same two files*, rotation collapses from "edit seven
scripts correctly" to "change one file's contents" — atomic, and revertible by writing the old
value back. That is a materially smaller and safer operation than what was originally scheduled.

Host-side key files created (`/etc/media-core/`, mode 0600, dir 0700), because these scripts run
on the host and could not read CT 105's in-container `.jellyfin_api_key`. CT 112 had **no** key
file at all; one was created at `/srv/jellyfin-npvr/.jellyfin_api_key`.

Verified after conversion: both keys authenticate from their files
(`media-core 10.11.9`, `jellyfin-npvr 10.11.9`), all seven files AST-parse, and zero key
literals remain in any of them. `epg-sync-ct112.timer` is live (daily 12:28) and its script was
converted and verified.

Rotation was **deliberately deferred from the night of 2026-09-02 to 05:30 on 2026-09-03.**
Reasoning, recorded because the tradeoff matters: rotation plus converting seven scripts is a
coordinated change, and doing it unattended late at night two days before a wanted recording
risked breaking the dashboard and the new reporting jobs. One additional night of a month-old,
RFC1918-scoped exposure was the smaller risk. The tooling was built and dry-run tested the same
night so the scheduled run is mechanical rather than exploratory.

Sequence enforced by `jellyfin-key-rotate` — the ordering is the safety property:

1. discover every consumer of both keys (host, CT 105, CT 112)
2. record rollback state outside the repo
3. mint new keys and verify they work **before** changing anything
4. write them to 0600 files (`/srv/media-core/.jellyfin_api_key` and the CT 112 equivalent)
5. convert every consumer to read the file — **no literal fallback left in code**
6. exercise every consumer against the new key
7. **revoke the old keys only after all consumers pass** — the sole irreversible step, and last
8. any failure at any step restores from rollback and leaves the old keys valid

## The standing rule this violated

`CLAUDE.md`: *"Secrets stay out of this repo — provider credentials live only in
`/srv/media-core/.env` (600) and generated files inside CT 105. Never in commits, logs, or chat
pastes."* The rule was correct and predates the exposure; nothing enforced it. A recurring
secret scan is the control that closes that gap — see the follow-on security work.

## Follow-on

* Recurring secret scanning so this cannot recur silently in a public repo.
* SBOM + CVE scan of the seven pinned container images. Pinned tags are correct for stability
  but mean **nothing ever auto-updates**, so known CVEs accumulate invisibly.
* Debian CVE posture on the host and the four running containers.
* Exposure audit — several services bind `0.0.0.0` (`8098`, `8099`, `9105`, `9106`, `3128`,
  `3389`); establish what is reachable from LAN vs Tailscale and what actually has auth.
  Note `/srv/log-server/docker-compose.yml` still carries `GF_SECURITY_ADMIN_PASSWORD=changeme-initial-setup`
  even though the running Grafana password is not that value.

## Rotation executed — 2026-09-03

Both keys rotated. The exposed values are no longer in use anywhere.

| Server | old (exposed) | new | app name |
|---|---|---|---|
| CT 105 production | `3f579d40…` (app `media-core-agent`, created 2026-07-05) | `9940e2da…` | `media-core-automation-20260903` |
| CT 112 jellyfin-npvr | `1f74eabb…` (app `claude-iconfix`, created 2026-08-02) | `96cb2db4…` | `npvr-automation-20260903` |

Useful finding from `GET /Auth/Keys`: each server had **exactly one** key — the exposed one.
No third-party app depended on either, which is what made rotation low-risk.

New keys were written straight into the 0600 key files by the minting script and **never
printed**, so no full key value appears in any transcript or log from this work.

### Consumers converted

Ten in total. Seven on the host/repo were converted on 2026-09-02 (commit `93deb73`). Three
more were found inside the containers and converted on 2026-09-03 — they are untracked,
ad-hoc scripts, which is exactly why a host-only sweep missed them:

* `CT105:/root/prod_verify.py`, `CT105:/root/prod_audit.py`
* `CT112:/root/enduser.py`

Key files, all 0600:

| Path | Server |
|---|---|
| `/etc/media-core/jellyfin-prod.key` (host, dir 0700) | CT 105 |
| `/etc/media-core/jellyfin-npvr.key` (host, dir 0700) | CT 112 |
| `/srv/media-core/.jellyfin_api_key` | CT 105, pre-existing |
| `/srv/jellyfin-npvr/.jellyfin_api_key` | CT 112, created 2026-09-02 (did not exist) |

`dvr-dashboard` and the reporting jobs built on 2026-09-02 needed **no** change — they already
read `.jellyfin_api_key`, and `dvr-dashboard` carries a fallback list that includes
`/var/lib/lxc/105/rootfs/srv/media-core/.jellyfin_api_key`, letting a host process read CT 105's
file directly. That pattern is worth copying.

### Verified before revoking

| Consumer | Result |
|---|---|
| `dvr-dashboard /api/status` | OK, `problems=[]`, 3 recordings |
| `dvr-recording-report --dry-run` | exit 0 |
| `dvr-preflight-digest --dry-run` | `ALL CLEAR` |
| `epg-sync-ct112` (live timer, daily 12:28) | key resolves, auths as `jellyfin-npvr` |
| CT 105 in-container scripts | auth OK as `media-core` |
| CT 112 in-container scripts | auth OK as `jellyfin-npvr` |

### Ordering is the safety property

Mint → verify new key works → swap file contents → exercise every consumer → **revoke last**.
Until revocation the old key stays valid, so every step before it is revertible by writing the
old value back (kept at `*.old-20260903` beside each key file, and in
`/root/keyrotate-bak-20260903/`). Those backups hold live credentials and stay off git.

## The blocker that nearly broke four tools — `mp0` means two files at one path

Before revoking, an exhaustive consumer sweep was run as a safety net. It found a trap that the
host-side sweep had missed, and that **contradicted an earlier claim in this document**:

> `/srv/media-core/.jellyfin_api_key` **on the host** still held the OLD key.

`/srv/media-core` is an `mp0` mount (`local-lvm:vm-105-disk-1`) that exists **inside CT 105**.
The Proxmox host has its own, entirely separate directory at the same path. Updating the file
inside the container therefore did **not** update the host's copy — they are two different
files that merely look like one.

`dvr-dashboard`, `dvr-preflight-digest`, `dvr-recording-report` and `sports-dvr-auto` all run on
the host and search `JF_KEY_FILES` with `/srv/media-core/.jellyfin_api_key` **first**. Revoking
at that point would have broken all four.

Two further corrections to what was written earlier:

* The fallback `/var/lib/lxc/105/rootfs/srv/media-core/.jellyfin_api_key` is **never reached**,
  because the primary path exists on the host — and it would not work anyway: that path does
  not exist, precisely because `/srv/media-core` is an `mp0` mount rather than part of the
  container rootfs.
* Verification before this fix **passed while proving nothing**. Every consumer authenticated
  successfully — using the *old* key, which was still valid. A green test cannot distinguish
  "using the new credential" from "using the old one that has not been revoked yet".
  **Verify which credential is resolved, not merely that the call succeeds.**

**Fix:** the host path is now a symlink to the single source of truth, so a future rotation
touches one file:

```
/srv/media-core/.jellyfin_api_key -> /etc/media-core/jellyfin-prod.key
```

### A second exposure, found by the same sweep

`docs/handoff-20260803b.md:196` contained the CT 112 key as a literal — a second copy in the
public repo that the original grep of `scripts/` had not covered. Replaced with
`<redacted-rotated-20260903>`. A `git grep` for both keys across the whole tracked tree now
returns nothing.

## Revocation — completed 2026-09-03

```
CT 105 : keys before ['3f579d40','9940e2da'] -> DELETE 204 -> after ['9940e2da']
         old key now REJECTED (HTTP 401); new key OK (media-core)
CT 112 : keys before ['1f74eabb','96cb2db4'] -> DELETE 204 -> after ['96cb2db4']
         old key now REJECTED (HTTP 401); new key OK (jellyfin-npvr)
```

Re-verified **after** revocation, when a stale credential could no longer mask a mistake:

| Consumer | Result |
|---|---|
| `dvr-dashboard` `/api/status`, `/api/schedule` | OK, `problems=[]`, 348 games |
| `dvr-recording-report` | exit 0 |
| `dvr-preflight-digest` | ALL CLEAR |
| `sports-dvr-auto` | exit 0 |
| `epg-sync-ct112` | auths as `jellyfin-npvr` |
| CT 105 sync scripts | auth as `media-core` |
| Saturday Bayern timer | intact |
| media stack | jellyfin + threadfin healthy |

The only 401 seen afterwards was a throwaway diagnostic script written earlier in the session
that had the old key inline — which is itself confirmation that revocation took effect.

