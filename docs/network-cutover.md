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
- [x] pve-01 physically connected to the `192.168.9.x` L2 — **done by 2026-07-05**
- [x] pve-01 re-IP'd to `192.168.9.11` — **done by 2026-07-05** (host reachable
      at `192.168.9.11`, default route via `192.168.9.1`)
- [x] Verification checklist passed — **2026-07-05**: gateway ping, DNS,
      internet egress OK from host and from the media node at `192.168.9.50`.
      Swiss split-tunnel for `.50` verified working (see "Egress anomaly —
      resolved" below).
- [x] "Tunnel B" (Switzerland) for VM 103: **done 2026-07-04.** OpenVPN TCP
      tunnel `VM103-Swiss` (tunnel_id 8925) using the already-imported
      Surfshark profile `ch-zur.prod.surfshark.com_tcp` (group
      `SurfSharkAll` 48771, client 468), running as `ovpnclient2` alongside
      the US tunnel. Kill switch **on**; VM 103's MAC
      (`BC:24:11:59:1F:60`) is the only device bound to it. Verified:
      tunnel egress is `156.146.62.37` — Zurich, Switzerland; other LAN
      clients unaffected.

~~As of 2026-07-04 pve-01 is **not visible on the current network**~~ —
**superseded 2026-07-05: the cutover is complete.** pve-01 is live at
`192.168.9.11` and the media node at `192.168.9.50`.

> **2026-07-05 — the `.50` device is now LXC 105, not VM 103.** The owner
> destroyed VMs 100/101/103 on 2026-07-05. The new media node is an
> unprivileged Debian 13 LXC (**CT 105 `media-core`**) whose `net0` carries
> VM 103's old MAC `BC:24:11:59:1F:60` — so the static lease *and* the
> Swiss-tunnel policy binding on the Flint 2 apply to it unchanged, with no
> router-side edits. (Never give another guest that MAC.)

## Egress anomaly — ✅ RESOLVED 2026-07-05

During the LXC build the Swiss policy binding was briefly not in effect
(CT 105 and the host both egressed via the same US datacenter IP
`45.43.19.29`). The owner fixed it on the router the same day. Verified
after the fix:

- CT 105 (`192.168.9.50`) → **`146.70.134.252`, Zurich, Switzerland**
  (M247 — a Surfshark CH exit; the exit IP can differ from the
  `156.146.62.x` seen on 2026-07-04, that's normal).
- pve-01 host → `45.43.19.29` (US) — i.e. **not** via the Swiss tunnel;
  split tunnel works as designed.
- No DNS leak: from CT 105, `whoami.akamai.net` resolves via
  `146.70.134.252` (queries exit through the tunnel); from the host, via
  the US path.

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
- VPN ("Tunnel B") is already **done** — see the as-built section below.
  Nothing VPN-related remains before or after the cutover; VM 103 starts
  egressing via Zurich the moment it appears at `192.168.9.50`.

## Tunnel B — Switzerland OpenVPN (TCP) for VM 103 only ✅ done

**Implemented 2026-07-04** via the router API. OpenVPN over TCP was chosen
for stability, matching the existing Surfshark-US tunnel. As built:

| | |
|---|---|
| Tunnel | `VM103-Swiss`, tunnel_id `8925`, interface `ovpnclient2` |
| Profile | `ch-zur.prod.surfshark.com_tcp` (port 1443/TCP) — was already imported on the router in group `SurfSharkAll` (48771, client 468) with stored service credentials |
| Bound devices | only MAC `BC:24:11:59:1F:60` (VM 103 / `192.168.9.50`), assigned via `vpn-client set_single_mac` |
| Kill switch | **on** (`route_policy.@rule[1].killswitch='1'`) — VM 103 gets no WAN if the tunnel drops; LAN (Jellyfin serving, SSH) unaffected |
| Default policy | unchanged — every other device stays no-VPN; the US "Primary Tunnel" (`ovpnclient1`) keeps running untouched |
| Verified | `curl --interface ovpnclient2 am.i.mullvad.net/json` → `156.146.62.37`, Zurich, Switzerland; a LAN client checked at the same time did **not** egress via CH |

Both tunnels appear side by side in the UI under **VPN Dashboard**; the
device binding is under **Clients → VM103-Docker → VPN policy**.

Throughput note: OpenVPN/TCP tops out far lower than WireGuard on this box
and adds TCP-over-TCP overhead, but an IPTV stream is ~8–15 Mbit/s — well
within it. If 1080p buffering ever becomes chronic, switching Tunnel B to
WireGuard is a drop-in change (same device binding, different tunnel).

**Re-verify once the server is on the network:**

```bash
# on VM 103 — must print a Surfshark Switzerland IP:
curl -4 ifconfig.me
# on any other machine — must print the home WAN IP (or Tunnel A's):
curl -4 ifconfig.me
# DNS must not leak — should resolve via the tunnel DNS from VM 103:
dig +short whoami.akamai.net
```

If VM 103 has no internet at all after this: the tunnel is down and the
kill switch is doing its job — check the `VM103-Swiss` tunnel status in the
router's VPN Dashboard before debugging anything inside the VM.
