# Monitoring & Alerting Estate Audit Report (Post-Cutover)
**Date:** 2026-08-28 · **Host:** `pve-01` (192.168.9.11) · **Status:** Complete Read-Only Diagnostic Audit

---

## Executive Summary

Following the 2026-08-27 network cutover, the physical topology and IP routing of the homelab changed fundamentally:
- **GL-BE9300** (`192.168.9.1`) became the primary PPPoE edge router (terminating Deutsche Telekom fiber) and the primary LAN gateway (`192.168.9.0/24`).
- **GL-MT2500** (`192.168.2.1`), formerly the PPPoE edge, was retired, unplugged, and de-racked.
- **GL-MT6000** (Flint 2), formerly at `192.168.9.1`, moved to `192.168.5.1` to serve the TV corner (Chromecast, FireStick, LG webOS TV).
- The **`192.168.3.0/24`** subnet is completely gone.
- The **`192.168.2.0/24`** subnet is completely gone.
- Proxmox host `pve-01` (`192.168.9.11`) physically moved to the BE9300 rack and egresses natively via Telekom (no tunnel). Its public IP rotates nightly (~01:30 CEST).
- Container egress policy: Media stack (CT 105, 111, 112) -> Swiss WireGuard (Zürich); Scraper/Logs (CT 107, 108) -> US WireGuard (Ashburn).

A comprehensive, non-destructive audit of all Prometheus scrape targets, Grafana dashboards, Alertmanager configurations, Loki log shippers, cron/systemd timers, host-level helper scripts, and network watchdogs was conducted.

### Key Finding:
**13 distinct failures, stale assumptions, and coverage gaps** were identified. Most dangerously, **silence is masquerading as health**:
1. WireGuard tunnel snapshotting (`wg-snapshot.sh`) is failing SSH authentication every 60 seconds and generating false alerts in Loki while failing to monitor the four actual WireGuard tunnels on the BE9300.
2. Prometheus scrape target `job="router"` (`192.168.9.1:9100`) has been in state `DOWN` (Connection Refused) since the cutover, with **zero alert rules** in place to notify the owner.
3. The router syslog pipeline is completely dead: `router-log-receiver.py` discards all logs from the MT6000 (`192.168.5.1`), the BE9300 is not configured to ship syslog, and zero router logs have reached Loki since 20:26 CEST yesterday.
4. `router-dashboard` (:8098) is actively emitting corrupt and lying metrics (`gateway_2_1_wan_up 1`, `router_subnet_conflict_detected 1`, `router_network_egress_reachable{network="main"} 0`).
5. Critical infrastructure has **zero automated surveillance**: the new PPPoE edge (`192.168.9.1`), nightly public IP rotation, container VPN egress policy correctness, and the router's self-healing boot hook (`/etc/hotplug.d/iface/99-network-buildout-persist`).

Mail delivery via Postfix and Gmail SMTP relay remains **fully operational** and resilient across dynamic IP rotation.

---

## Severity-Ranked Table of Broken / Lying / Gap Items

| Severity | Class | Component / File | What It Does Now (Observed Behavior) | Impact | Specific Fix |
|---|---|---|---|---|---|
| **CRITICAL** | **BROKEN / GAP** | `/root/bin/wg-snapshot.sh`<br>`wg-snapshot.timer` | Attempts `ssh root@192.168.9.1 "wg show all dump"` without router SSH key identity. Fails every 60s with `Permission denied (publickey,password)` and pushes `SSH_OR_WG_FAILED` alert to Loki. | Grafana WireGuard health panels show alert state; all 4 WireGuard tunnels on BE9300 are completely unmonitored. | Update SSH invocation to use `-F /root/.ssh/config glinet-9.1` (or `-i /root/.ssh/id_ed25519_routers`). Update Loki host label from `flint2` to `be9300`. |
| **CRITICAL** | **GAP** | Edge Watchdog<br>`gateway-2-1-monitor.service` | `gateway-2-1-monitor.service` is disabled/stopped. Its role (monitoring PPPoE session drops & WAN flaps) is completely uncovered for `192.168.9.1`. | No visibility into Telekom PPPoE drops, nightly re-auth disconnect times, or WAN link flapping on the primary edge. | Repoint script to `192.168.9.1` (`gateway-9-1-monitor`), query `pppoe-wan` status via SSH `glinet-9.1`, persist to `/var/lib/router-dashboard/gateway-9-1-current.json`, re-enable unit. |
| **CRITICAL** | **GAP** | Container VPN Egress Enforcement | No automated script or watchdog probes public IP / geolocation of containers (CT 105, 107, 108, 111, 112) or host. | If router policy routing drops or leaks to bare WAN, media containers could leak traffic to ISP without detection. | Add periodic egress check in `stack-monitor.py` querying IP echo inside CTs; export `container_egress_valid` metric and alert if invalid. |
| **HIGH** | **LYING / BROKEN** | `/usr/local/bin/router-dashboard`<br>`router-dashboard.service` | Tries SSH to `glinet-3.1` via `glinet-2.1` ProxyJump. Checks dead subnets (192.168.3.x, 11.x, 12.x). Flags `router_subnet_conflict_detected 1` because obsolete prio-5900 rule is gone. Emits frozen `gateway_2_1_wan_up 1`. | Emits corrupt, false metrics on port 8098 to Prometheus; corrupts Grafana network dashboard. | Re-architect `router-dashboard` for 2-router topology (BE9300 9.1 + MT6000 5.1). Remove 2.1/3.1/11.x/12.x code. Update subnet conflict detector. |
| **HIGH** | **BROKEN / LYING** | `/srv/log-server/prometheus/prometheus.yml`<br>`job_name: router` (CT 107) | Prometheus scrapes `192.168.9.1:9100` labeled as `GL-MT6000 (flint2)`. Scrape returns `connection refused` (`health: "down"`). | Permanently down. All 13 panels in Grafana `Network: Router & Tunnels` dashboard are blank. 0 alerts fire because no `up == 0` rule exists. | Either install `prometheus-node-exporter-ucode` on OpenWrt routers or remove scrape target and collect router metrics via Prometheus-compatible collector. |
| **HIGH** | **BROKEN / GAP** | `/root/router-log-receiver.py`<br>`config.alloy` (CT 107) | UDP 514 syslog receiver drops all packets where `addr[0] != "192.168.9.1"`. MT6000 moved to 5.1; BE9300 does not have `log_ip` configured. | `/root/network-logs/flint2-syslog.log` stopped receiving data at 20:26 yesterday. Zero router syslogs reaching Loki. | Update receiver to accept 192.168.9.1 and 192.168.5.1; configure `uci set system.@system[0].log_ip='192.168.9.164'` on both routers; update Alloy labels. |
| **HIGH** | **BROKEN** | `/root/bin/router-backup.sh`<br>`router-backup.timer` | Hardcodes `ROUTER="root@192.168.9.1"` with raw SSH (no `-i`). Fails with SSH permission denied on `ssh_r "true"`. Ignores MT6000. | Scheduled weekly backup on Sunday 09:13 will fail; neither router has automated off-device backups. | Update `router-backup.sh` to use `id_ed25519_routers` key and back up both `glinet-9.1` and `glinet-5.1`. |
| **HIGH** | **GAP** | Boot Hook Health Surveillance | `/etc/hotplug.d/iface/99-network-buildout-persist` on BE9300 runs silently. If it fails, VPN forward/NAT/DNS rules break silently. | Single point of failure for container internet/DNS has zero execution or state validation. | Add a health probe checking for `jump srcnat_wgclient*` jumps, `/tmp/resolv.conf.d/resolv.conf.wgclient*` size > 0, and log to Loki/Prometheus. |
| **MEDIUM** | **BROKEN** | `/etc/systemd/system/chromecast-logcat.service`<br>`chromecast-adb-keepalive` | Service hardcodes `CHROMECAST_ADDR=192.168.9.203:5555`. Timer searches `192.168.9.203`. Chromecast moved to `192.168.5.x` on MT6000. | Service stuck in `- waiting for device -`. Zero Chromecast logcat logs reaching Loki. Keepalive fails. | Update DHCP reservation on MT6000 for Chromecast (e.g. `192.168.5.203`), update systemd unit with new IP, configure cross-subnet routing/mDNS. |
| **MEDIUM** | **STALE / BROKEN** | `/root/.ssh/config` (pve-01 host) | Contains stale `Host glinet-3.1` (192.168.3.1), `Host glinet-2.1` (192.168.2.1). Missing alias for `glinet-5.1` (192.168.5.1 / 100.82.52.36). | Bare `ssh root@192.168.9.1` and `ssh root@192.168.5.1` fail authentication without explicit `-i` or alias. | Clean up stale blocks; add `glinet-5.1`; add wildcard rule for router subnets mapping `id_ed25519_routers`. |
| **MEDIUM** | **GAP** | Missing Node Exporters on CT 111 & 112 | CT 111 (`jellyfin-vod`) and CT 112 (`jellyfin-npvr`) have `prometheus-node-exporter` inactive and are absent from `prometheus.yml`. | No OS-level CPU, RAM, disk, or network metrics for VOD and NextPVR containers. | Enable `prometheus-node-exporter` in CT 111 & 112; add targets `192.168.9.171:9100` and `192.168.9.219:9100` to `prometheus.yml`. |
| **MEDIUM** | **GAP / LYING** | Grafana & Prometheus Alert Rules | Prometheus has 0 alert rules. Grafana has 8 alert rules, but NONE for `up == 0` (target down), `wg_handshake_age_s > 180`, or `wan_up == 0`. | When scrape targets or tunnels go down, the monitoring system remains completely silent. | Add alert rules for Prometheus target down, WireGuard handshake staleness, and WAN interface down. |
| **LOW** | **BROKEN** | Grafana Dashboard `router-network-dashboard` | Dashboard contains 4 panels querying `gateway_2_1_*` metrics for the retired MT2500. | Panels display stale/frozen data or "No Data". | Update panels to query `gateway_9_1_*` metrics once `gateway-9-1-monitor` is deployed. |

---

## Detailed Audit Findings by Area

### 1. Prometheus Scrape Targets & Endpoints

Scrape configuration was audited directly via CT 107 `/srv/log-server/prometheus/prometheus.yml` and the live Prometheus API (`curl -s http://localhost:9090/api/v1/targets`):

```json
{"activeTargets":[
  {"scrapePool":"node","labels":{"alias":"pve-01 (host)","instance":"192.168.9.11:9100","job":"node"},"health":"up"},
  {"scrapePool":"node","labels":{"alias":"CT 105 media-core","instance":"192.168.9.50:9100","job":"node"},"health":"up"},
  {"scrapePool":"node","labels":{"alias":"CT 107 log-server","instance":"192.168.9.164:9100","job":"node"},"health":"up"},
  {"scrapePool":"node","labels":{"alias":"CT 108 scraper","instance":"192.168.9.115:9100","job":"node"},"health":"up"},
  {"scrapePool":"pve","labels":{"instance":"192.168.9.11","job":"pve"},"health":"up"},
  {"scrapePool":"router","labels":{"alias":"GL-MT6000 (flint2)","instance":"192.168.9.1:9100","job":"router"},"health":"down",
   "lastError":"Get "http://192.168.9.1:9100/metrics": dial tcp 192.168.9.1:9100: connect: connection refused"},
  {"scrapePool":"router_dashboard","labels":{"alias":"pve-01 (router-dashboard)","instance":"192.168.9.11:8098","job":"router_dashboard"},"health":"up"},
  {"scrapePool":"stack_monitoring","labels":{"alias":"stack-monitor","instance":"192.168.9.11:9105","job":"stack_monitoring"},"health":"up"}
]}
```

#### Specific Findings:
- **`job="router"` is DOWN**: Points to `192.168.9.1:9100` with label `GL-MT6000 (flint2)`. `192.168.9.1` is now the BE9300, which does not have a node exporter running on port 9100. The MT6000 moved to `192.168.5.1` and also has no node exporter listening.
- **`job="router_dashboard"` is UP but LYING**: Emits data from `/usr/local/bin/router-dashboard` (port 8098), which returns stale gateway 2.1 data, failed mesh checks, and fake subnet conflicts.
- **Missing Container Targets**: CT 111 (`192.168.9.171`) and CT 112 (`192.168.9.219`) are active and hosting production services (Jellyfin VOD, Jellyfin NPVR, NextPVR), but are not scraped by Prometheus.

---

### 2. Grafana Dashboards & Datasources

All dashboards stored in `/srv/log-server/grafana-data/grafana.db` and provisioning directories were inspected:

| Dashboard UID | Dashboard Title | Datasources Used | Status / Defect |
|---|---|---|---|
| `media-core-epg-sync` | Media-Core: EPG Sync & Coverage | Loki | **HEALTHY**: Queries `{job="epg-sync"}` and Threadfin container logs. |
| `media-core-reliability` | Media-Core: Streaming & Reliability | Loki | **BROKEN PANELS**: "WireGuard Tunnel Health" and "Handshake Age Trend" query `{job="wg-snapshot"}`, which receives only SSH error logs. |
| `media-core-host-resources`| Media-Core: Host & Proxmox Resources| Prometheus | **PARTIAL GAP**: Proxmox cluster and scraped hosts are healthy; CT 111 and 112 are absent. |
| `media-core-router-network`| Network: Router & Tunnels | Prometheus | **COMPLETELY DEAD**: 13 panels query `node_*` with `job="router"`. Displays 100% "No Data". |
| `brewers-schedule` | Milwaukee Brewers — Schedule | Infinity (MLB API) | **HEALTHY**: Direct queries to `statsapi.mlb.com`. |
| `router-network-dashboard` | Router & Network Infrastructure | Prometheus | **STRUCTURALLY CORRUPT & LYING**: 4 panels query `gateway_2_1_*` (retired box); mesh backhaul panel queries dead topology; subnet egress panel queries dead subnets. |

---

### 3. Alert Rules & Silence-As-Health Failure Modes

Grafana SQLite database table `alert_rule` contains 8 active rules. Prometheus has 0 alert rules.

```
[ID=1 UID=ffsskzdcwj4zkf] Media-Core: alert logged (Loki: {job="media-core-alerts"}) -> OK (NoData)
[ID=2 UID=cfssl019qjev4e] Media-Core: daily EPG sync did not complete (Loki: {job="epg-sync"} [25h]) -> Normal
[ID=3 UID=ffssl0whvucxsc] Media-Core: EPG real-channel coverage regressed -> Normal
[ID=4 UID=network-carrier-flap] NIC link flap on enp2s0 (bridge port) -> Normal
[ID=5 UID=network-carrier-down] NIC enp2s0 link currently down -> Normal
[ID=6 UID=rec-sanity-alert] Media-Core: recording fragmented or undersized -> Normal
[ID=7 UID=stack-health-down] Media-Core: stack service down -> Normal
[ID=8 UID=epg-freshness-stale] Media-Core: EPG guide data stale per stack -> Normal
```

#### Silence-As-Health Vulnerabilities:
1. **Prometheus Scrape Target Down (`up == 0`)**: No rule exists. When `job="router"` failed at the cutover, no alert fired.
2. **WireGuard Tunnel Stall**: No rule exists for handshake staleness (`handshake_age_s > 180`). Even when `wg-snapshot.sh` failed entirely, no alert was raised.
3. **PPPoE WAN Failure**: No alert exists for edge gateway disconnects.
4. **Container VPN Egress Leakage**: No alert exists if CT 105 or CT 112 egresses via bare Telekom WAN.

---

### 4. Email & Scheduled Reports Delivery

Scripts and services sending email were swept across the codebase:
- `/root/bin/alert-responder.py` (`alert-responder.service`, port 9106)
- `/usr/local/bin/lineup-watch-report` / `/root/bin/lineup-watch-email.py` (`lineup-watch.timer`)
- `/usr/local/bin/sports-dvr-auto` (`sports-dvr-auto.timer`)
- `/usr/local/bin/dvr-power-reminder` (`dvr-power-reminder.timer`)
- `/usr/local/bin/dvr-clean-shutdown` (`dvr-clean-shutdown.timer`)
- `/usr/local/bin/router-dashboard` (`router-dashboard.service`)

#### Postfix & Dynamic IP / SPF Verification:
Host `/etc/postfix/main.cf` relays all outbound mail through `[smtp.gmail.com]:465` with SASL authentication (`kopr.notify@gmail.com`).
Journal inspection (`journalctl -u postfix -n 50`) confirmed successful deliveries during and after the nightly Telekom IP change:
```
62B83500297: to=<nathan.karras@gmail.com>, relay=smtp.gmail.com[142.251.127.109]:465, dsn=2.0.0, status=sent (250 2.0.0 OK)
100C25002C7: to=<nathan.karras@gmail.com>, relay=smtp.gmail.com[142.251.127.109]:465, dsn=2.0.0, status=sent (250 2.0.0 OK) [01:08 CEST]
D835D5002C7: to=<nathan.karras@gmail.com>, relay=smtp.gmail.com[142.251.127.109]:465, dsn=2.0.0, status=sent (250 2.0.0 OK) [02:34 CEST]
E28A85002C7: to=<nathan.karras@gmail.com>, relay=smtp.gmail.com[142.251.127.108]:465, dsn=2.0.0, status=sent (250 2.0.0 OK) [04:08 CEST]
```
**Conclusion:** Email delivery is completely unaffected by the nightly rotating dynamic IP because SMTP authentication over TLS (port 465) bypasses residential port 25 filtering and SPF/DKIM restrictions.

---

### 5. Loki & Log Shipping Estate

Loki streams were queried via `/loki/api/v1/query_range`:

| Stream / Shipper | Originating Unit | Status | Findings |
|---|---|---|---|
| `{job="epg-sync"}` | CT 105 `media-core-sync` | **ACTIVE** | Healthy stream. |
| `{compose_service=~"jellyfin\|threadfin"}` | CT 105 Docker | **ACTIVE** | Ingesting application logs normally. |
| `{job="recording-sanity"}` | Host `stack-monitor.py` | **ACTIVE** | Emits recording anomaly events when detected. |
| `{job="router-syslog"}` | CT 107 `router-log-receiver` | **DEAD** | Log file `/root/network-logs/flint2-syslog.log` stalled at `2026-08-27T20:26 CEST`. Zero ingestion. |
| `{job="wg-snapshot"}` | Host `wg-snapshot.sh` | **CORRUPTED** | Emits `level=alert event=wg_health handshake_age_s=-1 msg="SSH_OR_WG_FAILED: Permission denied"` every 60s. |
| `{job="chromecast-logcat"}` | Host `chromecast-logcat.sh` | **FROZEN** | Process stuck on `failed to connect to 192.168.9.203:5555 - waiting for device -`. Zero logs. |

---

### 6. Host Units Swept for Legacy Topology References

A search across `/etc/systemd/system/`, `/usr/local/bin/`, `/root/bin/`, and `/root/.ssh/config` identified all references to retired addresses (`192.168.2.x`, `192.168.3.x`, `glinet-2.1`, `glinet-3.1`, `192.168.2.241`, `192.168.9.203`):

```
/etc/systemd/system/gateway-2-1-monitor.service -> References 192.168.2.1 (Disabled)
/usr/local/bin/gateway-2-1-monitor              -> References 192.168.2.1 (Obsolete)
/usr/local/bin/router-dashboard                 -> References glinet-3.1, 192.168.3.1, 192.168.2.241, ProxyJump glinet-2.1, VLAN 11/12 (Lying)
/root/bin/wg-snapshot.sh                        -> References root@192.168.9.1 without key, host="flint2" (Broken)
/root/bin/router-backup.sh                      -> References root@192.168.9.1 without key (Broken)
/root/router-log-receiver.py (CT 107)           -> References ROUTER_IP="192.168.9.1" only (Broken)
/srv/log-server/prometheus/prometheus.yml       -> References 192.168.9.1:9100 as GL-MT6000 (Broken)
/etc/systemd/system/chromecast-logcat.service   -> References 192.168.9.203:5555 (Broken)
/usr/local/bin/chromecast-adb-keepalive         -> References 192.168.9.203 (Broken)
/root/.ssh/config                               -> Contains glinet-2.1 (2.1), glinet-3.1 (3.1), glinet-3.1-ts (Tailscale) (Stale)
```

---

### 7. Coverage Gaps in Critical Infrastructure

#### Gap A: The PPPoE Edge & WAN Flap Monitoring
- When `192.168.2.1` was retired, `gateway-2-1-monitor` was disabled.
- The new PPPoE edge (`192.168.9.1`) currently has no service tracking `pppoe-wan` interface uptime, no disconnect detection, no flap duration logging, and no alert rule.

#### Gap B: Nightly Telekom PPPoE Rotation Unmonitored
- Telekom terminates PPPoE daily ~01:30 CEST.
- Last night, IP changed from `93.209.195.65` to `84.149.191.129`.
- The only mechanism recording this was `/usr/local/bin/nightwatch.sh`, which is an unmanaged, temporary script launched by hand in a terminal session. If PPPoE fails to re-authenticate or takes >10 minutes, nothing alerts.

#### Gap C: All Four WireGuard Tunnels on BE9300 Unmonitored
- `wgclient1` (Zürich), `wgclient2` (Frankfurt), `wgclient3` (Ashburn), and `ovpnclient1`/`wgclient4` (New York) have no handshake or throughput monitoring because `wg-snapshot.sh` is failing authentication.

#### Gap D: Per-Container Egress Verification
- No automated probe checks `wget -qO- https://api.ipify.org` inside CT 105/111/112 (must be Swiss) and CT 107/108 (must be US).
- If firewall rules drop or routing rules revert, media traffic could egress natively on Telekom with zero detection.

#### Gap E: Self-Healing Boot Hook (`/etc/hotplug.d/iface/99-network-buildout-persist`)
- The boot hook on BE9300 fixes MLD bridging, deletes stale rules, binds firewall zones to `wgclientN` devices, and populates `/tmp/resolv.conf.d/resolv.conf.wgclientN`.
- This hook runs silently without health validation. If an edit or firmware update breaks it, client traffic drops without alerting.

---

## Prioritised Repair Plan

### Phase 1: Immediate Triage & Noise Elimination (High Priority)
1. **Fix `wg-snapshot.sh`**:
   - Change `ROUTER="glinet-9.1"` in `/root/bin/wg-snapshot.sh` (or pass `-i /root/.ssh/id_ed25519_routers`).
   - Change Loki stream host label from `flint2` to `be9300`.
   - Verify immediate ingestion into Loki: `systemctl restart wg-snapshot.service`.
2. **Update Host SSH Configuration (`/root/.ssh/config`)**:
   - Remove obsolete `glinet-2.1` and `glinet-3.1` entries.
   - Add `glinet-5.1` (`HostName 192.168.5.1`, fallback `100.82.52.36`).
   - Add wildcard match for router IPs pointing to `IdentityFile /root/.ssh/id_ed25519_routers`.
3. **Fix `router-backup.sh`**:
   - Update script to use `glinet-9.1` and `glinet-5.1` aliases so both routers receive automated Sunday backups.

### Phase 2: Core Telemetry & Syslog Restoration (High Priority)
1. **Restore Router Syslog Pipeline**:
   - Update `/root/router-log-receiver.py` on CT 107 to accept UDP syslog from both `192.168.9.1` and `192.168.5.1`, tagging each line with host `be9300` or `mt6000`.
   - On BE9300: execute `uci set system.@system[0].log_ip='192.168.9.164'`, `uci set system.@system[0].log_port='514'`, `uci commit system`, `/etc/init.d/log restart`.
   - On MT6000: execute `uci set system.@system[0].log_ip='192.168.9.164'`, `uci set system.@system[0].log_port='514'`, `uci commit system`, `/etc/init.d/log restart`.
   - Update `/srv/log-server/alloy/config.alloy` to tail both router logs.
2. **Fix Chromecast Telemetry**:
   - Set static DHCP reservation on MT6000 for Chromecast (e.g. `192.168.5.203`).
   - Update `CHROMECAST_ADDR=192.168.5.203:5555` in `/etc/systemd/system/chromecast-logcat.service` and `/usr/local/bin/chromecast-adb-keepalive`.

### Phase 3: Edge Monitoring & Safety Guardrails (Medium Priority)
1. **Deploy `gateway-9-1-monitor`**:
   - Copy `/usr/local/bin/gateway-2-1-monitor` to `/usr/local/bin/gateway-9-1-monitor`.
   - Update to ping `192.168.9.1`, query `ubus call network.interface.wan status` (and `pppoe-wan`) via SSH `glinet-9.1`.
   - Write state to `/var/lib/router-dashboard/gateway-9-1-current.json` and event log to `gateway-9-1-events.jsonl`.
   - Create and enable `gateway-9-1-monitor.service`.
2. **Add Container Egress Verification & Hook Validator to `stack-monitor.py`**:
   - Add egress verification probe in `stack-monitor.py`: verify CT 105/111/112 exit via Switzerland and CT 107/108 exit via US.
   - Add boot-hook validator probe: check BE9300 `srcnat` jumps and resolv files.
   - Expose Prometheus metrics: `container_egress_valid{container="105"} 1` and `router_buildout_persist_healthy 1`.

### Phase 4: Dashboard & Alerting Modernization (Normal Priority)
1. **Update Prometheus & Node Exporters**:
   - Enable `prometheus-node-exporter` in CT 111 and CT 112.
   - Add CT 111 (`192.168.9.171:9100`) and CT 112 (`192.168.9.219:9100`) to `prometheus.yml`.
   - Remove dead `192.168.9.1:9100` router target until OpenWrt node exporter is configured.
2. **Modernize `router-dashboard` & Grafana Panels**:
   - Update `/usr/local/bin/router-dashboard` to reflect the 2-router topology (BE9300 9.1 + MT6000 5.1).
   - Repoint `Router & Network Infrastructure` panels in Grafana to `gateway_9_1_*`.
3. **Implement Missing Critical Alert Rules**:
   - Add Grafana/Prometheus alert rules for:
     - `up == 0` (Scrape target down > 2m)
     - `handshake_age_s > 180` (WireGuard tunnel stalled)
     - `gateway_9_1_wan_up == 0` (Edge WAN disconnect > 3m)
     - `container_egress_valid == 0` (Container VPN leak)
