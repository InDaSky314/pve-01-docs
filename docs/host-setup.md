# Host setup & maintenance notes

## Versions

- Proxmox VE **9.2.4** (`pve-manager/9.2.4`), kernel **6.8.12-32-pve**
- Debian **13 (trixie)** base
- History shows in-place major upgrades over time (old 6.2/6.5 kernels were
  purged; GRUB EFI reinstalled and `systemd-boot` removed along the way).

## APT repositories

| Repo | Status |
|---|---|
| Debian trixie + updates + security (`main contrib non-free-firmware`) | active |
| `download.proxmox.com … pve-no-subscription` | active — this is where PVE updates come from |
| `enterprise.proxmox.com … pve-enterprise` (deb822 `.sources`) | **still enabled but unusable without a subscription** — causes 401 errors on `apt update`. Disable it: `mv /etc/apt/sources.list.d/pve-enterprise.sources{,.disabled}` |

## Notable installed extras (beyond stock PVE)

- `task-kde-desktop` — full KDE Plasma 6.3 desktop ([desktop-gui.md](desktop-gui.md))
- `virt-viewer` — SPICE client for VM consoles
- `intel-microcode` — CPU microcode updates for the N5105
- Admin tools: `git`, `vim`, `htop`, `sysstat`, `iptraf`, `parted`, `zip/unzip`, `wget`

## Customization: subscription-nag removal

`/etc/apt/apt.conf.d/no-nag-script` contains a `DPkg::Post-Invoke` hook that
patches `/usr/share/javascript/proxmox-widget-toolkit/proxmoxlib.js` after
every package operation, suppressing the "No valid subscription" popup in
the web UI. It re-applies itself automatically whenever
`proxmox-widget-toolkit` is updated (the `apt --reinstall install
proxmox-widget-toolkit` in the apt history was a manual restore of the
original file at some point). To undo: delete the hook file and reinstall
`proxmox-widget-toolkit`.

## Services worth knowing about

**Proxmox stack:** `pveproxy` (web UI :8006), `pvedaemon`, `pvestatd`,
`pvescheduler`, `pve-cluster` (pmxcfs — runs even single-node),
`pve-firewall` + `proxmox-firewall` (nftables), `spiceproxy`, `qmeventd`,
`ksmtuned`, `smartmontools`, `postfix` (local mail for PVE notifications).

**From the desktop install:** `sddm` (graphical login), NetworkManager,
CUPS + cups-browsed, Avahi, PackageKit, power-profiles-daemon, fwupd,
ModemManager, netavark-firewalld-reload (Podman networking plumbing).

**Remote access:** OpenSSH enabled; root shell access in use.

## Users

- `root` — PVE administration
- `nate` — desktop login (KDE)
- `ceph` — system account from PVE's bundled Ceph packages (Ceph not in use)

## Known quirks / to-do

- [ ] Disable the enterprise repo (no subscription) to clean up `apt update`.
- [ ] `sdb` ("STORAGE DEVICE", 0 B) shows up — a card reader or dead USB
      device; harmless but appears in disk listings.
- [ ] VM 102 has an `unused0` disk on `SSD` that can be deleted to free space.
- [ ] No off-host backups — everything (VM disks *and* vzdump backups) lives
      inside this one chassis.
- [ ] pfSense (VM 100) lost its `onboot: 1` flag after snapshot rollbacks —
      re-enable if it should autostart.
