# IPTV Provider Outage & Root-Cause Analysis: Per-IP Streaming Block (2026-09-03)

## Quick Summary / TL;DR

* **Symptom:** Every live stream returned `HTTP 511 Network Authentication Required` from CT 105, but `player_api.php` authenticated successfully (`auth=1 status=Active`).
* **Root Cause:** The provider's streaming edge blocked the specific Surfshark VPN exit IP (`89.37.173.42`). The API sits behind Cloudflare and allowed the IP, but stream transcoders rejected it.
* **Diagnosis (5-minute check):** If `player_api.php` succeeds with `auth=1` from CT 105 while stream `.ts` requests fail with `HTTP 511`, the exit IP is blocked, not the account.
* **Fix:** Bounce WireGuard tunnel 2430 (`wgclient1`) on the GL.iNet router (`192.168.9.1`) to obtain a fresh Zurich exit IP (`156.146.62.50`). Streams recovered immediately (`HTTP 200`).
* **Recurrence:** This **will recur** periodically because Surfshark regularly rotates IP allocations and provider blocklists evolve.

---

## Timeline of the Incident

* **2026-09-02 22:12 CEST:** Last recorded successful live stream through CT 105.
* **2026-09-03 03:48 CEST:** Brewers @ Cubs live recording died midway due to provider returning `HTTP 502 Bad Gateway`.
* **2026-09-03 03:48 - 10:30 CEST:** Total streaming outage. Every stream request failed from CT 105 with `HTTP 511`.
* **2026-09-03 10:30 - 11:15 CEST:** Systematic diagnostic isolation across network paths, DNS, credentials, and tunnel endpoints.
* **2026-09-03 11:20 CEST:** Tunnel 2430 bounced on the GL.iNet router. New IP `156.146.62.50` acquired; streams immediately returned `HTTP 200` with full media delivery.

---

## The Core Puzzle: API Succeeded While Streams Failed

During the outage, all stream attempts from CT 105 failed consistently:
- All 9 channel groups returned `HTTP 511`.
- All User-Agents (`MediaCoreSync/1.0`, `Lavf/60.16.100`, `VLC/3.0.20`, standard browser UAs) returned `HTTP 511`.
- Direct requests to `http://cf.teltv.xyz/live/<user>/<pass>/<id>.ts` and requests via Threadfin's `HTTP 302` redirect both returned `HTTP 511`.

At the exact same time, from the **same container and same IP address**:
```bash
# Executed in CT 105:
curl -s "http://cf.teltv.xyz/player_api.php?username=...&password=..."
# Returned:
# {"user_info": {"auth": 1, "status": "Active", "exp_date": "1793142000", "active_cons": 0, "max_connections": 1}}
```
`player_api.php` reported `status=Active`, `auth=1`, `active_cons=0`, with subscription valid through `2026-10-28`. The account credentials were valid and active, yet streaming requests were completely blocked.

---

## Hypotheses Investigated & Ruled Out by Measurement

1. **Subscription Expiration / Account Deactivation:**
   - *Ruled Out:* `player_api.php` returned `status=Active` and expiry timestamp `1793142000` (2026-10-28).
2. **WireGuard VPN Down / Dead Route:**
   - *Ruled Out:* `wgclient1` handshake was active, transfer byte counters were actively climbing, public IP was verified in Zurich, Switzerland (`89.37.173.42`), and general internet HTTPS traffic returned `HTTP 200`.
3. **DNS Resolution Mismatch:**
   - *Ruled Out:* Both `player_api.php` and stream URLs query the exact same hostname `cf.teltv.xyz` resolving to Cloudflare edge IP `172.67.212.205`. The API worked over this IP while stream paths failed.
4. **Tuner / Concurrent-Connection Contention:**
   - *Ruled Out:* `player_api.php` reported `active_cons=0` and `max_connections=1`. No ffmpeg processes or active recordings held the tuner.
5. **Agent / Local Code Regression:**
   - *Ruled Out:* Timeline analysis showed the stream failure began at 03:48 CEST, ~6 hours before any automated agent testing began.
6. **"Datacenter IP Ranges Are Globally Blocked" Theory:**
   - *Ruled Out:* A test tunnel to Romania datacenter IPs timed out, which temporarily suggested commercial hosting ranges were blocked wholesale. However, the owner tested the **Surfshark native app on Switzerland (`185.212.170.126`)** at that exact moment, and streams played perfectly. Both connections used the same VPN vendor and same country, but different exit IPs. This disproved the datacenter theory and proved a **specific per-IP block**.

---

## Root Cause

The IPTV provider employs two distinct layers:
1. **Cloudflare Edge (API):** Handles `player_api.php` and web endpoints. Cloudflare allowed traffic from `89.37.173.42`.
2. **Streaming Origin / Ingest Edge:** Handles `/live/<user>/<pass>/<id>.ts` video chunk delivery. This layer independently blocked the specific IP `89.37.173.42`, responding with `HTTP 511 Network Authentication Required`.

Because commercial VPN providers cycle addresses and shared IP pools occasionally attract abuse flags, `89.37.173.42` had landed on the streaming provider's blocklist.

---

## 5-Minute Diagnostic and Recovery Runbook

When live streams fail or recordings drop with HTTP 511 / 502 / timeout:

### 1. Diagnose in 60 seconds

Run from `pve-01`:
```bash
# Check 1: Does the API authenticate?
pct exec 105 -- python3 -c '
import urllib.request, json
with open("/srv/media-core/.env") as f:
    env = dict(l.strip().split("=", 1) for l in f if l.strip() and not l.startswith("#") and "=" in l)
url = f"{env[\"XTREAM_BASE\"]}/player_api.php?username={env[\"XTREAM_USER\"]}&password={env[\"XTREAM_PASS\"]}"
req = urllib.request.Request(url, headers={"User-Agent": "MediaCoreSync/1.0"})
with urllib.request.urlopen(req, timeout=10) as r:
    data = json.load(r)
print("API Status:", data.get("user_info", {}).get("status"), "Auth:", data.get("user_info", {}).get("auth"))
'

# Check 2: Does a stream return HTTP 511?
pct exec 105 -- python3 -c '
import urllib.request
with open("/srv/media-core/.env") as f:
    env = dict(l.strip().split("=", 1) for l in f if l.strip() and not l.startswith("#") and "=" in l)
url = f"{env[\"XTREAM_BASE\"]}/live/{env[\"XTREAM_USER\"]}/{env[\"XTREAM_PASS\"]}/430234.ts"
req = urllib.request.Request(url, headers={"User-Agent": "MediaCoreSync/1.0"})
try:
    with urllib.request.urlopen(req, timeout=10) as resp:
        print("Stream HTTP Status:", resp.status)
except urllib.error.HTTPError as e:
    print("Stream HTTP Error:", e.code)
'
```

**Diagnostic Rule:**
* If API Status is `Active` / `Auth: 1`, but Stream returns `511` (or timeout / `502`), **the exit IP is blocked**.

### 2. Fix in 60 seconds (Bounce wgclient1)

Execute on `pve-01` to bounce tunnel 2430 on the GL.iNet router:
```bash
# Disable tunnel 2430
ssh root@192.168.9.1 "ubus call gl-session call '{\"module\":\"vpn-client\",\"func\":\"set_tunnel\",\"params\":{\"tunnel_id\":2430,\"enabled\":false}}'"

sleep 8

# Re-enable tunnel 2430 to acquire a new IP from the Surfshark Zurich pool
ssh root@192.168.9.1 "ubus call gl-session call '{\"module\":\"vpn-client\",\"func\":\"set_tunnel\",\"params\":{\"tunnel_id\":2430,\"enabled\":true}}'"
```

### 3. Verify Resolution

```bash
# 1. Verify new public exit IP
pct exec 105 -- wget -qO- https://api.ipify.org
# Expect a new IP different from the previous blocked IP

# 2. Verify stream plays with HTTP 200
pct exec 105 -- python3 -c '
import urllib.request
with open("/srv/media-core/.env") as f:
    env = dict(l.strip().split("=", 1) for l in f if l.strip() and not l.startswith("#") and "=" in l)
url = f"{env[\"XTREAM_BASE\"]}/live/{env[\"XTREAM_USER\"]}/{env[\"XTREAM_PASS\"]}/430234.ts"
req = urllib.request.Request(url, headers={"User-Agent": "MediaCoreSync/1.0"})
with urllib.request.urlopen(req, timeout=10) as resp:
    print("Stream Status:", resp.status, "Read bytes:", len(resp.read(8192)))
'
# Expect: Stream Status: 200 Read bytes: 8192
```
