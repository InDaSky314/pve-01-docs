# Network cutover runbook — `192.168.8.0/24` → `192.168.9.0/24`

Moves `pve-01` from the old AXT1800 LAN (`192.168.8.0/24`) onto the current
gateway network (`192.168.9.0/24`). This is Phase 1 of
[project-media-core.md](project-media-core.md), expanded into exact commands.

> **Reality check (2026-07-04):** the deployed gateway is a **GL-MT6000
> "Flint 2"** (firmware 4.9.0), *not* the Brume 2 the media-core plan
> assumed. Same role in the design, one welcome difference: the Flint 2
> **has Wi-Fi radios** (SSID `Big-GL` live on 2.4 GHz, plus a GL-BE3600 and
> a UniFi UAP-AC-Lite already acting as APs on the LAN), so the
> "AXT1800 as access point" step is **optional** — the Chromecast is
> already on this LAN over the existing Wi-Fi.

## Status

- [x] Gateway (Flint 2) live at `192.168.9.1/24`, DHCP pool
  `192.168.9.100`–`.249` (start 100, limit 150) — so the static `.11` and
  `.50` are **outside the pool**, no conflict. Verified 2026-07-04 via
  router API + `uci`.
- [x] Static DHCP lease `192.168.9.50` reserved for VM 103
  (MAC `BC:24:11:59:1F:60`, tag `VM103-Docker`) — **added 2026-07-04**,
  committed and dnsmasq reloaded.
- [x] VPN policy safe for the server: the router's default route policy is
  **no VPN** (an OpenVPN "Primary Tunnel" rule with kill switch exists but
  binds no LAN device by MAC), so pve-01 and the VMs will egress direct.
- [ ] ~~AXT1800 switched to Access-Point mode~~ — optional now (see note
      above); only needed if its extra ports/SSIDs are wanted.
- [ ] pve-01 physically connected to the `192.168.9.x` L2
- [ ] pve-01 re-IP'd to `192.168.9.11` (steps below)
- [ ] Verification checklist passed
- [ ] "Tunnel B" (Switzerland) for VM 103: **not configured yet.** WireGuard
      configs on the router are provider stubs (AzireVPN/Mullvad groups,
      empty; one Surfshark US-Chicago peer imported); the active tunnel is
      OpenVPN (Surfshark US). Adding a CH WireGuard tunnel + policy-routing
      it to `192.168.9.50` is media-core Phase 1 work, fine to do later.

As of 2026-07-04 pve-01 is **not visible on the current network** (full ping
sweep of `192.168.9.0/24`; neither the host nor VM 103's MAC has *ever*
appeared in the router's client history). It is either powered off or still
cabled to the old AXT1800 LAN with its static `192.168.8.11` config — with
that config it cannot talk on the new subnet even if the cable is moved,
which is exactly what this runbook fixes.

## Address plan

| Device | Old | New | How |
|---|---|---|---|
| Gateway / DNS / DHCP | `192.168.8.1` (AXT1800) | `192.168.9.1` (Flint 2) | Flint 2 is the only router |
| pve-01 host (`vmbr0`) | `192.168.8.11/24` static | `192.168.9.11/24` static | edit `/etc/network/interfaces` (step 4) |
| VM 103 "Docker" | DHCP on old LAN | `192.168.9.50` | ✅ static lease on the Flint 2, keyed to MAC `BC:24:11:59:1F:60` |
| Other VMs (101/102/104) | DHCP | DHCP (`192.168.9.100`–`.249` pool) | nothing to do if they use DHCP — verify, see step 6 |
| pfSense (VM 100) | stopped | stays stopped | Flint 2 does all routing/VPN |

Note: the Flint 2 also runs a separate IoT subnet (`192.168.10.1/24`) and a
guest network — the server belongs on the main LAN, not those.

## Step 1 — Flint 2: static lease for VM 103 ✅ done

Added 2026-07-04 via the router API (visible in the GL.iNet UI under
Clients / Address Reservation):

```
dhcp.@host[4].mac='BC:24:11:59:1F:60'   # VM 103 net0
dhcp.@host[4].ip='192.168.9.50'
dhcp.@host[4].tag='VM103-Docker'
```

## Step 2 — AXT1800: Access-Point mode (optional)

Not required: the Flint 2 has its own radios (`Big-GL`), and a GL-BE3600 +
UAP-AC-Lite already extend the LAN. Only do this if the AXT1800's SSIDs
(`GL-AXT1800-ef3*`, `IOT`) or extra switch ports are wanted: AXT1800 UI →
Network → Network Mode → **Access Point**, uplink LAN→LAN into the Flint 2.

## Step 3 — Physical

Cable pve-01 `enp2s0` (the first NIC — the only one wired today) into any
Flint 2 LAN port (`lan1`–`lan4`, or a switch/AP port on the same L2).
`enp3s0`–`enp5s0` stay unplugged.

## Step 4 — Re-IP pve-01

⚠️ **Work at the local KDE console as root, not over SSH** — the session
drops the moment the IP changes.

```bash
# 1. Backup
cp /etc/network/interfaces /etc/network/interfaces.bak-192.168.8

# 2. vmbr0: address first, then gateway (this order keeps the patterns unambiguous)
sed -i 's|address 192.168.8.11/24|address 192.168.9.11/24|' /etc/network/interfaces
sed -i 's|gateway 192.168.8.1|gateway 192.168.9.1|'         /etc/network/interfaces

# 3. Host name resolution and DNS
sed -i 's|192\.168\.8\.11|192.168.9.11|' /etc/hosts
sed -i 's|nameserver 192\.168\.8\.1|nameserver 192.168.9.1|' /etc/resolv.conf

# 4. Review, then apply
grep -n '192\.168' /etc/network/interfaces /etc/hosts /etc/resolv.conf
ifreload -a
```

Only the `vmbr0` stanza changes; `vmbr1`–`vmbr4` and the physical-NIC lines
stay as they are. Target stanza:

```
auto vmbr0
iface vmbr0 inet static
        address 192.168.9.11/24
        gateway 192.168.9.1
        bridge-ports enp2s0
        bridge-stp off
        bridge-fd 0
```

If the actual file differs from what `sed` expects (check with the `grep` in
step 4.4 — the two `192.168.9.x` lines must be there), edit it by hand
instead.

## Step 5 — Verify

From pve-01 (console):

```bash
ip -4 addr show vmbr0        # 192.168.9.11/24
ip route | head -1           # default via 192.168.9.1 dev vmbr0
ping -c2 192.168.9.1         # gateway
ping -c2 1.1.1.1             # internet
ping -c2 deb.debian.org      # DNS
hostname --ip-address        # 192.168.9.11 (proves /etc/hosts is right — pveproxy cares)
```

From another machine on the LAN:

- <https://192.168.9.11:8006> — PVE web UI (accept the self-signed cert again)
- `ssh root@192.168.9.11` — expect a known-hosts warning on machines that
  knew the old IP; clear with `ssh-keygen -R 192.168.8.11`

## Step 6 — VMs

1. Boot VM 103 → it should lease `192.168.9.50`. If it comes up with a
   `192.168.8.x` address instead, its guest OS has a **static** config —
   switch it to DHCP inside the guest (`/etc/netplan/` or
   `/etc/network/interfaces`, depending on distro).
2. Same check for VMs 101/102/104 when they're next used: DHCP → nothing to
   do; static `192.168.8.x` → reconfigure in the guest.
3. pfSense (VM 100) stays **stopped**. Its interface assignments reference
   the old design; leave that for whenever it's actually needed.

## Rollback

Everything is reversible from the local console:

```bash
cp /etc/network/interfaces.bak-192.168.8 /etc/network/interfaces
sed -i 's|192\.168\.9\.11|192.168.8.11|' /etc/hosts
sed -i 's|nameserver 192\.168\.9\.1|nameserver 192.168.8.1|' /etc/resolv.conf
ifreload -a
```

…and cable back to the AXT1800 LAN (or flip the AXT1800 out of AP mode).

## After cutover

- `README.md` and `docs/network.md` in this repo already show the new
  `192.168.9.x` addressing — tick the checkboxes in **Status** above as each
  step lands, then this note can go.
- Continue with [project-media-core.md](project-media-core.md) Phase 0/2
  (the Docker stack on VM 103) — from here on the agent build on pve-01 can
  proceed over SSH at `192.168.9.11`.
- Pending VPN work (media-core Phase 1, item 1), adapted to what's actually
  on the Flint 2: normal clients already egress direct or via the existing
  OpenVPN "Primary Tunnel" (Surfshark US) per policy; the missing piece is a
  **Switzerland WireGuard tunnel policy-routed to `192.168.9.50` only**
  ("Tunnel B") — procedure below. The Flint 2's default-no-VPN policy means
  the server works fine before this lands.

## Tunnel B — Switzerland OpenVPN (TCP) for VM 103 only

**Chosen approach: OpenVPN over TCP** (preferred for stability), matching
the existing Surfshark-US tunnel — the router already has a `SurfShark-TCP`
OpenVPN client group with the service credentials stored, so the CH tunnel
is one more profile in that group. The existing Surfshark subscription
covers it; no second provider needed. This can all be done before the
server is even moved — the policy starts applying when VM 103 first leases
`192.168.9.50`.

Throughput note: OpenVPN/TCP tops out far lower than WireGuard on this box
and adds TCP-over-TCP overhead, but an IPTV stream is ~8–15 Mbit/s — well
within it. If 1080p buffering ever becomes chronic, switching Tunnel B to
WireGuard is a drop-in change (same policy rule, different tunnel).

1. **Get the CH OpenVPN config** (account holder step): Surfshark website →
   **VPN → Manual setup → Router → OpenVPN** → download the
   **Switzerland (Zurich) TCP** profile (`ch-zur.prod.surfshark.com_tcp.ovpn`).
   The service credentials shown on that page are already stored on the
   router from the US setup.
2. **Install it on the Flint 2**: UI at `http://192.168.9.1` → **VPN →
   OpenVPN Client → Add Configuration** → upload the `.ovpn` into the
   existing `SurfShark-TCP` group (credentials auto-fill). Do **not**
   connect it "globally".
3. **Bind VM 103 to it**: VPN Dashboard → **VPN Policy / Proxy Mode →
   "Based on the Client Device"** → add a rule: device
   `BC:24:11:59:1F:60` / `192.168.9.50` (shows as `VM103-Docker` once
   seen) → via the CH tunnel. Firmware 4.9 runs this alongside the
   existing US rule (multi-tunnel policy is already enabled:
   `route_policy.global.instance_on='1'`).
4. **Enable the kill switch ("Block Non-VPN Traffic") on that rule** so
   IPTV traffic can never leak out the WAN if the tunnel drops. LAN access
   (Jellyfin from the TV/Chromecast, SSH from the LAN) is unaffected — the
   kill switch only blocks WAN egress.
5. Leave everything else alone: default policy stays **no VPN**; the
   Surfshark-US OpenVPN rule keeps doing whatever it does today.

**Verify** (after the server is on the network):

```bash
# on VM 103 — must print a Surfshark Switzerland IP:
curl -4 ifconfig.me
# on any other machine — must print the home WAN IP (or Tunnel A's):
curl -4 ifconfig.me
# DNS must not leak — should resolve via the tunnel DNS from VM 103:
dig +short whoami.akamai.net
```

If VM 103 has no internet at all after this: the tunnel is down and the
kill switch is doing its job — check the WireGuard client status on the
router before debugging the VM.
