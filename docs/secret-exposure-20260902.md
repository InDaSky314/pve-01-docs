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
