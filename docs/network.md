# Network

## Physical NICs

The box has four onboard 2.5GbE Intel NICs. Only the first is cabled today.

| NIC | Bridge | Link | Role |
|---|---|---|---|
| `enp2s0` | `vmbr0` | **UP** | LAN / management uplink |
| `enp3s0` | `vmbr1` | down | spare (pfSense net2) |
| `enp4s0` | `vmbr2` | down | spare (pfSense net3) |
| `enp5s0` | `vmbr3` | down | spare (pfSense net4) |
| — | `vmbr4` | (no port) | internal-only lab bridge |

## Bridges (`/etc/network/interfaces`, managed by Proxmox)

- **`vmbr0`** — static `192.168.9.11/24`, gateway `192.168.9.1`. Carries the
  host management IP and the default network for every VM.
  (Was `192.168.8.11/24` on the old AXT1800 LAN — see
  [network-cutover.md](network-cutover.md) for the migration runbook.)
- **`vmbr1`–`vmbr3`** — one bridge per spare physical NIC, no IP on the host.
  Ready to become dedicated pfSense WAN/LAN/OPT segments once cabled.
- **`vmbr4`** — no physical port at all. A pure internal switch; used as an
  isolated lab segment (pfSense has a leg on it, and VM 102's
  "Troubleshooting" snapshot config attached to it).

All VM NICs are virtio with the Proxmox firewall flag enabled
(`firewall=1`); both `pve-firewall` and the newer nftables-based
`proxmox-firewall` services are running.

## pfSense wiring (VM 100)

pfSense has five virtio NICs — one per bridge:

| pfSense NIC | Bridge | Backed by |
|---|---|---|
| net0 | vmbr0 | enp2s0 (LAN, currently the only live link) |
| net1 | vmbr4 | internal-only |
| net2 | vmbr1 | enp3s0 |
| net3 | vmbr2 | enp4s0 |
| net4 | vmbr3 | enp5s0 |

The intent: pfSense can take over routing between physical segments as soon
as the other NICs are cabled, without touching the VM config.

> pfSense is currently **stopped** — routing for `192.168.9.0/24` is handled
> upstream by the GL.iNet **GL-MT6000 "Flint 2"** at `192.168.9.1` (the
> media-core plan assumed a Brume 2; the Flint 2 fills the same role and has
> its own Wi-Fi, SSID `Big-GL`). The GL-AXT1800 "Slate AX" (the old
> `192.168.8.1` router) is no longer needed as an AP but can optionally be
> added as one; its SSIDs are saved as Wi-Fi profiles in NetworkManager on
> the host.

## Wi-Fi

NetworkManager (installed with the KDE desktop) holds three saved Wi-Fi
profiles: `GL-AXT1800-ef3`, `GL-AXT1800-ef3-5G`, and `IOT`. No Wi-Fi device
is currently active; wired networking is fully under Proxmox/ifupdown
control, not NetworkManager.
