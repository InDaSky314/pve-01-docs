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
| **Management IP** | `192.168.9.11/24` on bridge `vmbr0` (gateway `192.168.9.1`, GL-MT6000 "Flint 2") |
| **Web UI** | https://192.168.9.11:8006 |
| **Timezone** | Europe/Berlin |

## What runs on it

Two QEMU/KVM virtual machines and one LXC container (VMs 100 PFSENSE,
101 Zorn and 103 Docker were destroyed 2026-07-05):

| ID | Name | Type / OS | vCPU | RAM | Purpose |
|---|---|---|---|---|---|
| 102 | WIN11 | VM — Windows 11 (UEFI + vTPM) | 4 | 8 GB | Windows desktop VM |
| 104 | SRV-STD-2022 | VM — Windows Server 2022 Eval | 2 | 8 GB | Windows Server lab |
| 105 | media-core | LXC — Debian 13 (unprivileged) | 2 | 8 GB | Media-Core Docker stack (Jellyfin/Threadfin/m3u2strm), `192.168.9.50`, 1 TB data mount |

Details in [docs/virtual-machines.md](docs/virtual-machines.md).

## Documentation index

> 🤖 **Agents start at [CLAUDE.md](CLAUDE.md)** — reading order, current
> project state, verified network facts, and hard rules.

- [docs/desktop-gui.md](docs/desktop-gui.md) — how and why KDE Plasma is installed on the hypervisor
- [docs/network.md](docs/network.md) — physical NICs, Linux bridges, pfSense wiring
- [docs/network-cutover.md](docs/network-cutover.md) — **runbook**: migration from the old AXT1800 LAN (`192.168.8.0/24`) to the Brume 2 (`192.168.9.0/24`), with status checklist and rollback
- [docs/storage.md](docs/storage.md) — disks, LVM-thin layout, storage pools
- [docs/virtual-machines.md](docs/virtual-machines.md) — per-VM configuration and snapshots
- [docs/host-setup.md](docs/host-setup.md) — packages, repos, services, known quirks
- [docs/project-media-core.md](docs/project-media-core.md) — DVR/media stack in LXC 105 (`192.168.9.50`); Phases 0–1 deployed 2026-07-05, Phase 2 (provider URLs + UI config) pending
- [docs/media-core-manifest.md](docs/media-core-manifest.md) — the original Media-Core manifest the plan above adapts (reference only)

## Architecture overview

```
                        ┌─────────────────────────────────────────────┐
                        │  pve-01  (Proxmox VE 9.2 + KDE Plasma)      │
                        │                                             │
  LAN 192.168.9.0/24 ───┤ enp2s0 ── vmbr0 ── host IP 192.168.9.11     │
                        │             │                               │
                        │             ├── VM102 WIN11                 │
                        │             ├── VM104 SRV-STD-2022          │
                        │             └── CT105 media-core (.9.50)    │
                        │                                             │
        (unplugged) ────┤ enp3s0 ── vmbr1                             │
        (unplugged) ────┤ enp4s0 ── vmbr2                             │
        (unplugged) ────┤ enp5s0 ── vmbr3                             │
                        │                                             │
                        │  (no NIC) vmbr4 ── internal-only lab bridge │
                        └─────────────────────────────────────────────┘
```
