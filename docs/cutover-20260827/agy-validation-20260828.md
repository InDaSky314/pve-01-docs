# Validation Report: Post-Cutover Network & Firewall Fixes

**Target Router:** GL-BE9300 (`192.168.9.1`)  
**Secondary Router:** GL-MT6000 (`192.168.5.1` / Tailscale `100.82.52.36`)  
**Host & Workloads:** Proxmox VE `pve-01` (`192.168.9.11`) · Containers CT 105, 107, 108, 111, 112  
**Date/Time of Audit:** 2026-08-27 23:40 CEST  
**Audit Mode:** Independent & Adversarial Verification (Read-only + Authorized Router Reboot)

---

## Executive Summary

The fixes applied in `/etc/hotplug.d/iface/99-network-buildout-persist` **successfully restored container connectivity and survived an unattended cold reboot**. All containers on `pve-01` egress through their expected geographic exits (Zurich for media/Jellyfin, Ashburn for log-server/scraper, Deutsche Telekom native for the host), and DNS resolution succeeds end-to-end.

However, an adversarial review identified **four significant architectural defects and risks** in the current implementation, ranked by severity below. Most critically, the DNS reconciliation populates **US and German DNS resolvers for the Swiss IPTV tunnel**, introducing Geo-DNS CDN mismatch risk for European streaming streams.

---

## Severity-Ranked Findings

| Level | Finding | Impact | Recommendation |
|---|---|---|---|
| **HIGH** | **Geo-DNS Misalignment on Swiss Tunnel (`wgclient1`)** | Dual Surfshark resolvers (`162.252.172.57` in New York, `149.154.159.92` in Frankfurt) are written to `/tmp/resolv.conf.d/resolv.conf.wgclient1`. CDN queries from `media-core` and `jellyfin-npvr` resolve to US/DE edge nodes, causing geo-blocking/proxy detection. | Override `dns` in `wireguard.peer_1526` (or provide Swiss resolvers like `185.107.56.241` / Swisscom / Cloudflare Swiss EDNS) instead of copying the generic provider default. |
| **MEDIUM** | **Synchronous `/etc/init.d/firewall restart` in Hotplug Handler** | `reconcile_vpn_firewall()` executes a full firewall restart inside an iface hotplug hook, blocking procd's hotplug worker for **11 seconds** during boot (observed: 23:38:45 → 23:38:56). | Replace `/etc/init.d/firewall restart` with `/sbin/fw4 reload` and background the execution, or gate strictly on `$ACTION = "ifup"`. |
| **MEDIUM** | **Unconditional Hotplug Loop Triggering** | `99-network-buildout-persist` lacks `$ACTION` / `$INTERFACE` gating at the top. The entire bridging, rule pruning, WireGuard check, DNS reconciliation, and zone scanning logic executes on *every* hotplug event (`wan`, `lan`, `guest`, `iot`, `vlan11`, `vlan12`, `wgclient*`, `docker0`, `tailscale0`). | Add early exit `[ "$ACTION" = "ifup" ] || exit 0` or match specific interfaces to eliminate wasted CPU cycles during network state churn. |
| **LOW** | **Syntax / Hygiene Artifact in Boot Hook** | Line 88 of `/etc/hotplug.d/iface/99-network-buildout-persist` contains a duplicate `#!/bin/sh` header midway through the script from appending functions. | Clean up the duplicate shebang header during the next planned edit. |

---

## Detailed Section-by-Section Challenge & Verification

### 1. Code Correctness & Idempotency of `reconcile_vpn_dns()` and `reconcile_vpn_firewall()`

#### Analysis of `reconcile_vpn_dns()`
```sh
reconcile_vpn_dns() {
    changed=0
    for t in wgclient1 wgclient2 wgclient3; do
        [ -e "/sys/class/net/$t" ] || continue
        f="/tmp/resolv.conf.d/resolv.conf.$t"
        [ -s "$f" ] && continue
        cfg=$(uci -q get "network.$t.config")
        dns=$(uci -q get "wireguard.$cfg.dns" 2>/dev/null)
        [ -z "$dns" ] && dns=$(uci -q get "wireguard.group_4557.dns" 2>/dev/null)
        [ -z "$dns" ] && continue
        mkdir -p /tmp/resolv.conf.d
        {
            echo "# Interface $t (reconciled by 99-network-buildout-persist)"
            echo "$dns" | tr ',' '\n' | while read -r s; do
                [ -n "$s" ] && echo "nameserver $s"
            done
        } > "$f"
        logger -t netbuildout "reconciled DNS for $t from $cfg"
        changed=1
    done
    [ "$changed" = "1" ] && /etc/init.d/dnsmasq reload >/dev/null 2>&1
    return 0
}
```
* **Idempotency:** **PASS (with caveat).** The check `[ -s "$f" ] && continue` prevents re-writing existing non-empty resolv files and avoids unnecessary dnsmasq reloads on subsequent hotplug events.
* **Flaw:** It relies on `wireguard.$cfg.dns` or `wireguard.group_4557.dns`. On GL.iNet's Surfshark profile, every peer profile defaults to `162.252.172.57,149.154.159.92`. Therefore, on boot, it blindly writes US and German resolvers into the Swiss tunnel's resolv file.

#### Analysis of `reconcile_vpn_firewall()`
```sh
reconcile_vpn_firewall() {
    need_reload=0
    for t in wgclient1 wgclient2 wgclient3 ovpnclient1; do
        [ -e "/sys/class/net/$t" ] || continue
        ip -4 addr show "$t" 2>/dev/null | grep -q "inet " || continue
        nft list chain inet fw4 srcnat 2>/dev/null | grep -q "jump srcnat_$t" && continue

        i=0
        while [ $i -lt 30 ]; do
            n=$(uci -q get "firewall.@zone[$i].name" 2>/dev/null)
            [ -z "$n" ] && break
            if [ "$n" = "$t" ]; then
                uci set "firewall.@zone[$i].device=$t"
                logger -t netbuildout "bound firewall zone $t to device $t (netifd never marked it up)"
                need_reload=1
                break
            fi
            i=$((i+1))
        done
    done
    if [ "$need_reload" = "1" ]; then
        uci commit firewall
        /etc/init.d/firewall restart >/dev/null 2>&1
        logger -t netbuildout "firewall restarted to emit VPN srcnat jumps"
    fi
    return 0
}
```
* **Idempotency:** **PARTIAL PASS.** Once the nftables chain `srcnat` contains `jump srcnat_$t`, the loop triggers `continue` and avoids calling `firewall restart`.
* **Risk (Deadlock & Lock Contention):** Calling `/etc/init.d/firewall restart` inside a hotplug handler blocks procd. In the boot trace below, `firewall restart` blocked for 11 seconds:
  ```
  Thu Aug 27 23:38:44 2026 user.notice network-buildout-persist: hotplug fired (INTERFACE=wan ACTION=ifup)
  Thu Aug 27 23:38:45 2026 user.notice netbuildout: bound firewall zone wgclient2 to device wgclient2 (netifd never marked it up)
  Thu Aug 27 23:38:56 2026 user.notice netbuildout: firewall restarted to emit VPN srcnat jumps
  ```
  While this did not deadlock like `rtp2.sh`'s earlier `network reload` (which was looping in `procd_network.lock`), running `uci commit firewall` concurrently with `rtp2.sh` can cause UCI lock collisions if both attempt to commit to `/etc/config/firewall` simultaneously.

---

### 2. Binding fw4 Zones to `device` vs `network` (Proper Fix vs Workaround)

* **Verdict:** Binding fw4 zones to `device` in UCI is a **workaround around GL.iNet's non-standard WireGuard bringup architecture**.
* **Why it occurs:**
  1. In standard OpenWrt fw4, a firewall zone defined with `network="wgclient1"` queries `ubus call network.interface.wgclient1 status` to resolve the `l3_device`.
  2. GL.iNet's `/lib/netifd/proto/wgclient.sh` deliberately suppresses `proto_init_update` and `ip address add` during `proto_wgclient_setup`. It defers interface announcement until `/etc/hotplug.d/wireguard/ifup.sh` receives the `KEYPAIR-CREATED` event from the kernel.
  3. When fw4 starts at boot, netifd reports `wgclient1/2/3` as pending / unconfigured. fw4 thus creates empty `accept_to_wgclientN` chains and omits the `srcnat` jumps.
  4. When `ifup.sh` later calls `proto_send_update`, netifd marks the interface up, but **fw4 is never notified to re-evaluate the zone**.
  5. Furthermore, GL.iNet's `/usr/bin/rtp2.sh` dynamically regenerates zones on interface events with `uci set firewall.${interface}.network="${interface}"`, continually wiping any static `device` setting back to `network`.
* **Proper Fix:** The root architectural fix would be inside GL.iNet's `ifup.sh` / `rtp2.sh` to execute `/sbin/fw4 reload` after `proto_send_update`, or setting `device` in `instance_set_firewall_main_rule()` in `/usr/bin/rtp2.sh`.

---

### 3. Unattended Reboot & Egress Verification

A live reboot was performed on `192.168.9.1` (`reboot` issued at 23:36:41). Egress IP and DNS lookups were tested directly from inside each container on `pve-01`:

```bash
# Egress and DNS Validation from LXC Containers post-reboot
CT 105 (media-core):   IP=152.89.162.228  (Switzerland - Zurich)  DNS=OK
CT 111 (jellyfin-vod):  IP=152.89.162.228  (Switzerland - Zurich)  DNS=OK
CT 112 (jellyfin-npvr): IP=152.89.162.228  (Switzerland - Zurich)  DNS=OK
CT 107 (log-server):    IP=37.19.206.52    (United States - Ashburn) DNS=OK
CT 108 (scraper):       IP=37.19.206.52    (United States - Ashburn) DNS=OK
pve-01 Host:            IP=93.209.195.65   (Deutsche Telekom AG - DE) DNS=OK
```

#### Firewall `srcnat` Jumps Emitted Post-Reboot
```text
table inet fw4 {
	chain srcnat {
		type nat hook postrouting priority srcnat; policy accept;
		oifname "pppoe-wan" jump srcnat_wan comment "!fw4: Handle wan IPv4/IPv6 srcnat traffic"
		oifname "ovpnclient1" jump srcnat_ovpnclient1 comment "!fw4: Handle ovpnclient1 IPv4/IPv6 srcnat traffic"
		oifname "wgclient1" jump srcnat_wgclient1 comment "!fw4: Handle wgclient1 IPv4/IPv6 srcnat traffic"
		oifname "wgclient3" jump srcnat_wgclient3 comment "!fw4: Handle wgclient3 IPv4/IPv6 srcnat traffic"
		oifname "wgclient2" jump srcnat_wgclient2 comment "!fw4: Handle wgclient2 IPv4/IPv6 srcnat traffic"
		jump upnp_postrouting comment "Hook into miniupnpd postrouting chain"
	}
}
```
All four VPN tunnels and native WAN postrouting jumps were correctly emitted without manual intervention.

---

### 4. Missed Items & Weekend Vulnerability Checks

#### 4.1 Killswitch Durability Test
* **Status:** **VERIFIED (Fails Closed)**
* **Evidence:**
  - `vpn-client` UCI configuration has `killswitch: true` on all 4 tunnels.
  - Policy routing tables (1001, 1002, 1003) contain static `blackhole default metric 254` routes underneath the device route:
    ```
    Table 1001:
      default dev wgclient1 proto static scope link 
      blackhole default proto static metric 254 
    ```
  - Priority 9910 rule `not from all fwmark 0/0xf000 blackhole` catches marked VPN traffic if a table drops.
  - Firewall `chain input_lan` contains leak protection:
    `udp dport 53 meta mark & 0x0000f000 != 0x00008000 drop comment "!fw4: lan_drop_leaked_dns"`
  - If `wgclient1` goes down, packets cannot route via WAN table 32766.

#### 4.2 DNS Leaks & Geo-DNS Resolver Colocation
* **Status:** **DEFECT IDENTIFIED (Geo-DNS Mismatch)**
* **Verification:**
  - Resolved locations of populated resolvers:
    - `162.252.172.57` -> **New York, US** (M247 / AS9009)
    - `149.154.159.92` -> **Frankfurt, Germany** (M247 / AS9009)
  - `/tmp/resolv.conf.d/resolv.conf.wgclient1` contains both IPs for the Swiss tunnel.
  - **Risk:** When `media-core` (CT 105) queries domains for Swiss IPTV feeds, the authoritative nameservers receive queries from New York or Frankfurt resolvers, returning non-Swiss CDN edge nodes and risking proxy/geoblocking detection.
  - **Note on Plaintext Leakage:** DNS queries are strictly encapsulated inside the WireGuard tunnel (routed to `dnsmasq` on port 2153 marked with `0x1000`), so queries do *not* leak onto native Deutsche Telekom WAN. The defect is purely **geographic resolver mismatch**.

#### 4.3 Weekend Recording Timers & Automation Health
* **Status:** **VERIFIED INTACT**
* **Active Timers in Jellyfin (`http://192.168.9.50:8096/LiveTv/Timers`):**
  1. **Bayern vs VfB Stuttgart (German feed):**
     - Timer ID: `b8d81cd897c317d55f66ece3e565ad20`
     - Channel: `Sky Sport Bundesliga 1 HD (720P)` (Ch 1001)
     - Time: `2026-08-28T17:00:00Z` → `2026-08-28T21:15:00Z`
     - Program ID: `b84f5c859144f14e08641610353ecde9`
     - Status: `New`
  2. **Cardinals @ Packers (Local Affiliate):**
     - Timer ID: `4866966b3e2dfceb6da89941f9ee2471`
     - Channel: `Green Bay: NBC 26 (WGBA)` (Ch 106)
     - Time: `2026-08-29T00:00:00Z` → `2026-08-29T03:00:00Z`
     - Program ID: `c71ed0faf19726f7f57dd4c4432581ec`
     - Status: `New`
* **Automation Log Error Clarification:**
  The `sports-dvr-auto` log line `Jellyfin API POST /emby/LiveTv/Timers failed` on Bayern is **benign**: it is a duplicate rejection because timer `b8d81cd8...` already exists in Jellyfin.
* **Power Management Override:**
  `/var/lib/dvr-dashboard/override-until` is set to `2026-09-01T06:00:00+02:00` (Tuesday morning), ensuring `dvr-clean-shutdown` will not shut down the server during the weekend fixtures.

---

### 5. MT6000 (`192.168.5.1` / `100.82.52.36`) Sanity Check

* **System Health:**
  - Uptime: 12 days, 17 hours · Load average: 0.06, 0.06, 0.08 · Free RAM: 630 MB / 985 MB.
* **Network & Wireless:**
  - Subnet `192.168.5.1/24` on `br-lan` active.
  - Wireless APs `ra0` (2.4GHz) and `rax0` (5GHz) broadcasting SSID `Big-GL` at 1147 Mbps and 2401 Mbps.
* **Active TV & Household Clients:**
  ```text
  192.168.5.183   f0:2f:9e:26:83:a1   LR-FireStick
  192.168.5.127   58:96:0a:f5:2a:1e   LGwebOSTV
  192.168.5.207   74:d6:37:a1:34:6d   amazon-c296dfd3b (FireTV)
  192.168.5.203   1c:53:f9:26:34:e9   DE-Chromecast
  192.168.5.133   68:5e:dd:5d:74:13   Nathans-Air
  192.168.5.101   5c:33:7b:f7:bb:09   Pixel-8-Pro
  192.168.5.143   b2:74:c0:7a:dc:1d   Erin-s-S25
  ```
  All TV corner streaming devices are active and receiving DHCP leases from the MT6000.
* **Hygiene Note:** Five stale pre-cutover DHCP reservations for Proxmox containers (`192.168.9.x`) still exist in MT6000 lease history (`/tmp/dhcp.leases`), but they are expired and non-interfering.

---

## Summary of Recommendations

1. **Fix Geo-DNS for Swiss Tunnel:** Explicitly assign Swiss DNS servers (or Swisscom / EDNS-capable Swiss IPs) to `wireguard.peer_1526.dns` so `reconcile_vpn_dns()` does not populate US/DE resolvers for Swiss-routed IPTV workloads.
2. **Optimize Firewall Reload in Boot Hook:** In `reconcile_vpn_firewall()`, replace synchronous `/etc/init.d/firewall restart` with `/sbin/fw4 reload` to avoid blocking hotplug for 11 seconds.
3. **Add Hotplug Event Filters:** Add `[ "$ACTION" = "ifup" ] || exit 0` at the start of `/etc/hotplug.d/iface/99-network-buildout-persist` to eliminate redundant script runs on interface unbinding/updates.
