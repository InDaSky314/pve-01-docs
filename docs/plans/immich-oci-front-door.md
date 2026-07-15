> **Status: saved for later — NOT yet executed.** Approved in principle 2026-07-14;
> owner will say when to start. Tracked in the README's Loose ends.

# Immich with Oracle Cloud Free-Tier Front Door

## Context

Nathan wants self-hosted photo backup (Immich) on pve-01, reachable from his phone off-LAN **without Tailscale/VPN on the phone**. The home network is double-NATed (GL-MT6000 uplinks via WiFi client mode), so inbound port-forwarding is out. Cloudflare Tunnel was rejected due to its ~100 MB upload cap (breaks phone video backup). Solution: an outbound WireGuard tunnel from an Immich LXC to a free Oracle Cloud VM running Caddy as the public HTTPS front door. Phone runs only the Immich app pointed at `photos.<domain>`.

**Free-tier verdict: yes, easily.** The front door needs ~100 MB RAM and negligible CPU. Always Free (verified July 2026) includes: Ampere A1 up to **2 OCPU / 12 GB** (halved 2026-06-15), 2× E2.1.Micro x86 VMs, 200 GB block storage, 10 TB/mo egress, free public IP. Caveat: Oracle reclaims **idle** Always Free instances (95th-pct CPU <20% over 7 days) — mitigated below.

## Resources check (done)

- pve-01: N5105 4-core, 26 GB RAM free, 1.5 TB free on `/mnt/pve/SSD`. CPU is the scarce resource (DVR load ~3.x) → Immich ML jobs must be scheduled off-peak, avoiding the 04:00 media-core cascade.
- Debian 13 LXC template already downloaded (`local:vztmpl/debian-13-standard_13.1-2_amd64.tar.zst`).
- Router policy note: a new LXC would default-route through the **US Surfshark tunnel** (route_policy rule[0] catches all non-IPTV MACs). The WG tunnel to OCI should NOT ride inside Surfshark → add a `novpn` policy rule for the Immich LXC's MAC (same uci mechanism as the 2026-07-14 Swiss/WireGuard change; backup exists in scratchpad).

## Plan

### 1. User actions (blocking, ~10 min total)
- Buy domain at Porkbun or Cloudflare Registrar (~$10/yr). If DNS is hosted on Cloudflare: the `photos` record must be **DNS-only (grey cloud)** — proxying through Cloudflare reintroduces the 100 MB upload cap.
- OCI: add the API public key I generate to Console → My Profile → API Keys, and paste back the tenancy/user/fingerprint config block OCI shows. (Console login + MFA can't be driven from here, so this step is Nathan's regardless of shared credentials.)

### 2. OCI CLI + provisioning (me, from pve-01)
- Install `oci` CLI, configure `~/.oci/config` with the API key.
- **First: verify home region** (`oci iam region-subscription list`). Nathan believes it's Ashburn (us-ashburn-1); home internet is in **Germany**. Always Free compute is home-region-only, so Ashburn adds ~200 ms RTT for a phone in Europe — acceptable for backup (the primary use), slightly sluggish for remote browsing. If the home region turns out to be an EU region, use it and the latency concern disappears. No paid cross-region resources.
- Provision via CLI, **everything from the Always Free set**:
  - VCN + public subnet + internet gateway + security list (ingress: 443/tcp, 80/tcp for ACME, 51820/udp for WireGuard; egress: all).
  - Instance: `VM.Standard.A1.Flex` **1 OCPU / 6 GB** (half the new A1 allowance, leaves headroom), Ubuntu 24.04, 50 GB boot volume, ephemeral public IP, my SSH pubkey. Fallback if A1 capacity unavailable in home region: `VM.Standard.E2.1.Micro`.
- Save a re-provision script + notes in `/root/pve-01-docs/` so a reclaimed instance is a ~10-min rebuild (VM is a stateless front door; photos never leave home).

### 3. OCI VM setup
- WireGuard **server** (listens :51820), Caddy reverse proxy: `photos.<domain>` → `10.66.0.2:2283` (LXC over WG), auto-HTTPS via Let's Encrypt, no request-body limit. `unattended-upgrades` on.

### 4. Immich LXC on pve-01
- New Debian 13 LXC (suggest VMID 106, 4 GB RAM, 32 GB rootfs on local-lvm), Docker + official Immich compose stack.
- Photo library bind-mounted from `/mnt/pve/SSD/immich` (1.5 TB free).
- WireGuard **client** → OCI VM with persistent-keepalive 25 (works through double NAT and regardless of router VPN policy).
- Immich ML/thumbnail jobs: concurrency 1, and smart-search/face-detection scheduled off-peak (~01:00, clear of the 04:00 cascade).

### 5. Router policy (one uci rule)
- Add `novpn` route_policy entry for the Immich LXC MAC so its WAN path bypasses Surfshark. Does not touch the IPTV rules or kill-switch. Verify IPTV egress still Zurich afterward.

### 6. DNS + Immich config
- A record `photos.<domain>` → VM public IP.
- Immich admin account, **2FA enabled**, mobile app pointed at `https://photos.<domain>`.

### 7. Idle-reclamation handling
- Stay Always Free (per Nathan). Accept reclamation risk: front door is stateless + scripted rebuild; DNS update is the only manual step. Optional (Nathan's call, not default): upgrade tenancy to Pay-As-You-Go — exempts from reclamation and still $0 within Always Free shapes, but requires a card on file.
- No artificial CPU load to dodge the idle detector (ToS-hostile, not proposed).

## Verification
1. From phone on **cellular**: Immich app login at `https://photos.<domain>`, upload a photo and a **>100 MB video** (the case Cloudflare Tunnel fails).
2. `curl -w` throughput test through the front door; expect ~50–80 Mbps (bounded by home uplink path).
3. Confirm IPTV unaffected: media-core egress still Zurich, kill-switch rules intact (`ip rule` on router unchanged for marks 0xa000/0x1000).
4. Reboot test: LXC autostart + WG reconnect + Caddy comes back without intervention.
5. Verify OCI monthly cost shows $0.00 after 24–48 h (Console → Billing).

## Free-tier guardrails (hard constraints)
- Only `VM.Standard.A1.Flex` ≤ 2 OCPU/12 GB total or `E2.1.Micro`; boot volume ≤ 200 GB total tenancy-wide; no load balancer, no reserved-but-unattached IPs, no paid add-ons. Egress (photo viewing) is pennies-scale vs the 10 TB/mo free allowance.
