# Network cutover runbook — `192.168.8.0/24` → `192.168.9.0/24`

Moves `pve-01` from the old AXT1800 LAN (`192.168.8.0/24`) onto the current
Brume 2 network (`192.168.9.0/24`). This is Phase 1 of
[project-media-core.md](project-media-core.md), expanded into exact commands.

## Status

- [x] Brume 2 is live as the current network gateway — verified 2026-07-04:
  `192.168.9.1` answers (GL.iNet console), DHCP is handing out
  `192.168.9.12x`–`.18x` leases to Wi-Fi clients.
- [ ] Static DHCP lease `192.168.9.50` reserved for VM 103 on the Brume 2
- [ ] AXT1800 switched to Access-Point mode behind the Brume 2
- [ ] pve-01 physically connected to the `192.168.9.x` L2
- [ ] pve-01 re-IP'd to `192.168.9.11` (steps below)
- [ ] Verification checklist passed
- [ ] WireGuard Tunnel B policy routing bound to VM 103 (media-core Phase 1,
      can be done later)

As of 2026-07-04 pve-01 is **not visible on the current network** (full ping
sweep of `192.168.9.0/24` found only the Brume 2, one Wi-Fi AP-ish device at
`.183`, a TV and a laptop). It is either powered off or still cabled to the
old AXT1800 LAN with its static `192.168.8.11` config — with that config it
cannot talk on the new subnet even if the cable is moved, which is exactly
what this runbook fixes.

## Address plan

| Device | Old | New | How |
|---|---|---|---|
| Gateway / DNS / DHCP | `192.168.8.1` (AXT1800) | `192.168.9.1` (Brume 2) | Brume 2 is the only router; AXT1800 becomes an AP |
| pve-01 host (`vmbr0`) | `192.168.8.11/24` static | `192.168.9.11/24` static | edit `/etc/network/interfaces` (step 4) |
| VM 103 "Docker" | DHCP on old LAN | `192.168.9.50` | static DHCP lease on Brume 2 keyed to MAC `BC:24:11:59:1F:60` |
| Other VMs (101/102/104) | DHCP | DHCP (`192.168.9.x` pool) | nothing to do if they use DHCP — verify, see step 6 |
| pfSense (VM 100) | stopped | stays stopped | Brume 2 does all routing/VPN |

> Both static addresses (`.11`, `.50`) sit below the Brume 2's DHCP pool
> (observed leases start at `.100`+). Confirm the pool in the Brume 2 UI
> (Network → LAN) doesn't reach below `.100`; shrink it if it does.

## Step 1 — Brume 2: static lease for VM 103

GL.iNet UI at <http://192.168.9.1> → Clients (or Network → LAN → Address
Reservation): bind `192.168.9.50` to MAC `BC:24:11:59:1F:60` (VM 103 `net0`).
The VM won't show as a client until it has booted on the new network once —
GL.iNet lets you add a reservation manually by MAC before that.

## Step 2 — AXT1800: Access-Point mode

AXT1800 UI → Network → Network Mode → **Access Point**, then uplink one of
its ports **LAN→LAN** into the Brume 2. SSIDs (`GL-AXT1800-ef3`,
`GL-AXT1800-ef3-5G`, `IOT`) carry over; all Wi-Fi clients now pull
`192.168.9.x` from the Brume 2. In AP mode the AXT1800's remaining ports act
as a plain switch — use one of them for pve-01 if the Brume 2's single LAN
port is taken.

## Step 3 — Physical

Cable pve-01 `enp2s0` (the first NIC — the only one wired today) into the
Brume 2 LAN or any AXT1800 port (same L2 once it's an AP). `enp3s0`–`enp5s0`
stay unplugged.

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
- Optional (media-core Phase 1, item 1): WireGuard tunnels A/B on the
  Brume 2 with Tunnel B policy-routed to `192.168.9.50` only.
