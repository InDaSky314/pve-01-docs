# Virtual machines

All guests are QEMU/KVM with `cpu: host`, virtio-scsi single controllers,
virtio NICs on the PVE firewall, and the QEMU guest agent enabled. Desktop
VMs use QXL video + SPICE (ich9 HDA audio) so they can be used from
`virt-viewer` on the host's KDE desktop. All VMs are currently **stopped**.

## VM 100 — PFSENSE

- pfSense CE 2.7.1, `ostype: other`, 1 vCPU / 2 GB RAM, 16 GB virtio disk
  on `local-lvm`.
- **Five NICs**, one per bridge (`vmbr0`, `vmbr4`, `vmbr1`–`vmbr3`) — see
  [network.md](network.md).
- `startup: order=1` — boots first when autostart is used.
- **Four snapshots with saved RAM state**, tracking a Tailscale experiment:
  1. `troubleshooting` (Dec 2023)
  2. `tailscale-only-gui`
  3. `Factory-Reset-Before-Talescale`
  4. `tailscale-installed-still-accesable` ← current parent
  The snapshot chain preserves the box before/after installing Tailscale on
  pfSense, with a factory-reset checkpoint in the middle. Note the current
  config has `onboot` removed (snapshots had `onboot: 1`), so pfSense no
  longer autostarts.

## VM 101 — Zorn

- Zorin OS 16.3 desktop (`ostype: l26`), 4 vCPU / 4 GB RAM.
- 32 GB qcow2 on `SSD` storage, `fstrim_cloned_disks=1` on the agent.
- QXL + SPICE audio; standard desktop-VM setup.

## VM 102 — WIN11

- Windows 11: OVMF/UEFI with pre-enrolled Secure Boot keys, q35 machine
  (`pc-q35-8.0`), vTPM 2.0 (`tpmstate0` on `local-lvm`).
- 4 vCPU / 8 GB RAM, 80 GB qcow2 on `SSD`.
- USB passthrough of host port 1-3 (`usb0: host=1-3,usb3=1`).
- `startup: order=2,up=60` — second in autostart order, 60 s delay.
- Snapshot history: `BeforeWIN11` (was a Win10 install, 2 vCPU,
  `x86-64-v2-AES`) → `wert` → `Troubleshooting` (attached to isolated
  `vmbr4`) → current (back on `vmbr0`). One leftover `unused0` raw disk on
  `SSD` could be reclaimed.

## VM 103 — Docker

- Linux Docker host (`ostype: l26`), 2 vCPU / 8 GB RAM.
- Two virtio disks on `local-lvm`: 40 GB OS disk (boot) + **1 TB data disk**
  for containers/volumes.
- SPICE enhancements enabled (folder sharing, video streaming) and QXL with
  128 MB VRAM — set up to be used interactively, not just headless.

## VM 104 — SRV-STD-2022

- Windows Server 2022 Standard Evaluation (`ostype: win11`): OVMF/UEFI,
  q35 (`pc-q35-8.1`), vTPM 2.0.
- 2 vCPU / 8 GB RAM, 60 GB qcow2 on `SSD` with writeback cache + discard.
- Server 2022 eval ISO and virtio-win 0.1.240 ISO still attached as CD-ROMs
  (install-era config).
