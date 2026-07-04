# Desktop GUI on the hypervisor (KDE Plasma)

Proxmox VE normally runs headless — you manage it through the web UI at
port 8006. On `pve-01`, a full **KDE Plasma 6.3 desktop** has been installed
*directly on the Proxmox host*, so the machine doubles as a workstation:
plug in a monitor/keyboard and you get a graphical login.

## How it was installed

From the apt history, the desktop came from Debian's standard KDE task
metapackage (it was installed, removed once, and reinstalled):

```bash
apt install task-kde-desktop   # pulls in Plasma, SDDM, Firefox ESR, etc.
apt install virt-viewer        # SPICE client for connecting to VM consoles
```

`task-kde-desktop` is the same metapackage the Debian installer uses when you
tick "KDE Plasma" — so this is effectively a stock Debian 13 KDE desktop
layered on top of the PVE install.

## What that pulled in

| Component | Detail |
|---|---|
| **Display manager** | SDDM (`sddm.service`, enabled — graphical login on boot) |
| **Desktop** | KDE Plasma 6.3.6, both Wayland (`plasma.desktop`) and X11 (`plasmax11.desktop`) sessions |
| **Browser** | Firefox ESR 140 (with `plasma-browser-integration`) |
| **Network GUI** | NetworkManager (runs alongside Proxmox's ifupdown2 — see caveats) |
| **SPICE client** | `virt-viewer` — used to open VM consoles from the desktop |
| **Desktop plumbing** | CUPS printing, Avahi mDNS, PackageKit/Discover, power-profiles-daemon, fwupd, ModemManager |

The desktop user account is **`nate`** (the host also has the standard
`ceph` system user from PVE's Ceph packaging).

## Typical workflow

1. Log into Plasma as `nate` at the SDDM greeter.
2. Open Firefox → `https://localhost:8006` for the Proxmox web UI.
3. Open VM consoles either via noVNC in the browser or via
   `virt-viewer` / Remote Viewer over SPICE (most VMs use `vga: qxl` +
   SPICE audio specifically to make this pleasant).

## Caveats of a GUI on a hypervisor

- **NetworkManager vs. ifupdown2**: Proxmox manages `/etc/network/interfaces`
  itself. NetworkManager (installed by the KDE task) must not be allowed to
  touch the bridges/NICs, or the web UI's network config will fight it. On
  this host NetworkManager only holds Wi-Fi profiles (`GL-AXT1800-ef3`,
  `GL-AXT1800-ef3-5G`, `IOT`) — the wired NICs stay under PVE control.
- **RAM/CPU**: Plasma idles at a few hundred MB of RAM; on a 32 GB box with
  ~30 GB allocatable to VMs this is an acceptable trade for a lab.
- **Attack surface**: extra services (CUPS, Avahi, PackageKit) run on the
  hypervisor. Fine for a homelab behind pfSense; not something to do in
  production.
- **Upgrades**: `task-kde-desktop` adds hundreds of packages to
  `apt full-upgrade` runs during PVE major upgrades. Read upgrade output
  carefully — package conflicts between Debian desktop packages and PVE
  packages are the most likely breakage point.
