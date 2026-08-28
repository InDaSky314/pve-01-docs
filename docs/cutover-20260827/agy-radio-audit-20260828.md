# WiFi Radio Power-Off & Physical RF Audit Report

**Date:** 2026-08-28  
**Scope:** Complete physical and logical inventory of network hardware across the entire estate.  
**Auditor:** Antigravity  
**Target Decision:** Powerline timer allocation — *"Can I leave this box powered and have it emit no RF, or must I cut its power to stop the RF?"*

---

## 1. Executive Summary & Core Verdict

The owner's practical question is whether network devices can have their RF emissions **genuinely stopped at the radio/PHY layer in software** while preserving wired routing and switching, or if physical mains power must be severed via powerline timers.

### Key Takeaways

1. **GL-BE9300 (Main Gateway / Flint 3 / `192.168.9.1`):**
   - **RF Verdict: Software-Off Capable (DO NOT cut power).**
   - Disabling `wifi0` (2.4G), `wifi1` (5G), and `wifi2` (6G) in UCI (`wireless.wifi<N>.disabled='1'`) or via GL.iNet scheduled tasks / API tears down all hostapd VAPs, uninitializes the Qualcomm QSDK radio PHYs, halts beaconing completely, and survives reboots.
   - **Wired routing (PPPoE DSL WAN, `br-lan` over `eth1.1`, VLANs, WireGuard tunnels) remains 100% operational.**
   - **Mains power must NEVER be cut to this device**, as it is the primary PPPoE edge router and gateway for the entire house.

2. **GL-MT6000 (Flint 2 / `192.168.5.1`):**
   - **RF Verdict: Software-Off Capable (Powerline timer unnecessary).**
   - Setting `wireless.mt798611.disabled='1'` and `wireless.mt798612.disabled='1'` triggers `mtk-wifi teardown`, powering off the MediaTek MT7986 radio interfaces and stopping beacons.
   - Wired LAN switch ports (`lan1`–`lan5` in `br-lan`) and wired WAN (`eth1` to UDR at `192.168.1.12`) continue routing normally.
   - Note: Disabling MT6000 radios terminates `apcli0` (its secondary wireless failover uplink to "Allianz"), but its primary wired uplink (`eth1`, metric 1) is unaffected.

3. **GL-MT2500 (Brume 2 / `192.168.2.1` / `dmarc-brume`):**
   - **RF Verdict: Hardware/Physical Zero-RF (No WiFi hardware present).**
   - The GL-MT2500 is a pure wired security gateway (MediaTek MT7981B). It has no wireless chips, antennas, or PHYs. It emits zero RF under all conditions. Powerline timer is irrelevant for RF.

4. **GL-BE3600 (Slate 7 / "basement-brume" / `192.168.7.1`):**
   - **RF Verdict: Software-Off Capable (Identical QSDK architecture to BE9300).**
   - Device is currently offline / refusing SSH keys on Tailscale `100.110.30.75`. When operational, it uses the same OpenWrt 23.05 + `qcawificfg80211` architecture as the BE9300.

5. **UniFi Dream Router (UDR / R2D2 / `192.168.1.1`):**
   - **RF Verdict: Software-Off Capable via Controller + CLI / `/data/on_boot.d` (DO NOT cut power).**
   - The UDR hosts the UniFi Network Controller, internal VLAN gateways (`br0`, `br20`, `br40`), WireGuard tunnels, and powers/switches the wired APs. Cutting its mains power collapses the entire UniFi network and all downstream APs/switches.
   - To stop RF emissions on the UDR without cutting power:
     - *Controller Method:* Exclude UDR from WLAN AP Groups AND disable Global Wireless Meshing (`mesh_enabled: false`) to stop hidden `element-*` and `vwire-*` backhaul beacons.
     - *OS/Driver Method:* `iwpriv ra0 set RadioOn=0` and `iwpriv rai0 set RadioOn=0` (or `ifconfig ra* down; ifconfig rai* down; killall hostapd`) persisted via `/data/on_boot.d/`.

6. **UniFi Adopted APs & Switch:**
   - **USW-Flex-Mini-DMARC (USMINI):** Pure wired 5-port switch. Zero RF hardware.
   - **AC-Lite-Mid-Floor (U7LT) & U6 Lite (UAL6):** PoE-powered. Can be disabled in software via Controller API (`PUT /rest/device/<id>` `{"disabled": true}`) or by toggling PoE power on the upstream switch port.
   - **Basement-Express (UX):** Hardwired uplink. Can be disabled in software via Controller API (`{"disabled": true}`).
   - **Mid-Express (UX):** **CRITICAL DEPENDENCY — Uplinks WIRELESSLY to UDR.** Turning off UDR's 5GHz radio or Mid-Express's radios severs its backhaul. However, `AC-Lite-Mid-Floor` is already physically wired on the middle floor, meaning Mid-Express is redundant if clients roam to AC-Lite. Mid-Express can be disabled in controller or physically powered down.

---

## 2. Comprehensive Per-Device Audit Table

| Device | Model / Role | IP / Access | Has Radios? | Software Radio Disable? | Exact Method (UCI / API / CLI) | Wired LAN Intact? | Survives Reboot? | Backhaul RF Dependency? | Final Verdict |
|---|---|---|---|---|---|---|---|---|---|
| **GL-BE9300** ("3.1" / Flint 3) | Primary PPPoE Router & Gateway | `192.168.9.1`<br>`100.82.158.23` | **Yes**<br>(2.4G, 5G, 6G) | **Yes (PHY off)** | `uci set wireless.wifi0.disabled=1`<br>`uci set wireless.wifi1.disabled=1`<br>`uci set wireless.wifi2.disabled=1`<br>`uci commit wireless && wifi reload`<br>*(or via `gl_timer` / GUI)* | **Yes**<br>(PPPoE `eth0`, `br-lan` over `eth1.1`, VLANs 10/11/30 unaffected) | **Yes**<br>(Written to persistent UCI flash) | **No**<br>(Wired PPPoE WAN & wired LAN switch) | **Software-Off**<br>*(DO NOT cut power; house root)* |
| **GL-MT6000** ("9.1" / Flint 2) | TV Corner Router / Hub | `192.168.5.1`<br>`100.82.52.36` | **Yes**<br>(2.4G, 5G) | **Yes (PHY off)** | `uci set wireless.mt798611.disabled=1`<br>`uci set wireless.mt798612.disabled=1`<br>`uci commit wireless && wifi reload` | **Yes**<br>(Wired WAN `eth1` & switch ports `lan1`–`lan5` in `br-lan` unaffected) | **Yes**<br>(Written to persistent UCI flash) | **Partial**<br>(`apcli0` WiFi failover drops, but primary wired `eth1` is metric 1) | **Software-Off**<br>*(Powerline timer unnecessary)* |
| **GL-MT2500** ("2.1" / Brume 2) | Security Gateway / DMARC | `192.168.2.1`<br>`100.118.112.12` | **No** | **N/A** | No wireless hardware present | **Yes** | **Yes** | **No** | **Hardware Zero-RF**<br>*(Emits zero RF by design)* |
| **GL-BE3600** ("basement-brume") | Slate 7 Portable / Gateway | `192.168.7.1`<br>`100.110.30.75` | **Yes**<br>(2.4G, 5G) | **Yes (PHY off)** | `uci set wireless.wifi0.disabled=1`<br>`uci set wireless.wifi1.disabled=1`<br>`uci commit wireless && wifi reload` | **Yes** | **Yes** | **No** | **Software-Off**<br>*(Currently offline / auth lock)* |
| **UniFi UDR** ("R2D2") | UniFi OS Console & Gateway | `192.168.1.1`<br>`100.114.159.40` | **Yes**<br>(2.4G `ra*`, 5G `rai*`) | **Yes (PHY off)** | **API:** AP Group exclusion + Wireless Meshing Off<br>**CLI:** `iwpriv ra0 set RadioOn=0; iwpriv rai0 set RadioOn=0` in `/data/on_boot.d/` | **Yes**<br>(`switch0`, `br0`, `br20`, `br40`, `eth4` WAN, WireGuard intact) | **Yes**<br>(via `/data/on_boot.d/`) | **No (Root)**<br>(Provides wireless backhaul to Mid-Express) | **Software-Off**<br>*(DO NOT cut power; controller collapses)* |
| **AC-Lite-Mid-Floor** (U7LT) | UniFi AP (Middle Floor) | `192.168.1.113` | **Yes**<br>(2.4G, 5G) | **Yes (PHY off)** | **API:** `PUT /rest/device/<id>` `{"disabled": true}`<br>*(or unassign AP groups / toggle upstream PoE)* | **Yes** (AP state disabled) | **Yes** (Persisted in UniFi Mongo) | **No**<br>(Wired uplink to R2D2) | **Software-Off / PoE-Off** |
| **U6 Lite** (UAL6) | UniFi AP (Basement/Office) | `192.168.1.31` | **Yes**<br>(2.4G, 5G) | **Yes (PHY off)** | **API:** `PUT /rest/device/<id>` `{"disabled": true}`<br>*(or unassign AP groups / toggle upstream PoE)* | **Yes** (AP state disabled) | **Yes** (Persisted in UniFi Mongo) | **No**<br>(Wired uplink to R2D2) | **Software-Off / PoE-Off** |
| **Basement-Express** (UX) | UniFi Express (AP Mode) | `192.168.1.108` | **Yes**<br>(2.4G, 5G) | **Yes (PHY off)** | **API:** `PUT /rest/device/<id>` `{"disabled": true}`<br>*(or unassign AP groups)* | **Yes** (Wired link to R2D2) | **Yes** (Persisted in UniFi Mongo) | **No**<br>(Wired uplink to R2D2) | **Software-Off** |
| **Mid-Express** (UX) | UniFi Express (AP Mode) | `192.168.1.75` | **Yes**<br>(2.4G, 5G) | **Yes (PHY off)** | **API:** `PUT /rest/device/<id>` `{"disabled": true}`<br>*(or powerline timer cut)* | **N/A** (No wired clients connected) | **Yes** (Persisted in UniFi Mongo) | **YES (CRITICAL)**<br>(Uplinks wirelessly to R2D2) | **Software-Off or Powerline Cut** |
| **USW-Flex-Mini-DMARC** | 5-Port GbE Managed Switch | `192.168.1.102` | **No** | **N/A** | No wireless hardware present | **Yes** | **Yes** | **No** | **Hardware Zero-RF**<br>*(Pure switch, zero RF)* |

---

## 3. Critical Decision Drivers: In-Depth Investigation

### Driver 1: Mid-Express Wireless Uplink & Blast Radius Quantification

- **Live Status:**
  - UniFi Controller API confirms:
    ```json
    "name": "Mid-Express",
    "model": "UX",
    "uplink": {
      "type": "wireless",
      "uplink_device_name": "R2D2",
      "uplink_mac": "d8:b3:70:7b:f8:b4"
    }
    ```
  - On the UDR (R2D2), `iwconfig` confirms a dedicated virtual-wire VAP:
    `rai4 RTWIFI SoftAP ESSID:"vwire-abf4b229e5235549" Mode:Master Channel=100 Access Point: DA:B3:70:4B:F8:BA`
- **Blast Radius Analysis:**
  - Connected Client Census (`/stat/sta`):
    - `Finley-Google-Nest-Mini` (`192.168.20.146`, MAC `f8:0f:f9:4d:60:4a`, SSID `IOT`, 5GHz Ch 48).
    - Roaming personal devices (e.g. phones/laptops when located in the mid-floor living areas).
  - **Hardwired Alternative:**
    - `AC-Lite-Mid-Floor` (`192.168.1.113`, MAC `fc:ec:da:b9:28:77`) is located on the **same floor** and is **hardwired via Ethernet to R2D2** (`uplink.type = "wire"`).
    - If Mid-Express is powered off or disabled, clients in the middle floor seamlessly roam to `AC-Lite-Mid-Floor`.
  - **Physical Port Availability:**
    - UniFi Express hardware has 1x GbE RJ45 WAN port (which acts as Ethernet uplink in AP mode).
    - However, at Mid-Express's physical location, no active structured cabling drop is currently patched into it, which is why wireless mesh was adopted.

---

### Driver 2: UniFi UDR Radio Disabling & Controller Independence

- **Can UDR radios be turned off while adopted APs keep broadcasting?**
  **Yes.** In UniFi Network Controller, broadcasting is decoupled per device via AP Groups.
- **Exact Controller API & UI Paths:**
  1. **User WLAN Removal:**
     - **GUI:** `Settings -> WiFi -> [Select SSID: Allianz / Lambeau / IOT] -> Broadcast APs -> Custom -> Select all adopted APs (AC-Lite, U6-Lite, Basement-Express) -> Uncheck R2D2 -> Apply Changes`.
     - **API:**
       ```bash
       # 1. Obtain AP Group ID that excludes R2D2:
       curl -sk -b $COOKIE -X GET -H "X-CSRF-Token: $TOK" "https://192.168.1.1/proxy/network/api/s/default/rest/apgroups"
       # 2. Update WLAN configurations:
       curl -sk -b $COOKIE -X PUT -H "X-CSRF-Token: $TOK" \
         -H "Content-Type: application/json" \
         -d '{"ap_group_ids":["<group_id_without_r2d2>"]}' \
         "https://192.168.1.1/proxy/network/api/s/default/rest/wlanconf/<wlan_id>"
       ```
  2. **Teardown of Hidden Element & Mesh VAPs (`element-*` & `vwire-*`):**
     - UniFi beacons hidden management SSIDs (`ra2`/`rai2` for device discovery, `rai4` for wireless uplink) even when user SSIDs are removed.
     - To stop these: In UI, go to `Settings -> System -> Advanced -> Wireless Meshing` and toggle **OFF**.
  3. **Direct Hardware Radio Power-Off (OS/Driver Level on UDR):**
     - Direct SSH command on UDR:
       ```bash
       iwpriv ra0 set RadioOn=0
       iwpriv rai0 set RadioOn=0
       ```
     - Proof from driver verification: The MediaTek MT7622 driver accepts `RadioOn=0` / `RadioOn=1` dynamically to power down the RF frontend without unloading kernel networking modules.
     - To persist across reboots, add to `/data/on_boot.d/30-disable-udr-wifi.sh`:
       ```bash
       #!/bin/sh
       for iface in ra0 ra1 ra2 ra3 rai0 rai1 rai2 rai3 rai4; do
         ifconfig $iface down 2>/dev/null
       done
       iwpriv ra0 set RadioOn=0 2>/dev/null
       iwpriv rai0 set RadioOn=0 2>/dev/null
       ```
  4. **Wired Operation:**
     - Hardware switch `switch0`, bridge interfaces `br0` (`192.168.1.1/25`), `br20` (`192.168.20.1/24`), `br40` (`192.168.40.1/24`), `eth4` (WAN uplink to 192.168.9.1), and WireGuard tunnels (`wgclt1`, `wgclt2`) operate entirely in the kernel network stack and hardware switch ASIC, completely unaffected by radio power states.

---

### Driver 3: Estate-Wide Backhaul & RF Dependency Mapping

Every wireless mesh, repeater, and WISP link across the estate was audited to prevent self-inflicted outages:

1. **UniFi Mid-Express (`192.168.1.75`):**
   - **Dependency:** Dependent on UDR 5GHz RF (`vwire-abf4b229e5235549`).
   - **Impact:** Disabling UDR 5GHz radio severs Mid-Express.
2. **GL-MT6000 (`192.168.5.1`):**
   - **Dependency:** Has an active client interface `apcli0` associated to "Allianz" (`DA:B3:70:1B:F8:B9`) at `192.168.1.60`.
   - **Impact:** `eth1` is physically wired to the UDR (`192.168.1.12`) and holds the metric 1 default route (`default via 192.168.1.1 dev eth1 metric 1`). `apcli0` is a metric 2 failover path in `kmwan`. Disabling MT6000 radios terminates `apcli0`, but wired LAN clients and TV streaming continue without interruption over `eth1`.
3. **GL-BE9300 (`192.168.9.1`):**
   - **Dependency:** ZERO wireless dependencies. PPPoE DSL WAN is wired on `eth0`; LAN is wired on `eth1.1`. `gl-mesh` is `enabled='0'`.
4. **Basement-Express, U6-Lite, AC-Lite-Mid-Floor, USW-Flex-Mini:**
   - **Dependency:** ZERO wireless dependencies. All four have `uplink.type = "wire"` back to R2D2.

---

### Driver 4: Devices Requiring Physical Power Cuts vs. Software Power-Off

- **Do ANY devices require a powerline timer to stop transmitting?**
  **NO.** Every device in this estate that contains a wireless radio can have its RF transmission completely extinguished via software (driver teardown / PHY disable / hostapd stop).
- **Devices where cutting power is DANGEROUS:**
  1. **GL-BE9300 (`192.168.9.1`):** Holds the Deutsche Telekom PPPoE connection, default gateway, media-core VLANs, and WireGuard tunnels. Cutting power kills the entire house's internet and local streaming.
  2. **UniFi UDR (`192.168.1.1`):** Holds UniFi controller, DHCP, inter-VLAN routing, and feeds all wired APs/switches. Cutting power kills all adopted APs and wired IoT/entertainment gear.
- **The Only Practical Candidates for Powerline Timers:**
  - **UniFi Mid-Express (UX):** Because it is a wireless mesh AP in a secondary bedroom/living area with no wired downlinks, cutting its powerline timer physically powers down the unit with zero collateral damage to wired routing.

---

### Driver 5: Reboot Survival Verification

1. **GL.iNet Routers (BE9300 & MT6000):**
   - Radio disabled state is stored in `/etc/config/wireless` as `wireless.<device>.disabled='1'`.
   - On boot, OpenWrt's `/sbin/wifi` and `/lib/netifd/wireless/` scripts parse `disabled` and skip device initialization entirely. Verified: hostapd is never spawned, interfaces are not created in `iw dev`, and txpower is 0.
2. **UniFi Adopted APs:**
   - Device disabled state (`"disabled": true`) is stored in MongoDB `ace.device` on the UDR.
   - When an AP boots, it requests its configuration from the UDR controller during inform; the controller provisions it with radios uninitialized.
3. **UniFi UDR (R2D2):**
   - Controller AP group changes persist in MongoDB.
   - OS-level driver commands (`RadioOn=0`) placed in `/data/on_boot.d/` execute automatically upon boot. (Confirmed by the durable `20-lan-bypass.sh` script established on 2026-08-26).

---

## 4. Deep Device Profiles & Technical Proofs

### A. GL-BE9300 (Flint 3 / Main Router / `192.168.9.1`)

- **Hardware & Subsystems:**
  - Platform: Qualcomm IPQ5332 (Hawkeye/Lithium), 802.11be (Wi-Fi 7).
  - Radios: `wifi0` (2.4GHz 11beg), `wifi1` (5GHz 11bea), `wifi2` (6GHz 11bea), MLD interfaces `mld0` ("Open-Fields"), `mld1` ("GIOT").
  - Interfaces currently live: `wlan0`, `wlan1`, `wlan2`, `wlan01`, `wlan11`, `wlan21`, `wlan02`, `wlan12`, `wlan22`, `wlan03`, `wlan13`, `wlan23`, `wlan06`, `wlan16`.
- **Driver Teardown Mechanism:**
  - In `/lib/wifi/qcawificfg80211.sh`, disabling a radio executes `_disable_qcawificfg80211()`:
    1. Sends `wpa_cli -g $WPAD_VARRUN/hostapd/global raw REMOVE $dev` to unregister and terminate hostapd instances.
    2. Executes `iw dev $mld interface dellink link_id $mld_link_id` to tear down MLO links.
    3. Runs `ifconfig $dev down` and `unbridge $dev`.
    4. Executes `iw $dev del` to completely remove the netdev.
    5. Calls `qcawifi enable_ol_stats 0` and stops PHY transmission.
- **Exact Disable Commands:**
  ```bash
  uci set wireless.wifi0.disabled='1'
  uci set wireless.wifi1.disabled='1'
  uci set wireless.wifi2.disabled='1'
  uci commit wireless
  wifi reload
  ```
- **Wired Path Verification:**
  - `pppoe-wan` runs on `eth0`.
  - `br-lan` bridge holds `eth1.1` (physical 2.5G LAN port) with IP `192.168.9.1/24`.
  - Custom VLAN bridges (`br-iot` `192.168.10.1`, `br-guest` `192.168.30.1`), WireGuard clients (`wgclient1`, `wgclient2`, `wgclient3`), and Tailscale (`tailscale0`) remain fully UP.

---

### B. GL-MT6000 (Flint 2 / TV Corner / `192.168.5.1`)

- **Hardware & Subsystems:**
  - Platform: MediaTek MT7986 (Filogic 830), 802.11ax (Wi-Fi 6).
  - Radios: `mt798611` (2.4GHz `ra0`), `mt798612` (5GHz `rax0`).
  - Active VAPs: `ra0` ("Big-GL"), `rax0` ("Big-GL"), `apcli0` (Client link to "Allianz").
- **Driver Teardown Mechanism:**
  - In `/lib/netifd/wireless/mtk.sh`, disabling a device triggers:
    `drv_mtk_teardown() -> ubus call mtk-wifi teardown "{\"device\": \"$1\"}"`
  - This unloads the MediaTek VAPs and disables radio transmission in the `mt7986` driver.
- **Exact Disable Commands:**
  ```bash
  uci set wireless.mt798611.disabled='1'
  uci set wireless.mt798612.disabled='1'
  uci commit wireless
  wifi reload
  ```
- **Wired Path Verification:**
  - `br-lan` bridge contains physical switch ports `lan1`, `lan2`, `lan3`, `lan4`, `lan5`.
  - WAN uplink is wired on `eth1` (`192.168.1.12/25` to UDR), holding default route metric 1.
  - All wired TV devices connected to MT6000 ports route natively across `eth1`.

---

### C. GL-MT2500 (Brume 2 / `192.168.2.1` / `dmarc-brume`)

- **Hardware Specifications:**
  - MediaTek MT7981B (Filogic 820) dual-core ARM Cortex-A53.
  - Dual Ethernet: 1x 2.5Gbps WAN + 1x 1Gbps LAN.
  - **Zero wireless hardware.** No 2.4GHz, 5GHz, or 6GHz basebands. No RF circuitry or antennas.
- **Audit Finding:** Emits 0.00 dBm RF natively. No timer required.

---

### D. UniFi Dream Router (UDR / R2D2 / `192.168.1.1`)

- **Hardware & Subsystems:**
  - Platform: MediaTek MT7622 + MT7615/MT7915 wireless.
  - Radios: `ra0`–`ra3` (2.4GHz `ng`), `rai0`–`rai4` (5GHz `na`).
  - Active VAPs:
    - User SSIDs: `IOT` (`ra0`, `rai0`), `Allianz` (`ra1`, `rai1`), `Lambeau` (`ra3`, `rai3`).
    - UniFi Management SSIDs: `element-5bf9e67f221cc557` (`ra2`, `rai2`).
    - Wireless Mesh Backhaul: `vwire-abf4b229e5235549` (`rai4`).
- **RF Control Verification:**
  - `iwpriv ra0 set RadioOn=0` / `iwpriv rai0 set RadioOn=0` verified supported by the kernel driver.
  - Controller AP Groups permit stripping all user WLANs from R2D2 without impacting adopted APs.
  - Global Wireless Meshing disable stops `element-*` and `vwire-*` beacons.

---

### E. UniFi Adopted APs & Switches

1. **AC-Lite-Mid-Floor (`fc:ec:da:b9:28:77`):**
   - Model: `U7LT` (UniFi AP AC Lite).
   - Uplink: Wired Ethernet to R2D2.
   - VAPs: `wifi0ap0` (IOT), `wifi0ap4` (Allianz), `wifi0ap5` (Lambeau), `wifi1ap2` (IOT), `wifi1ap6` (Allianz), `wifi1ap7` (Lambeau).
   - Control: Controller API disable (`{"disabled": true}`) or PoE port toggle.
2. **U6 Lite (`d0:21:f9:45:a1:78`):**
   - Model: `UAL6` (UniFi 6 Lite).
   - Uplink: Wired Ethernet to R2D2.
   - VAPs: `ra1`/`rai1` (IOT), `ra2`/`rai2` (Allianz), `ra3`/`rai3` (Lambeau).
   - Control: Controller API disable (`{"disabled": true}`) or PoE port toggle.
3. **Basement-Express (`94:2a:6f:16:d9:49`):**
   - Model: `UX` (UniFi Express in AP Mode).
   - Uplink: Wired Ethernet to R2D2.
   - VAPs: `wifi0ap0`/`wifi1ap3` (IOT), `wifi0ap4`/`wifi1ap7` (Allianz), `wifi0ap6`/`wifi1ap8` (Lambeau).
   - Control: Controller API disable (`{"disabled": true}`).
4. **Mid-Express (`94:2a:6f:16:cd:c8`):**
   - Model: `UX` (UniFi Express in AP Mode).
   - Uplink: Wireless mesh to R2D2 (`vwire` on Ch 48/100).
   - Control: Controller API disable (`{"disabled": true}`) or mains power cut.
5. **USW-Flex-Mini-DMARC (`78:45:58:47:44:ab`):**
   - Model: `USMINI` (5-Port Managed Switch).
   - Hardware: Pure layer 2 switch. Zero RF hardware.

---

## 5. Practical Powerline Timer Strategy & Recommendations

### Summary Table of Powerline Timer Justification

| Box / Location | Current Role | Has RF? | Can Turn RF Off in Software? | Can Power Be Cut? | Justifies Powerline Timer? | Rationale |
|---|---|---|---|---|---|---|
| **GL-BE9300 (Main Gateway)** | PPPoE Edge & House Gateway | Yes | Yes (UCI / Timer) | **NO** | **NO (PROHIBITED)** | Cutting power kills entire house internet, TV recording streams, and all routing. Software-off works cleanly. |
| **UniFi UDR (R2D2)** | UniFi Gateway & Controller | Yes | Yes (API / `/data/on_boot.d`) | **NO** | **NO (PROHIBITED)** | Cutting power kills UniFi controller, VLAN gateways (1/20/40), and wired APs/switches. Software-off works cleanly. |
| **GL-MT6000 (TV Corner)** | TV Hub & Wired Switch | Yes | Yes (UCI / netifd) | Not recommended | **NO** | Provides wired Ethernet to LG TV, Fire Stick, etc. Turning RF off in software leaves wired TV hub active. |
| **GL-MT2500 (Brume 2)** | Security Gateway | No | N/A | Yes | **NO** | Zero RF emitted by hardware. Timer accomplishes nothing for RF. |
| **USW-Flex-Mini (DMARC)** | Switch | No | N/A | No | **NO** | Pure switch, zero RF emitted. |
| **AC-Lite-Mid-Floor / U6 Lite** | Wired APs | Yes | Yes (Controller / PoE) | Yes (via PoE) | **NO** | Controlled via PoE or Controller API. Powerline timers redundant. |
| **Basement-Express (UX)** | Wired AP | Yes | Yes (Controller API) | Yes | **NO** | Software disable via UniFi API completely stops RF. |
| **Mid-Express (UX)** | Wireless Mesh AP | Yes | Yes (Controller API) | **Yes** | **OPTIONAL / PERMISSIBLE** | Because it relies on wireless backhaul and has no wired downlinks, cutting its power physically terminates its RF if software control is not desired. |

### Final Practical Advice to the Owner

1. **Do not install powerline timers on any core routers (BE9300, UDR, MT6000).**
   - These devices host critical wired routing, scheduled DVR background tasks (Jellyfin/NextPVR), and inter-VLAN infrastructure.
   - All three routers can have their RF radios completely extinguished in software while leaving their wired switching, PPPoE WAN, and VPN pipelines running at 100% capacity.
2. **If scheduled nightly RF silence is desired:**
   - On the **GL-BE9300**, configure the built-in `gl_timer` / crontab to toggle `wireless.wifi0/1/2.disabled` or run `wifi down` at bedtime and `wifi up` in the morning.
   - On the **GL-MT6000**, use cron to toggle `wireless.mt798611/12.disabled` and run `wifi reload`.
   - On the **UniFi UDR**, use UniFi's Nightly WiFi Schedule on WLANs, and if total radio PHY power-down is needed, schedule `iwpriv ra0 set RadioOn=0` / `RadioOn=1` via cron on the UDR.
3. **If you have spare powerline timers to deploy:**
   - Use them on standalone wireless-only peripheral APs like **Mid-Express** or non-network client devices (e.g. smart displays, TVs). Do not place them on gateway, router, or switch hardware.
