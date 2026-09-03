# Security review and CVE remediation — 2026-09-03

Full estate review of pve-01 and the four running containers, following the API-key exposure
(`docs/secret-exposure-20260902.md`). agy ran the scans; Claude Code verified findings and
applied the urgent fixes. Scanner binaries (`syft`, `grype`, `trivy`, `gitleaks`) were installed
static into `/usr/local/bin` — no apt repos touched.

## Fixed immediately

**A live credential was world-readable.** `CT107:/srv/log-server/prometheus/prometheus.yml.pre-secrets-20260828-071452`
was mode `0644` and contained the plaintext `router-dashboard` Basic Auth password. It was
created on 2026-08-28 when credentials were isolated into `router_dashboard.pw` — the *backup*
kept the secret under the default umask. Now `0600`, along with
`prometheus.yml.pre-cutover-fix-20260828-070416`, which had the same inline form.

The remaining `prometheus.yml*` files use `password_file:` and are safe at `0644` — verified
individually rather than assumed from filename or size. `/root/agy-reports/` tightened to `0700`
with reports `0600`, since the reports quote live secrets.

**`alert-responder` was an unauthenticated remote-execution vector.** It binds `0.0.0.0:9106`
and a bare POST dispatched an agy job and sent outbound mail — reachable by anything on the LAN
or the tailnet. Now source-allowlisted to Grafana's host and localhost. Source-IP allowlisting
rather than a shared secret because it needs no change to Grafana's contact point (whose admin
password we do not hold). Verified: localhost `200`, CT 107 `200`, CT 105 `403` and logged.

## CVE posture

Pinned tags are correct for stability but mean nothing auto-updates, so CVEs accumulate
silently. That is the gap this review closed.

**Applied** (none on the recording path; CT 105 deliberately untouched before the Saturday
2026-09-05 recording):

| Component | From | To | Verified by |
|---|---|---|---|
| Loki (CT 107) | `3.7.3` | `3.7.7` | pushed a line and queried it back |
| Alloy (CT 107, CT 112) | `v1.17.1` | `v1.19.2` | live log streams still arriving in Loki from both |
| Prometheus (CT 107) | `v3.2.1` | `v3.14.0` | 8/8 scrape targets `up`; all 11 Grafana alert rules intact |

CT 105's `jellyfin` and `threadfin` confirmed at unchanged uptime afterwards.

**Prepared, not applied** — Jellyfin `10.11.9` -> `10.11.11` (must not silently re-enable the
three deliberately-disabled tasks: Media Segment Scan, Generate Trickplay Images, Extract
Chapter Images) and Grafana `11.6.16` -> 12.x LTS (two majors; Infinity datasource and alert
migration need checking).

**Accepted with reason:** NextPVR shows 657 Critical/High, all from an unpatched Debian 12
base. The pinned tag is already current upstream, so there is no bump to take. It parses only
provider IPTV streams on the LAN. A custom rebuild would be fragile to maintain for a container
that is not internet-facing.

## Exposure

No inbound port forwards exist on the edge router; WAN is default-DROP. Everything below is
LAN- or tailnet-reachable only.

Unauthenticated listeners worth knowing about: Threadfin `34400` (full tuner/playlist UI),
Prometheus `9090` and Loki `3100` (query *and* log injection), the node exporters `9100`,
`pve_exporter` `9221`, `stack-monitor` `9105`, `icon-host` `8100`, and `rpcbind` `111`.
`8006` (Proxmox) and `3389` (xrdp) are reachable over Tailscale but both require auth.

The stale `GF_SECURITY_ADMIN_PASSWORD=changeme-initial-setup` in the compose file was tested
live and **does not work** (`HTTP 401`) — Grafana only reads it when creating a new database.
Cosmetic, but worth removing so it stops looking like a live credential.

## Secrets

`gitleaks` over all 252 commits found only the two Jellyfin keys already rotated and revoked on
2026-09-03. No other credential has ever been committed. A second copy in
`docs/handoff-20260803b.md` was found and redacted during that work.

**Still open:** the `router-dashboard` password was world-readable for six days (in-container
only, not internet-facing) and should be rotated. It is a two-sided credential — Prometheus
presents it, `router-dashboard` validates it — so both ends must change together, and the proof
of success is the `router_dashboard` scrape target returning to `up`.

Also open: a pre-commit `gitleaks` hook plus a scheduled scan, so a public repo cannot silently
accumulate credentials again. The control must fail loudly; a scanner that exits 0 on error is
worthless.

## Upgrades applied — 2026-09-03 maintenance window

Owner opened a window to 17:00 with nothing watching. Executed least-valuable-first so a
failure would be cheap: CT 112 (staging) -> CT 105 (production) -> CT 107.

| Component | From | To | Verified |
|---|---|---|---|
| Jellyfin CT 112 | `10.11.9` | `10.11.11` | version, timers, item counts, 957 channels, 25,128 programmes |
| Jellyfin CT 105 | `10.11.9` | `10.11.11` | version, **Saturday Bayern timer identical**, 25,731/7,691/216,903 items, 1,225 channels |
| Grafana CT 107 | `11.6.16` | `12.4.10` | 11 alert rules intact and all in `Normal`; Loki, Prometheus and Infinity datasources all present |

No rollbacks were needed. A fresh config backup (718 MB) was taken first and confirmed written
before anything was touched.

**The three deliberately-disabled tasks on CT 105 survived the Jellyfin upgrade with empty
triggers** — Media Segment Scan, Generate Trickplay Images, Extract Chapter Images. That was
the specific risk of this bump and it was checked explicitly rather than assumed.

### Post-upgrade function test — all green

Containers on CT 105/107/108/112 healthy; 11 host timers and 6 CT 105 timers active; 8/8
Prometheus targets up; Loki ingest OK; `dvr-dashboard` `problems=[]` with 348 games;
`router-dashboard` 200 on the rotated credential; `dvr-recording-report`, `dvr-preflight-digest`
(ALL CLEAR) and `sports-dvr-auto` all exit 0; Threadfin serving 1,225 channels; `epg.xml` fresh;
and the Saturday Bayern fixture still resolvable in Jellyfin's guide after the upgrade.

### Credential rotation completed

`router-dashboard` and `dvr-dashboard` turned out to **share one credential** — both read
`/etc/dvr-dashboard.auth`, and Prometheus presents the same password from
`CT107:/srv/log-server/prometheus/router_dashboard.pw`. Rotated across all four touchpoints:
new secret in both files, both services restarted, Prometheus restarted. Verified new credential
`200` on both dashboards, **old credential `401`**, and the `router_dashboard` scrape target back
to `up` (8/8). Backups at `/root/dvr-dashboard.auth.pre-rotate-20260903` and the matching
`.pre-rotate-20260903` on CT 107.

