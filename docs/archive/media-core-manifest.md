> **ARCHIVED** — kept for history; superseded by the consolidated [../../README.md](../../README.md). Links to other docs/ files may be dead.

> Original "Media-Core" systems manifest, imported verbatim 2026-07-04
> (architecture diagram image stripped). **Read [project-media-core.md](project-media-core.md)
> first** — it adapts this manifest to pve-01 and supersedes it where they
> differ (VM 103 instead of bare metal, Flint 2 instead of Brume 2, OpenVPN
> TCP instead of WireGuard for Tunnel B, no /dev/dri mapping initially).

# **SYSTEMS ENGINEERING MANIFEST: PROJECT "MEDIA-CORE"**

**Automated Zero-Subscription Backend DVR & Virtual VOD Server Stack**

**Target Infrastructure Base:** GL.iNet Brume 2 Gateway \+ Dedicated Local Micro-Computer Engine

## **1\. Executive Summary & Operational Goals**

The objective of Project **Media-Core** is to build a completely self-hosted, centralized media capture, proxy, and playback pipeline. The infrastructure is structurally hardened to maximize the efficiency of a **single-stream (1 Connection) IPTV subscription** without risking provider-side account locks or stream terminations.

### **Core Objectives:**

* **Decoupled Sports Capture:** Completely decouple recording executions from end-client viewing sticks (Chromecasts). Live football and basketball games are intercepted and written straight to disk by an always-on server backend.  
* **Zero Ongoing Software Cost:** Eliminate all subscription overheads (e.g., Channels DVR monthly fees) by deploying an open-source, community-audited stack.  
* **Virtual Cinema Integration:** Readily map the provider’s remote Video-On-Demand (VOD) movie links as native metadata-rich digital assets.  
* **Split-Tunnel Geographic Privacy:** Securely pass media traffic through high-throughput WireGuard relays while preserving unencrypted gigabit internal network pipelines.

## 

## 

## 

## 

## 

## 

## 

## 

## 

## **2\. Definitive Network Architecture**

The gateway environment operates under a native **192.168.9.1** Class C private subnet. Outbound WAN routing is governed strictly by client-device identity mapping on the GL.iNet hardware layer.

### **System Routing Model**

                          (architecture diagram omitted — see project-media-core.md for the adapted version)

### **Subnet Allocations & Node Parameters:**

* **Home Gateway:** 192.168.9.1  
* **Micro-Computer Server (Headless Node):** 192.168.9.50 (Enforced via Static DHCP Lease)  
* **Primary Chromecast Node:** Dynamic DHCP Allotment (e.g., 192.168.9.15)  
* **LAN Switching:** Unencrypted Layer 2 transport. If a VPN tunnel encounters a total connection fault, local device-to-device streaming remains alive via the internal subnet loops.

### **Policy-Based WAN Trajectory Settings:**

* **All Local Clients / Chromecasts:** Routed explicitly out through **Tunnel A (USA)**. This ensures native residential verification for standard subscription clients (Netflix, Prime Video, YouTube) and completely avoids proxy blacklists.  
* **Micro-Computer Server (192.168.9.50):** Routed explicitly out through **Tunnel B (Switzerland)**. This isolates all IPTV playlist retrievals, electronic program guide hooks, and active media stream pipes behind a privacy-centric offshore network infrastructure.

## **3\. Server Hardware Specification Specs**

* **Primary Core Node:** X86-64 Micro-Computer (Intel-based architecture with integrated HD Graphics / Iris Xe supporting Quick Sync Video).  
* **Operating System Base:** Linux Ubuntu Server LTS or standard Windows 10/11 x64 execution layer.  
* **Memory Footprint:** Minimum 8GB RAM to handle massive metadata index caches during deep VOD scrapes.  
* **Storage Target:** Minimum 1TB Portable External SSD or externally powered mechanical desktop drive connected via a native high-speed **USB 3.0 blue port** (format file allocation system as **NTFS** or **EXT4**; do *not* use FAT32 due to its structural 4GB individual file size limitation, which will prematurely truncate live sports recordings).

## **4\. Automated Deployment: Infrastructure-as-Code**

The entire software pipeline is encapsulated inside a single, declarative Docker Compose blueprint. This guarantees uniform folder mounts, isolated environments, and direct hardware pathing for video processing.

### **The Complete Docker Manifest (docker-compose.yml)**

YAML  
services:  
  jellyfin:  
    image: jellyfin/jellyfin:latest  
    container\_name: jellyfin  
    restart: unless-stopped  
    network\_mode: host \# Ensures raw, zero-overhead HDHomeRun discovery on the 192.168.9.x subnet  
    environment:  
      \- PUID=1000  
      \- PGID=1000  
      \- TZ=Europe/Berlin  
    volumes:  
      \- ./jellyfin/config:/config  
      \- ./jellyfin/cache:/cache  
      \- ./media/movies:/media/movies:ro \# Reads the .strm shortcuts m3u2strm generates  
      \- ./media/recordings:/media/recordings:rw \# Active landing zone for sports DVR files  
    devices:  
      \# Maps the integrated Intel hardware acceleration graphics card nodes directly for video rendering  
      \- /dev/dri/renderD128:/dev/dri/renderD128  
      \- /dev/dri/card0:/dev/dri/card0

  threadfin:  
    image: freetv/threadfin:latest  
    container\_name: threadfin  
    restart: unless-stopped  
    ports:  
      \- "34400:34400"  
    environment:  
      \- PUID=1000  
      \- PGID=1000  
      \- TZ=Europe/Berlin  
    volumes:  
      \- ./threadfin/conf:/home/threadfin/.threadfin  
      \- /tmp/threadfin:/tmp/threadfin

  m3u2strm:  
    image: jacobsnyder/m3u2strm:latest  
    container\_name: m3u2strm  
    restart: unless-stopped  
    environment:  
      \- PUID=1000  
      \- PGID=1000  
      \- M3U\_URL=YOUR\_ACTUAL\_IPTV\_M3U\_LINK\_HERE \# Paste raw provider playlist string here  
      \- SYNC\_INTERVAL=86400 \# Executes on a hard-coded 24-hour cycle to re-verify cinema links  
      \- OUTPUT\_DIR=/output  
    volumes:  
      \- ./media/movies:/output

## **5\. Granular Software Configuration Handshakes**

To ensure the 1-stream restriction is never violated, config steps must be followed precisely in this operational order:

### **1\. The Threadfin Shield Configuration**

* Access the administrative interface via a browser on the network: \[http://192.168.9.50:34400/web/\](http://192.168.9.50:34400/web/)  
* **Input Streams:** Paste your unique IPTV Provider **M3U Playlist URL** and **XMLTV EPG URL**.  
* **Enforce Safety Limit:** Navigate to Settings. Locate the **Simultaneous Streams** field and configure it strictly to **1**. This is a hard-coded software brake. If an accidental secondary request occurs locally, Threadfin drops it internally before it can travel through the Brume 2 gateway and alert the provider.  
* **Channel Optimization:** Navigate to the Filter interface. Select only the necessary sports networks (e.g., local broadcast channels, specific regional sports bundles). Keep the total channel payload under **500 items** to protect system memory.

### **2\. The Jellyfin Integration Pipeline**

* Access the initialization screen via: \[http://192.168.9.50:8096\](http://192.168.9.50:8096)  
* **Map the Live Tuner:** Go to Dashboard $\\rightarrow$ Live TV. Click **Tuner Devices (+)** and select **HDHomeRun**. Input the internal address: http://localhost:34400/tuner/threadfin.  
* **Map the Guide:** Click **TV Guide Data Providers (+)**, choose **XMLTV**, and paste your provider's EPG Link URL.  
* **Mount the Cinema Library:** Go to Dashboard $\\rightarrow$ Libraries $\\rightarrow$ Add Media Library. Set the Content Type to **Movies**, name the section "IPTV Cinema", and target the directory path /media/movies. Jellyfin will read the small virtual .strm text shortcuts generated by the script, fetch the cinematic artwork from open databases, and display a rich Netflix-style dashboard.

### **3\. Client Interface Setup (Chromecast)**

* Install the **Jellyfin** client application on the Chromecast.  
* Bypass automatic discovery protocols by selecting **Add Server Manually**.  
* Input the exact local path target: **\[http://192.168.9.50:8096\](http://192.168.9.50:8096)**.

## **6\. Critical Operational Protocols for 1-Stream Packages**

Because the server stack is limited to a single connection channel, Finley must know these system behaviors:

* **The Shared Stream Advantage:** If Jellyfin is recording a live basketball game to disk, it occupies your single stream. If you open your Chromecast to watch that *same* game while the recording is active, Jellyfin splits the incoming data stream on the local server level. It passes the cached data to your TV screen over the LAN while continuing to write the file to the drive. The provider sees only **one** stream request.  
* **The Channel-Flipping Hazard:** If Jellyfin is actively saving a game on Channel A, and you open your Chromecast and attempt to view a *different* channel (Channel B) live via Jellyfin, the server will block your request to protect the ongoing recording from failure.  
* **The TiviMate Hybrid Strategy:** For casual, live channel-surfing when no active background recording is scheduled, use **TiviMate Premium** directly on the Chromecast. Because TiviMate handles network handshakes efficiently, you get rapid 1-second channel switches. Just make sure to shut down TiviMate before a scheduled background recording begins on the server to prevent multi-stream account locks.

