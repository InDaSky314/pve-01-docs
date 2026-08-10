# Router Network Visibility & Control Dashboard: Architecture & Implementation Plan

**Date:** 2026-08-10  
**Author:** AI Agent / Antigravity  
**Status:** Plan Mode (Read-Only Proposal — No Changes Applied)  
**Target Host:** `pve-01` (co-located alongside `dvr-dashboard`)  
**Target Routers:** `glinet-9.1` (GL-MT6000), `glinet-3.1` (GL-BE9300), `glinet-2.1`, `unifi-1.1`

---

## 1. Executive Summary & Goals

This plan outlines the architecture for a dedicated **Router Network-Visibility Dashboard** hosted on `pve-01`. The primary objective is to provide a single, trustworthy interface showing the complete network, routing, client, and security state across all local subnets/VLANs.

### Primary Requirements
1. **Per-VLAN / Network Visibility**: Subnet, gateway, VLAN ID, live connected clients (wired + wireless).
2. **True Routing Path**: Display which VPN tunnel (or bare WAN) each network rides, including raw Linux kernel policy routing (`ip rule` / table 100x) that lies outside GL.iNet's `gl-session` API.
3. **Active Egress & Reachability Verification**: Perform real HTTP egress checks per subnet (verifying real egress IP against a bare-WAN baseline), rather than trusting GL.iNet or OpenWrt status flags.
4. **DHCP & Static Binding Visibility**: Active leases and static IP reservations per VLAN.
5. **Local Script & Persistence Tracking**: Operational status of hotplug persistence scripts (e.g. `/etc/hotplug.d/iface/99-vlan-vpn`) on the router.
6. **Two-Phase Evolution**: Phase 1 focus on read-only monitoring; Phase 2 introducing write access (VLAN-to-tunnel mapping, DHCP edits, tunnel toggles) governed by strict safety rails.

---

## 2. Data Model & Polling Strategy

To maintain high responsiveness without overloading router CPU or network bandwidth, data sources are categorized into **Fast State** (local RPC/kernel), **Slow/Active State** (WAN egress checks), and **Config State** (infrequent config reads).

```
+-----------------------------------------------------------------------------------+
|                                 pve-01 Host                                       |
|                                                                                   |
|  +-----------------------------------------------------------------------------+  |
|  |                     router-dashboard Service (Port 8098)                   |  |
|  |                                                                             |  |
|  |  +----------------------+   +---------------------+   +------------------+  |  |
|  |  | Background Poll Loop |   | In-Memory Cache     |   | Web UI Server    |  |  |
|  |  | (Thread / Async)     |-->| (State + Egress)    |-->| (HTML/REST API)  |  |  |
|  |  +----------------------+   +---------------------+   +------------------+  |  |
|  +-----------------------------------|-----------------------------------------+  |
+--------------------------------------|--------------------------------------------+
                                       | Persistent SSH (ControlPersist)
                                       v
                     +-----------------------------------+
                     |      GL.iNet Routers (OpenWrt)    |
                     |                                   |
                     |  - ubus call gl-session call ...  |
                     |  - ip rule show / ip route show   |
                     |  - bridge link / hostapd_cli      |
                     |  - curl --interface br-vlanX      |
                     +-----------------------------------+
```

### 2.1 Polling Breakdown

| Data Point | Source | Command / Method | Interval | Cache Strategy |
|---|---|---|---|---|
| **VLAN / Subnet Info** | Router RPC | `ubus call gl-session call '{"module":"vlan_subnet","func":"get_subnets","params":{}}'` | 30s | Cache 30s; invalidate on write |
| **GL.iNet API Tunnels** | Router RPC | `ubus call gl-session call '{"module":"vpn-client","func":"get_tunnel","params":{}}'` | 30s | Cache 30s; invalidate on write |
| **Raw Policy Routing** | Linux Kernel | `ip rule show` & `ip route show table all` | 30s | Cache 30s |
| **Connected Clients** | Router RPC & Hostapd | `gl-clients list`, `/tmp/dhcp.leases`, `hostapd.wlan* get_clients` | 30s | Cache 30s |
| **DHCP Reservations** | Router RPC | `ubus call gl-session call '{"module":"vlan_subnet","func":"get_static_bind_list","params":{}}'` | 60s | Cache 60s |
| **Hotplug Script Status** | Router Filesystem | `test -f /etc/hotplug.d/iface/99-vlan-vpn && stat -c %Y /etc/hotplug.d/iface/99-vlan-vpn` | 60s | Cache 60s |
| **Active Egress & Reachability** | Active HTTP Test | `curl -s --interface br-vlan<N> --max-time 4 https://ifconfig.me/all.json` | **5 minutes** (or manual refresh) | Cache 5 min |
| **Bare WAN Reference** | Active HTTP Test | `curl -s --interface eth1 --max-time 4 https://ifconfig.me/all.json` (on 9.1) | **5 minutes** | Cache 5 min |

### 2.2 Active Egress & Reachability Design
- **Why 5 minutes?** Active egress tests require outgoing HTTP calls over every active VLAN interface. Polling external endpoints (e.g. `ifconfig.me`, `ip-api.com`, `ipify.org`) every 30 seconds would risk IP rate-limiting, unnecessary WAN bandwidth usage, and unnecessary CPU wakeups on the router.
- **On-Demand Override**: The UI will provide a **"Test Reachability Now"** button allowing the user to trigger immediate, live egress validation on demand.
- **Fail-Closed Awareness**: The dashboard will explicitly check for priority blackhole rules (e.g. `from all iif br-vlan12 blackhole` at priority 9920). If a VLAN has no active tunnel mapping and hits a blackhole rule, it is marked as `BLACKHOLED (Fail-Closed)` in the UI rather than `OFFLINE`.

---

## 3. Backend Architecture: Extension vs. Sibling Service

### 3.2 Evaluation of Options

* **Option A: Extend `/usr/local/bin/dvr-dashboard` directly**
  * *Pros*: Single systemd service, single HTTP port (8099), shared UI navigation.
  * *Cons*: Monolithic script (~1300 lines); SSH polling or network delays to routers could block or degrade media-DVR scheduling and Jellyfin API checks.
* **Option B: Standalone Sibling Service (`router-dashboard` on Port 8098)**
  * *Pros*: Complete process and failure isolation. A router SSH timeout or hung sub-shell will never impact DVR power automation or Jellyfin recording checks. Independent logging, independent systemd unit. Shared authentication model (`/etc/dvr-dashboard.auth`).
  * *Cons*: Requires a second systemd service unit and second port (easily linked in `dvr-dashboard`'s Links tab or embedded via iframe/proxy).

### 3.3 Recommendation: Standalone Sibling Service (`router-dashboard`)
We recommend building **`router-dashboard`** as a lightweight, zero-dependency Python service running on **port 8098** (`/usr/local/bin/router-dashboard`, managed by `/etc/systemd/system/router-dashboard.service`).

- **Shared Auth Pattern**: Reads `/etc/dvr-dashboard.auth` (`username:password`), enforcing HTTP Basic Authentication via stdlib `http.server` & `hmac.compare_digest` — identically matching `dvr-dashboard`.
- **Cross-Dashboard Integration**: Add a direct link and status dot for the Router Dashboard in `dvr-dashboard`'s **Links** tab.

---

## 4. Router SSH Transport & Multi-Path Resiliency

### 4.1 SSH Connection Management (ControlPersist)
Running naive `ssh` subprocess calls every 30 seconds introduces connection overhead (200–500ms handshake per execution, process spawning on OpenWrt).

**Solution**:
1. Configure SSH **ControlMaster / ControlPersist** in `/root/.ssh/config`:
   ```sshconfig
   Host glinet-9.1
       HostName 192.168.9.1
       User root
       IdentityFile /root/.ssh/id_ed25519_routers
       IdentitiesOnly yes
       ControlMaster auto
       ControlPath ~/.ssh/sockets/%r@%h:%p
       ControlPersist 10m

   Host glinet-3.1
       HostName 192.168.3.1
       User root
       IdentityFile /root/.ssh/id_ed25519_routers
       IdentitiesOnly yes
       ControlMaster auto
       ControlPath ~/.ssh/sockets/%r@%h:%p
       ControlPersist 10m
   ```
2. **Single-Pass Data Collector**: Instead of issuing 5-6 separate SSH commands per poll, `router-dashboard` executes **one bundled SSH command** per router that returns a single, structured JSON payload containing all RPC data, kernel rules, and file statuses in a single round-trip (<20ms execution over existing multiplexed socket).

### 4.2 Multi-Path Resilience (Handling Router Outages)
As documented during the 2026-08-10 `3.1` mesh disassociation incident, direct LAN IP access (`192.168.3.1`) can drop while alternative admin paths remain 100% operational. `router-dashboard` will implement automatic failover probing across all 3 documented admin paths for `3.1`:

1. **Path 1 (Primary LAN)**: `root@192.168.3.1` (ConnectTimeout=3s)
2. **Path 2 (Tailscale OOB)**: `root@<3.1-tailscale-ip>` (ConnectTimeout=3s)
3. **Path 3 (ProxyJump via 2.1)**: `ssh -J glinet-2.1 root@192.168.2.241` (ConnectTimeout=3s)

If Primary LAN fails, the dashboard automatically attempts Path 2, then Path 3, and highlights the active management path in the UI (e.g. `glinet-3.1 (Reachable via Tailscale Fallback)`).

---

## 5. UI & User Experience Design

The dashboard will feature a dark-mode first, responsive layout using Vanilla CSS and modern web standards.

```
+-----------------------------------------------------------------------------------+
|  [Router Visibility & Control Dashboard]       [Refresh Now]  Updated: 15:32:00   |
|  Routers: 9.1 [ONLINE (LAN)] | 3.1 [ONLINE (Tailscale)] | 2.1 [ONLINE]           |
+-----------------------------------------------------------------------------------+
| [ All Networks ]  [ Router 9.1 ]  [ Router 3.1 ]  [ Routing Matrix ]              |
+-----------------------------------------------------------------------------------+
| +------------------------------------+ +----------------------------------------+ |
| | GIOT (VLAN 11)         [9.1] ON    | | WALDO (VLAN 12)        [3.1] ON      | |
| | Subnet: 192.168.11.0/24            | | Subnet: 192.168.12.0/24                | |
| | Gateway: 192.168.11.1              | | Gateway: 192.168.12.1                  | |
| | DHCP: 192.168.11.100 - .249 (14)   | | DHCP: 192.168.12.100 - .249 (3)        | |
| | Routing: WireGuard (us-nyc)        | | Routing: ip rule priority 5900 (us-buf)| |
| | Engine: GL.iNet API (MAC ipset)    | | Engine: Raw Kernel Policy Table 1002   | |
| | Real Egress: 185.220.x.x (US) PASS | | Real Egress: 198.51.x.x (US) PASS      | |
| | Clients: 4 Connected (3 Wifi/1 Wire) | Clients: 2 Connected (2 Wifi)          | |
| | Hotplug Script: Installed & Active | | Hotplug Script: Installed & Active     | |
| +------------------------------------+ +----------------------------------------+ |
+-----------------------------------------------------------------------------------+
```

### 5.1 Key UI Views
1. **Summary Status Banner**: Live health indicators for `9.1`, `3.1`, `2.1`, `1.1`, bare-WAN exit IP, total active clients, and global status warnings.
2. **Per-Network Cards**: Distinct cards for `Main`, `Guest`, `IoT`, `GIOT`, `WALDO`, `Open-Fields`. Each card displays:
   - Network Name, Router Host, Subnet CIDR, Gateway IP.
   - Live VPN Tunnel Assignment & Routing Engine (GL.iNet API vs. Raw `ip rule`).
   - Verified Real Egress IP, Egress Location, and Reachability Status.
   - Connected Clients (expandable device list with hostname, IP, MAC, signal strength, and interface).
   - DHCP Reservation count and Hotplug Script status.
3. **Combined Routing Matrix**: Tabular view mapping `SSID / Network -> Interface -> Routing Engine -> Table/Mark -> Egress IP -> Status`.

---

## 6. Phasing & Safety Protocols

### Phase 1: Read-Only Monitoring & Auditing (Immediate Goal)
- Full data gathering across all routers (`glinet-9.1`, `glinet-3.1`).
- Polling GL.iNet RPC modules (`vlan_subnet`, `vpn-client`, `gl-clients`).
- Polling raw kernel state (`ip rule`, `ip route table all`, `bridge link`).
- Real active HTTP reachability and egress IP validation.
- Multi-path SSH failover support.
- Zero write endpoints or RPC mutation capabilities exposed.

### Phase 2: Controlled Write Access & Configuration Management (Future Scope)
Exposing write operations (e.g. assigning a VLAN to a VPN tunnel, adding DHCP reservations, triggering a wireless reconnect) requires rigorous safety rails to prevent bricking or isolating routers.

#### Concrete Safety Rails for Phase 2 Implementation:
1. **Automated Pre-Write Config Backup**:
   - Before executing any RPC or CLI mutation, `router-dashboard` automatically issues a full system backup on the target router:
     ```bash
     ssh <router> "sysupgrade -b /tmp/backup_pre_edit_\$(date +%s).tar.gz"
     ```
   - The backup file is immediately copied to `/var/backups/router-dashboard/` on `pve-01`.
2. **Strict API Path Compliance**:
   - Standard GL.iNet actions MUST use `gl-session call` (`vpn-client.set_tunnel`, `vlan_subnet.add_static_bind`), NEVER raw `uci set` on `route_policy` (preventing numeric `group_id` / `peer_id` corruptions).
   - Custom SSID network reassignments MUST follow the safe subset recipe: edit `network` on an existing GUI-created interface via LuCI/UCI, never raw-create new `wifi-iface` sections.
3. **Dry-Run & Preview Step**:
   - The UI requires explicit two-step user confirmation: Step 1 displays the exact RPC JSON payload / CLI command; Step 2 executes upon confirmation.
4. **Post-Change Active Egress & Reachability Verification (The Standing Rule)**:
   - A `{"result":[]}` or `{"status":"success"}` response is NEVER trusted alone.
   - After a write operation, `router-dashboard` executes a 3-step verification sequence:
     a. Read back UCI / ubus state (`vlan_subnet.get_subnets` or `ip rule show`).
     b. Run active `curl --interface br-vlanN` egress verification.
     c. **Auto-Rollback**: If the router becomes unreachable or egress fails for >30 seconds, automatically restore the pre-edit backup or run `uci revert`.
5. **Audit Logging**:
   - Every mutation request, payload, user IP, timestamp, and post-verification result is logged to `/var/log/router-dashboard/audit.log` on `pve-01`.

---

## 7. Open Risks & Mitigation Strategies

| Risk / Failure Mode | Impact | Mitigation Strategy |
|---|---|---|
| **A. SSH Polling Overhead / CPU Spikes** | Embedded router CPU strain or process table exhaustion | Use SSH `ControlPersist` multiplexing. Issue **one combined SSH payload** per poll cycle instead of multiple commands. |
| **B. SSH Hangs During Router Network Outage** | Dashboard UI freezes waiting for SSH response | Enforce strict `ConnectTimeout=3` and `ServerAliveInterval=3` on SSH calls. Execute SSH polling in asynchronous background threads. |
| **C. Active Egress Polling Being Flagged as Connected Client** | `gl-clients` or DHCP lease table polluted by dashboard checks | `curl --interface br-vlanX` on the router uses the gateway IP (`192.168.X.1`) in the host network namespace. It does **not** trigger ARP or DHCP lease allocation, so `gl-clients` will ignore it. |
| **D. Contention with Proxmox / CT105 / DVR Service** | Resource contention on `pve-01` | `router-dashboard` runs as an isolated systemd service (`port 8098`) with independent process boundaries. No direct interaction with CT105 or Proxmox `pct` tools. |
| **E. Silenced Blackhole Routing** | Traffic on unmapped custom VLANs silently dropped | Dashboard UI explicitly parses priority 9920 `blackhole` rules and flags unassigned VLANs as `BLACKHOLED` in red warnings. |

---

## 8. Proposed Next Steps

1. **Review & Approve Plan**: Human review of this architecture report.
2. **Create Service Scaffold**: Write `/usr/local/bin/router-dashboard` (Python HTTP server + SSH background poller) and `/etc/systemd/system/router-dashboard.service`.
3. **Build Phase 1 Read-Only UI**: Render per-network cards, live egress checks, and multi-path SSH probing.
4. **Integration**: Add status link in `dvr-dashboard`'s Links tab.
