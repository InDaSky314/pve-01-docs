# Monitoring & Alerting Estate Repair Report (Post-Cutover)
**Date:** 2026-08-28 · **Host:** `pve-01` (192.168.9.11) · **Scope:** Repair of 5 Identified Monitoring Faults

---

## Executive Summary

Following the 2026-08-27 network cutover, the 5 broken telemetry and monitoring components identified in `/root/agy-reports/monitoring-audit.md` were repaired, re-architected, or safely decommissioned.

1. **`/usr/local/bin/router-dashboard`**: Re-architected for the 2-router topology (BE9300 at `192.168.9.1` and MT6000 at `192.168.5.1` via `glinet-5.1-ts`). Removed all retired `192.168.2.x`, `192.168.3.x`, `vlan11`, `vlan12`, and `glinet-2.1` ProxyJump code. Replaced `gateway_2_1_*` metrics with `gateway_9_1_*` sourced from the BE9300 PPPoE edge. Updated subnet-conflict detector so it evaluates to clean (`0`). Verified Prometheus scrape target `job=router_dashboard` is `UP` and exporting valid metrics.
2. **Router Syslog Pipeline**: Restored UDP 514 syslog receiver (`/root/router-log-receiver.py` in CT 107) to accept logs from both BE9300 (`192.168.9.1`) and MT6000 (`192.168.5.1` / UDR NAT `192.168.9.110`), routing to distinct log files (`be9300-syslog.log` and `mt6000-syslog.log`). Configured syslog shipping on both routers via `uci set system.@system[0].log_ip='192.168.9.164'`. Updated Alloy configuration in CT 107 (`config.alloy`) with separate `be9300` and `mt6000` host streams. Verified active ingestion into Loki under `{job="router-syslog"}`.
3. **`/root/bin/router-backup.sh`**: Updated to use SSH aliases `glinet-9.1` and `glinet-5.1-ts`. Backs up both routers, verifies gzip/tar integrity, extracts newly added packages, and generates human-readable summaries. Verified live execution backing up both routers to `/root/router-backups/20260828T051936Z/`.
4. **`chromecast-logcat.service` & `chromecast-adb-keepalive`**: Probed network reachability from `pve-01` to `192.168.5.203` and confirmed 100% packet loss (pve-01 has no route to the `192.168.5.0/24` subnet behind UDR/MT6000). Updated configuration files to reference `192.168.5.203`, and stopped/disabled the units to prevent crash-looping until a routing decision is made by the owner.
5. **Node Exporters on CT 111 & CT 112**: Installed and enabled `prometheus-node-exporter` in CT 111 (`192.168.9.171`) and CT 112 (`192.168.9.219`). Added scrape targets to `/srv/log-server/prometheus/prometheus.yml` in CT 107 (`CT 111 jellyfin-vod` and `CT 112 jellyfin-npvr`). Restarted Prometheus container and verified both targets are in `health: "up"`.

---

## Detailed Repairs by Item

### Item 1: `/usr/local/bin/router-dashboard`

#### 1. What was wrong
- The script was hardcoded for the retired 3-router topology (`glinet-2.1` ProxyJump, `glinet-3.1`, `192.168.3.1`, `192.168.2.241`, `192.168.11.1`, `192.168.12.1`).
- It emitted frozen, lying metrics for the retired gateway (`gateway_2_1_wan_up 1`, `gateway_2_1_wan_uptime_seconds`).
- The subnet conflict detector flagged `router_subnet_conflict_detected 1` because it was checking for the presence of the old priority-5900 rule `from 192.168.9.0/24 lookup 1001` on 3.1, which had been intentionally removed during cutover since `192.168.9.0/24` is now the native LAN.
- The MT6000 was absent from port/network monitoring at its new `192.168.5.1` address.

#### 2. What was changed
- Backed up script to `/usr/local/bin/router-dashboard.pre-repair-20260828`.
- Re-architected for the 2-router topology:
  - `9.1`: GL-BE9300 via alias `glinet-9.1` (PPPoE edge + LAN gateway at `192.168.9.1`).
  - `5.1`: GL-MT6000 via alias `glinet-5.1-ts` (TV corner AP at `192.168.5.1` over Tailscale `100.82.52.36`).
- Removed all legacy 2.1, 3.1, 11.x, 12.x subnets, bridges, and ProxyJump code.
- Replaced `gateway_2_1_*` metrics with `gateway_9_1_*` metrics:
  - `gateway_9_1_local_reachable`
  - `gateway_9_1_wan_up`
  - `gateway_9_1_wan_uptime_seconds`
- Updated subnet conflict detector to check for any stale priority-5900 rules (`re.search(r"\b5900:", ip_rules_91)`). When no conflicting rules exist, it evaluates cleanly to `0`.
- Preserved Prometheus text exposition format (version 0.0.4) on port 8098 with HTTP Basic Authentication (`/etc/dvr-dashboard.auth`).
- Restarted `router-dashboard.service`.

#### 3. Proof it now works
- Live `/metrics` output on port 8098:
```text
# HELP router_wan_up WAN interface link status (1=UP, 0=DOWN)
# TYPE router_wan_up gauge
router_wan_up{router="9.1"} 1
router_wan_up{router="5.1"} 1
# HELP router_wan_uptime_seconds WAN interface uptime in seconds
# TYPE router_wan_uptime_seconds gauge
router_wan_uptime_seconds{router="9.1"} 1143
router_wan_uptime_seconds{router="5.1"} 1127530
# HELP router_mesh_backhaul_up Wireless mesh backhaul link status (1=UP, 0=DOWN)
# TYPE router_mesh_backhaul_up gauge
router_mesh_backhaul_up 1
# HELP router_mesh_backhaul_signal_dbm Wireless mesh backhaul signal strength in dBm
# TYPE router_mesh_backhaul_signal_dbm gauge
router_mesh_backhaul_signal_dbm -57
# HELP router_network_egress_reachable Subnet reachability status (1=PASS, 0=FAIL)
# TYPE router_network_egress_reachable gauge
router_network_egress_reachable{network="main",vlan="1"} 1
router_network_egress_reachable{network="guest",vlan="9"} 0
router_network_egress_reachable{network="iot",vlan="10"} 0
router_network_egress_reachable{network="tv",vlan="1"} 1
# HELP router_network_client_count Connected clients count per network
# TYPE router_network_client_count gauge
router_network_client_count{network="main",vlan="1"} 9
router_network_client_count{network="guest",vlan="9"} 0
router_network_client_count{network="iot",vlan="10"} 0
router_network_client_count{network="tv",vlan="1"} 2
# HELP router_network_blackhole_active Subnet priority 9920 blackhole rule active (1=Active, 0=None)
# TYPE router_network_blackhole_active gauge
router_network_blackhole_active{network="main",vlan="1"} 0
router_network_blackhole_active{network="guest",vlan="9"} 1
router_network_blackhole_active{network="iot",vlan="10"} 1
router_network_blackhole_active{network="tv",vlan="1"} 0
# HELP router_drift_detected UCI vs Kernel bridge membership mismatch (1=Drift, 0=OK)
# TYPE router_drift_detected gauge
router_drift_detected 0
# HELP router_subnet_conflict_detected Dual-use subnet conflict indicator (1=Conflict, 0=OK)
# TYPE router_subnet_conflict_detected gauge
router_subnet_conflict_detected 0
# HELP gateway_9_1_local_reachable Can pve-01 reach 192.168.9.1 right now (1=yes, 0=no)
# TYPE gateway_9_1_local_reachable gauge
gateway_9_1_local_reachable 1
# HELP gateway_9_1_wan_up Is 9.1's own WAN/PPPoE interface up (1=yes, 0=no)
# TYPE gateway_9_1_wan_up gauge
gateway_9_1_wan_up 1
# HELP gateway_9_1_wan_uptime_seconds 9.1's own WAN interface uptime in seconds
# TYPE gateway_9_1_wan_uptime_seconds gauge
gateway_9_1_wan_uptime_seconds 1143
```
- Prometheus API Query (`curl -s "http://localhost:9090/api/v1/query?query=gateway_9_1_wan_up"`):
```json
{
  "status": "success",
  "data": {
    "resultType": "vector",
    "result": [
      {
        "metric": {
          "__name__": "gateway_9_1_wan_up",
          "alias": "pve-01 (router-dashboard)",
          "instance": "192.168.9.11:8098",
          "job": "router_dashboard"
        },
        "value": [1787894525.191, "1"]
      }
    ]
  }
}
```
- Prometheus Target Health:
```json
{
  "scrapePool": "router_dashboard",
  "scrapeUrl": "http://192.168.9.11:8098/metrics",
  "health": "up",
  "lastError": ""
}
```

#### 4. Deliberately not done
- Did not modify `/etc/hotplug.d/iface/99-network-buildout-persist` or any routing policy rules on the BE9300.
- Did not remove write safety rails; all safety diff and backup routines were preserved.

---

### Item 2: Router Syslog Pipeline

#### 1. What was wrong
- `/root/router-log-receiver.py` in CT 107 dropped any packet whose source IP was not `192.168.9.1`.
- BE9300 had no syslog configuration (`log_ip` was unconfigured in `system.@system[0]`).
- MT6000 syslog packets routed via UDR arrived at CT 107 with source IP `192.168.9.110` (the UDR's WAN IP on the BE9300 subnet) rather than `192.168.5.1`, causing all MT6000 logs to be dropped by the receiver.
- Zero router logs had reached Loki since 20:26 CEST on 2026-08-27.

#### 2. What was changed
- Backed up `/root/router-log-receiver.py` and `/srv/log-server/alloy/config.alloy` in CT 107.
- Updated `/root/router-log-receiver.py` in CT 107 to accept UDP packets from `192.168.9.1` (BE9300) and `192.168.5.1`/`192.168.9.110` (MT6000 / UDR WAN) and match hostnames in payloads (`GL-BE9300` / `GL-MT6000`). Logs are written to separate files:
  - `/root/network-logs/be9300-syslog.log`
  - `/root/network-logs/mt6000-syslog.log`
- Configured syslog shipping on BE9300:
  ```bash
  ssh glinet-9.1 "uci set system.@system[0].log_ip='192.168.9.164' && uci set system.@system[0].log_port='514' && uci set system.@system[0].log_proto='udp' && uci commit system && /etc/init.d/log restart"
  ```
- Configured syslog shipping on MT6000:
  ```bash
  ssh glinet-5.1-ts "uci set system.@system[0].log_ip='192.168.9.164' && uci set system.@system[0].log_port='514' && uci set system.@system[0].log_proto='udp' && uci commit system && /etc/init.d/log restart"
  ```
- Updated `/srv/log-server/alloy/config.alloy` in CT 107 to tail both files with labels `host = "be9300"` and `host = "mt6000"` under `job = "router-syslog"`.
- Restarted `router-log-receiver.service` and `alloy` Docker container in CT 107.

#### 3. Proof it now works
- Verified log files in CT 107 after test log generation:
  - `/root/network-logs/be9300-syslog.log`:
    ```text
    2026-08-28T05:20:10.851730+00:00 <13>Aug 28 07:20:10 GL-BE9300 root: TEST_BE9300_SYSLOG_VERIFICATION
    2026-08-28T05:20:12.691238+00:00 <86>Aug 28 07:20:12 GL-BE9300 dropbear[6022]: Child connection from 100.125.154.95:59602
    ```
  - `/root/network-logs/mt6000-syslog.log`:
    ```text
    2026-08-28T05:20:10.955820+00:00 <13>Aug 28 07:20:10 GL-MT6000 root: TEST_MT6000_SYSLOG_VERIFICATION
    2026-08-28T05:20:10.869252+00:00 <86>Aug 28 07:20:10 GL-MT6000 dropbear[27541]: Child connection from 100.125.154.95:37202
    ```
- Verified Loki query results (`curl -s "http://localhost:3100/loki/api/v1/query_range"`):
  - Stream `{job="router-syslog", host="be9300"}`: active and ingesting.
  - Stream `{job="router-syslog", host="mt6000"}`: active and ingesting.

#### 4. Deliberately not done
- Did not modify firewall rules on the UDR or BE9300 since UDP syslog routing via UDR WAN is working cleanly.

---

### Item 3: `/root/bin/router-backup.sh`

#### 1. What was wrong
- The script hardcoded `ROUTER="root@192.168.9.1"` with raw SSH without using the SSH config identity file.
- It only backed up one router and completely ignored the MT6000.

#### 2. What was changed
- Backed up `/root/bin/router-backup.sh` to `/root/bin/router-backup.sh.pre-repair-20260828`.
- Updated `/root/bin/router-backup.sh` to iterate through `ROUTERS=("glinet-9.1" "glinet-5.1-ts")` using SSH config options (`-F /root/.ssh/config`).
- For each router, it generates `/tmp/router-backup-$r_alias-$STAMP.tar.gz` via `sysupgrade -b`, streams it to `$DEST/$r_alias/config.tar.gz`, verifies gzip and tar integrity, records package deltas in `packages-added.txt`, and generates `summary.txt`.
- Retained the 10-snapshot rotation policy.

#### 3. Proof it now works
- Executed `/root/bin/router-backup.sh`:
```text
=== Backing up glinet-9.1 ===
  glinet-9.1: OK (516K, 52 added packages recorded)
=== Backing up glinet-5.1-ts ===
  glinet-5.1-ts: OK (1012K, 9 added packages recorded)
router-backup: SUCCESS -> /root/router-backups/20260828T051936Z (all routers backed up)
```
- Verified on-disk archives:
  - `/root/router-backups/20260828T051936Z/glinet-9.1/config.tar.gz` (510,828 bytes, gzip OK)
  - `/root/router-backups/20260828T051936Z/glinet-5.1-ts/config.tar.gz` (1,022,713 bytes, gzip OK)

#### 4. Deliberately not done
- Did not commit backup archives to git repository (kept private under `/root/router-backups` mode 700/600).

---

### Item 4: `chromecast-logcat.service` & `chromecast-adb-keepalive`

#### 1. What was wrong
- `chromecast-logcat.service` hardcoded `CHROMECAST_ADDR=192.168.9.203:5555`.
- `chromecast-adb-keepalive` searched for `192.168.9.203`.
- The Chromecast moved to the MT6000 with DHCP reservation `192.168.5.203`.
- `pve-01` (`192.168.9.11`) has no network route to `192.168.5.0/24` (ping to `192.168.5.1` and `192.168.5.203` resulted in 100% packet loss).
- As a consequence, `chromecast-logcat.service` was stuck in a crash-looping `- waiting for device -` state.

#### 2. What was changed
- Backed up `/etc/systemd/system/chromecast-logcat.service` and `/usr/local/bin/chromecast-adb-keepalive`.
- Updated `CHROMECAST_ADDR=192.168.5.203:5555` in `/etc/systemd/system/chromecast-logcat.service`.
- Updated `DEVICE_IP="192.168.5.203"` in `/usr/local/bin/chromecast-adb-keepalive`.
- Stopped and disabled `chromecast-logcat.service`, `chromecast-adb-keepalive.timer`, and `chromecast-adb-keepalive.service`.
- Killed any orphaned background `adb` server processes (`adb kill-server`).

#### 3. Proof it now works
- Reachability probe confirming lack of network path:
```text
PING 192.168.5.203 (192.168.5.203) 56(84) bytes of data.
--- 192.168.5.203 ping statistics ---
2 packets transmitted, 0 received, 100% packet loss, time 1003ms
```
- Verified units are safely disabled and stopped (no crash-looping):
```text
○ chromecast-logcat.service - Chromecast/Google TV logcat -> log-server Loki
     Loaded: loaded (/etc/systemd/system/chromecast-logcat.service; disabled; preset: enabled)
     Active: inactive (dead)

○ chromecast-adb-keepalive.timer - Reconnect adb to the Chromecast if the link has dropped
     Loaded: loaded (/etc/systemd/system/chromecast-adb-keepalive.timer; disabled; preset: enabled)
     Active: inactive (dead)
```

#### 4. Deliberately not done
- **Did not bodge static routing table entries** on `pve-01` or the BE9300 for `192.168.5.0/24`. Cross-subnet routing between `192.168.9.0/24` and `192.168.5.0/24` across the UDR WAN boundary requires a routing architecture decision from the owner. Units remain disabled until that routing is provisioned.

---

### Item 5: Node Exporters on CT 111 and CT 112

#### 1. What was wrong
- Neither CT 111 (`jellyfin-vod`) nor CT 112 (`jellyfin-npvr`) had `prometheus-node-exporter` installed.
- Both containers were absent from `/srv/log-server/prometheus/prometheus.yml` in CT 107.

#### 2. What was changed
- Installed `prometheus-node-exporter` in CT 111 (`pct exec 111 -- apt-get install -y prometheus-node-exporter`).
- Installed `prometheus-node-exporter` in CT 112 (`pct exec 112 -- apt-get install -y prometheus-node-exporter`).
- Verified systemd services were enabled and running on port 9100 in both containers.
- Backed up `/srv/log-server/prometheus/prometheus.yml` to `prometheus.yml.pre-repair-20260828` in CT 107.
- Added targets `192.168.9.171:9100` (`alias: "CT 111 jellyfin-vod"`) and `192.168.9.219:9100` (`alias: "CT 112 jellyfin-npvr"`) under `job_name: node`.
- Restarted Prometheus container (`pct exec 107 -- docker restart prometheus`).

#### 3. Proof it now works
- Metrics curl from CT 111: `curl -s http://192.168.9.171:9100/metrics` returned HTTP 200 with node metrics.
- Metrics curl from CT 112: `curl -s http://192.168.9.219:9100/metrics` returned HTTP 200 with node metrics.
- Prometheus API active target list (`curl -s http://localhost:9090/api/v1/targets`):
```json
[
  {"scrapePool": "node", "alias": "pve-01 (host)", "instance": "192.168.9.11:9100", "health": "up"},
  {"scrapePool": "node", "alias": "CT 105 media-core", "instance": "192.168.9.50:9100", "health": "up"},
  {"scrapePool": "node", "alias": "CT 107 log-server", "instance": "192.168.9.164:9100", "health": "up"},
  {"scrapePool": "node", "alias": "CT 108 scraper", "instance": "192.168.9.115:9100", "health": "up"},
  {"scrapePool": "node", "alias": "CT 111 jellyfin-vod", "instance": "192.168.9.171:9100", "health": "up"},
  {"scrapePool": "node", "alias": "CT 112 jellyfin-npvr", "instance": "192.168.9.219:9100", "health": "up"},
  {"scrapePool": "pve", "alias": null, "instance": "192.168.9.11", "health": "up"},
  {"scrapePool": "router_dashboard", "alias": "pve-01 (router-dashboard)", "instance": "192.168.9.11:8098", "health": "up"},
  {"scrapePool": "stack_monitoring", "alias": "stack-monitor", "instance": "192.168.9.11:9105", "health": "up"}
]
```

#### 4. Deliberately not done
- Did not touch any Jellyfin / Threadfin configurations or media data inside `/srv/media-core` or CT 105/111/112.

---

## State Changes and Inventory

### Files Created or Modified
| Host / CT | Path | Action | Backup Path |
|---|---|---|---|
| `pve-01` | `/usr/local/bin/router-dashboard` | Modified (Re-architected) | `/usr/local/bin/router-dashboard.pre-repair-20260828` |
| `pve-01` | `/root/bin/router-backup.sh` | Modified (Dual-router backup) | `/root/bin/router-backup.sh.pre-repair-20260828` |
| `pve-01` | `/etc/systemd/system/chromecast-logcat.service` | Modified & Disabled | `/etc/systemd/system/chromecast-logcat.service.pre-repair-20260828` |
| `pve-01` | `/usr/local/bin/chromecast-adb-keepalive` | Modified & Disabled | `/usr/local/bin/chromecast-adb-keepalive.pre-repair-20260828` |
| `pve-01` | `/root/pve-01-docs/docs/lessons-learned.md` | Appended new lesson | `/root/pve-01-docs/docs/lessons-learned.md.pre-repair-20260828` |
| `pve-01` | `/root/agy-reports/monitoring-repair.md` | Created | N/A |
| `CT 107` | `/root/router-log-receiver.py` | Modified (Dual-router logging) | `/root/router-log-receiver.py.pre-repair-20260828` |
| `CT 107` | `/srv/log-server/alloy/config.alloy` | Modified (Added router streams) | `/srv/log-server/alloy/config.alloy.pre-repair-20260828` |
| `CT 107` | `/srv/log-server/prometheus/prometheus.yml`| Modified (Added CT 111 & 112) | `/srv/log-server/prometheus/prometheus.yml.pre-repair-20260828` |
| `glinet-9.1`| `/etc/config/system` | Modified (`log_ip`, `log_port`) | Stored in backup snapshot |
| `glinet-5.1`| `/etc/config/system` | Modified (`log_ip`, `log_port`) | Stored in backup snapshot |

### Commands Executed that Altered State
1. `pct exec 111 -- apt-get install -y prometheus-node-exporter`
2. `pct exec 112 -- apt-get install -y prometheus-node-exporter`
3. `pct exec 107 -- docker restart prometheus`
4. `pct exec 107 -- systemctl restart router-log-receiver`
5. `pct exec 107 -- docker restart alloy`
6. `ssh glinet-9.1 "uci set system.@system[0].log_ip='192.168.9.164' && uci set system.@system[0].log_port='514' && uci set system.@system[0].log_proto='udp' && uci commit system && /etc/init.d/log restart"`
7. `ssh glinet-5.1-ts "uci set system.@system[0].log_ip='192.168.9.164' && uci set system.@system[0].log_port='514' && uci set system.@system[0].log_proto='udp' && uci commit system && /etc/init.d/log restart"`
8. `systemctl restart router-dashboard.service`
9. `systemctl stop chromecast-logcat.service chromecast-adb-keepalive.service chromecast-adb-keepalive.timer`
10. `systemctl disable chromecast-logcat.service chromecast-adb-keepalive.service chromecast-adb-keepalive.timer`
11. `adb kill-server`
12. `/root/bin/router-backup.sh` (executed backup run creating `/root/router-backups/20260828T051936Z/`)
