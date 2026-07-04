# pve-01 — Proxmox VE Homelab

Documentation for my single-node Proxmox VE homelab server, including the
(unusual) KDE Plasma desktop installed directly on the hypervisor host.

> ⚠️ This repo documents a private home network. Keep it **private** on GitHub —
> it contains internal IP addresses and network layout.

## At a glance

| | |
|---|---|
| **Hostname** | `pve-01` |
| **Platform** | Intel Celeron N5105 mini-PC, 4 cores, 32 GB RAM, 4× 2.5GbE NICs |
| **Hypervisor** | Proxmox VE 9.2.4 (kernel 6.8.12-32-pve) |
| **Base OS** | Debian 13 "trixie" |
| **Desktop GUI** | KDE Plasma 6.3 (installed on the host itself — see [docs/desktop-gui.md](docs/desktop-gui.md)) |
| **Management IP** | `192.168.8.11/24` on bridge `vmbr0` (gateway `192.168.8.1`) |
| **Web UI** | https://192.168.8.11:8006 |
| **Timezone** | Europe/Berlin |

## What runs on it

Five QEMU/KVM virtual machines (no LXC containers currently):

| VMID | Name | OS | vCPU | RAM | Purpose |
|---|---|---|---|---|---|
| 100 | PFSENSE | pfSense CE 2.7.1 | 1 | 2 GB | Router/firewall — wired to all 5 bridges |
| 101 | Zorn | Linux (Zorin OS 16.3) | 4 | 4 GB | Linux desktop VM |
| 102 | WIN11 | Windows 11 (UEFI + vTPM) | 4 | 8 GB | Windows desktop VM |
| 103 | Docker | Linux | 2 | 8 GB | Docker host (1 TB data disk) |
| 104 | SRV-STD-2022 | Windows Server 2022 Eval | 2 | 8 GB | Windows Server lab |

Details in [docs/virtual-machines.md](docs/virtual-machines.md).

## Documentation index

- [docs/desktop-gui.md](docs/desktop-gui.md) — how and why KDE Plasma is installed on the hypervisor
- [docs/network.md](docs/network.md) — physical NICs, Linux bridges, pfSense wiring
- [docs/storage.md](docs/storage.md) — disks, LVM-thin layout, storage pools
- [docs/virtual-machines.md](docs/virtual-machines.md) — per-VM configuration and snapshots
- [docs/host-setup.md](docs/host-setup.md) — packages, repos, services, known quirks
- [docs/project-media-core.md](docs/project-media-core.md) — **planned**: DVR/media stack on VM 103 + migration to Brume 2 gateway (`192.168.9.0/24`)

## Architecture overview

```
                        ┌─────────────────────────────────────────────┐
                        │  pve-01  (Proxmox VE 9.2 + KDE Plasma)      │
                        │                                             │
  LAN 192.168.8.0/24 ───┤ enp2s0 ── vmbr0 ── host IP 192.168.8.11     │
                        │             │                               │
                        │             ├── VM100 pfSense (net0)        │
                        │             ├── VM101 Zorn                  │
                        │             ├── VM102 WIN11                 │
                        │             ├── VM103 Docker                │
                        │             └── VM104 SRV-STD-2022          │
                        │                                             │
        (unplugged) ────┤ enp3s0 ── vmbr1 ──┐                         │
        (unplugged) ────┤ enp4s0 ── vmbr2 ──┼── VM100 pfSense         │
        (unplugged) ────┤ enp5s0 ── vmbr3 ──┘   (net2/net3/net4)      │
                        │                                             │
                        │  (no NIC) vmbr4 ── VM100 pfSense (net1)     │
                        │           internal-only lab bridge          │
                        └─────────────────────────────────────────────┘
```
