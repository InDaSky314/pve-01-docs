# Storage

## Physical disks

| Device | Model | Size | Role |
|---|---|---|---|
| `nvme0n1` | HP SSD EX900 Plus | 2 TB | Boot disk + LVM (PVE root, swap, `local-lvm` thin pool) |
| `sda` | WD Blue WDS200T2B0A (SATA SSD) | 2 TB | ext4, mounted at `/mnt/pve/SSD` (directory storage) |

## NVMe layout (LVM, volume group `pve`)

```
nvme0n1
├─ p1   BIOS boot (1 MB)
├─ p2   /boot/efi (1 GB, GRUB EFI boot)
└─ p3   LVM PV → VG "pve"
    ├─ pve-swap    8 GB
    ├─ pve-root   96 GB   (/, Debian + PVE + KDE)
    └─ pve-data  ~1.7 TB  LVM-thin pool → "local-lvm"
```

## Proxmox storage pools (`/etc/pve/storage.cfg`)

| Pool | Type | Backing | Content | Size / used |
|---|---|---|---|---|
| `local` | dir | `/var/lib/vz` (on root LV) | ISO, templates, backups | 94 GB, ~28% used |
| `local-lvm` | lvmthin | `pve/data` thin pool | VM disks, container rootfs | 1.7 TB, ~2% used |
| `SSD` | dir | `/mnt/pve/SSD` (`is_mountpoint 1`) | ISO, images (qcow2), backups, templates, snippets | 1.8 TB, ~13% used |

Split in practice:

- **`local-lvm`** holds raw thin-provisioned disks: pfSense's disk, the
  Docker VM's 40 GB OS disk and 1 TB data disk, TPM/EFI vdisks, and the
  saved-RAM state volumes for VM 100's snapshots.
- **`SSD`** holds qcow2 disks for the desktop VMs (101, 102, 104), the ISO
  library, and vzdump backups.

## ISO library (on `SSD`)

pfSense CE 2.7.1, Windows Server 2022 Eval, Windows 11, Zorin OS 16.3,
Ubuntu 22.04 (server + desktop), Fedora 37/39, TrueNAS SCALE 23.10, and
virtio-win driver ISOs (0.1.229 / 0.1.240).

## Backups

vzdump backups land on the `SSD` pool (`/mnt/pve/SSD/dump`). No off-host
backup target (e.g. Proxmox Backup Server or NAS) is configured — worth
adding, since both copies of everything live inside the same chassis.
