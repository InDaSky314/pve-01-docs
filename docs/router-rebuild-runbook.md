# Flint 2 (GL-MT6000) — backup & rebuild runbook

Written after the 2026-07-25 factory reset, which cost several hours of
manual rebuilding. Everything below is verified against the live device,
not assumed.

## Backups

`/root/bin/router-backup.sh` on pve-01 — weekly via
`router-backup.timer` (Sun 03:30 + jitter, inside the 01:00–05:00
maintenance window), plus on demand. Snapshots land in
`/root/router-backups/<UTC-timestamp>/`, 10 kept.

| File | Contents |
|---|---|
| `config.tar.gz` | Native `sysupgrade -b` archive — the restorable payload |
| `packages-added.txt` | opkg packages added since factory ROM (**not** in the archive) |
| `summary.txt` | Human-readable config summary; inspect without unpacking |

Verified as captured: **WireGuard private keys** (`/etc/config/wireguard`,
~28 KB — so a restore needs **no VPN-provider re-login**), all OpenVPN
provider profiles + auth, `route_policy` tunnel/MAC assignments,
`gl-client` display-name aliases, DHCP reservations, wireless
SSID/password, syslog forwarding, and pve-01's `dropbear`
`authorized_keys`.

⚠️ Snapshots contain **secrets** (WG private keys, VPN credentials,
admin password hash). They are 600/700 and live **outside** the git
repo. Never commit them.

---

## Step 1 — re-establish initial access (the part that bites)

After a factory reset the router is **not** where you left it:

| | Factory default | Our customized value |
|---|---|---|
| LAN IP | **`192.168.8.1`** | `192.168.9.1` |
| DHCP pool | `192.168.8.100–249` | `192.168.9.100–249` |
| SSID | `GL-MT6000-<suffix>` | (see `summary.txt`) |
| WiFi password | printed on device label | (restored from archive) |
| Admin URL | `http://192.168.8.1` | `http://192.168.9.1` |
| SSH | password only, no trusted keys | pve-01 key installed |

(Confirmed from `/rom/etc/board.d/03_gl_network` and
`/rom/etc/uci-defaults/network_gl`.)

**The catch:** pve-01 sits at `192.168.9.11`, so once the router is back
on `192.168.8.1` the host is on a different subnet and **cannot reach
it**. Pick one:

- **Easiest — use a laptop/phone on DHCP.** It gets a `192.168.8.x`
  lease and can reach `http://192.168.8.1` directly. Do steps 2–3 from
  there.
- **Temporarily alias pve-01 onto the factory subnet** (keeps existing
  connectivity intact; remove it afterwards):
  ```bash
  ip addr add 192.168.8.2/24 dev vmbr0      # add
  ssh root@192.168.8.1                       # ... do the restore ...
  ip addr del 192.168.8.2/24 dev vmbr0      # remove when done
  ```
- **Or** set the LAN IP straight back to `192.168.9.1` in the setup
  wizard before anything else, then work from pve-01 as normal.

**Step 2 — set an admin password.** The UI forces this on first visit
and SSH will not accept a password until it is set. Use the same
password for consistency; it is replaced by the archive's hash anyway
once restored.

**Step 3 — clear the stale SSH host key.** The host key is regenerated
by the reset, so pve-01 will refuse to connect until the old one is
dropped:
```bash
ssh-keygen -f /root/.ssh/known_hosts -R 192.168.9.1
# and, if you used the factory address:
ssh-keygen -f /root/.ssh/known_hosts -R 192.168.8.1
```

---

## Step 4 — restore the config

Either upload `config.tar.gz` via the UI
(**System → Backup / Restore → Restore**), or from a shell:

```bash
SNAP=/root/router-backups/<timestamp>
ROUTER=root@192.168.8.1     # factory address; 192.168.9.1 once restored

# dropbear here ships NO sftp-server, so scp fails — stream with cat
cat $SNAP/config.tar.gz | ssh $ROUTER "cat > /tmp/restore.tar.gz"
ssh $ROUTER "sysupgrade -r /tmp/restore.tar.gz && reboot"
```

The router reboots and comes back on **`192.168.9.1`** with tunnels,
policy routing, client names, DHCP reservations, wireless, syslog
forwarding and pve-01's SSH key all restored. Reconnect at the new
address. If you added the `192.168.8.2` alias, remove it now.

## Step 5 — reinstall packages

`sysupgrade -r` restores configuration only. The exporter's *config*
(including the critical `listen_interface='*'`) comes back with the
archive, but the binaries do not:

```bash
ssh root@192.168.9.1 "opkg update"
ssh root@192.168.9.1 "opkg install \
  prometheus-node-exporter-lua \
  prometheus-node-exporter-lua-wifi_stations \
  prometheus-node-exporter-lua-netstat \
  prometheus-node-exporter-lua-openwrt \
  prometheus-node-exporter-lua-nat_traffic \
  prometheus-node-exporter-lua-textfile \
  prometheus-node-exporter-lua-uci_dhcp_host \
  nlbwmon luci-app-nlbwmon"
ssh root@192.168.9.1 "/etc/init.d/prometheus-node-exporter-lua enable
                      /etc/init.d/prometheus-node-exporter-lua restart"
```

See `packages-added.txt` for the full delta (27 entries as of
2026-07-25; the rest are firmware-side, not ours).

---

## Step 6 — verify. Do not trust the status indicators.

**On 2026-07-25 both the Clients page and the
`vpn-client.get_vpn_using_status` RPC reported the scraper as
VPN-protected while its traffic was actually egressing on the bare
WAN.** The only trustworthy test is comparing real egress IPs — each
must differ from the bare-WAN reference:

```bash
curl -s https://api.ipify.org                       # pve-01 -> Primary Tunnel
pct exec 105 -- curl -s https://api.ipify.org        # CT105  -> media-core(ch)
pct exec 108 -- wget -qO- https://api.ipify.org      # CT108  -> Tunnel 1
ssh root@192.168.9.1 "curl -s https://api.ipify.org" # bare WAN reference
```

Then confirm the firewall ipsets are populated — **an empty ipset fails
OPEN; it does not blackhole:**

```bash
ssh root@192.168.9.1 "for s in \$(ipset -n list | grep src_mac); do \
  printf '%s: ' \$s; ipset list \$s | grep 'Number of entries'; done"
```

Any tunnel with MACs assigned must be non-zero. Also check
`http://192.168.9.1:9100/metrics` returns 200 from pve-01, and that
Prometheus shows the `router` target `up`.

Reference throughput (2026-07-25, ~104 Mbps direct-WAN ceiling):
media-core(ch) 96.7 Mbps / 33 ms · Primary 84.0 / 92 ms · Tunnel 1
83.5 / 102 ms.

---

## Gotchas, learned the hard way

**`from_mac` must be a UCI `list`, never an `option`.** Assigning a
single MAC with `uci set route_policy.@rule[N].from_mac=...` stores a
scalar and breaks two things at once:
- the Lua backend runs `ipairs()` over it → HTTP 500 on
  `vpn-client.get_vpn_using_status` → *"Unknown error occurred. Please
  check the network environment or reboot the device."* on the Clients
  page (looks like a network fault; is actually malformed config)
- the ipset never populates → **silent VPN leak**

Use `uci add_list`, then verify the raw file:
```bash
ssh root@192.168.9.1 "grep from_mac /etc/config/route_policy"   # want: list from_mac '...'
```

**`rtp2.sh` is the correct CLI apply path for route_policy.**
`/etc/init.d/network reload` and `ifup`/`ifdown` do **not** repopulate
ipsets — config and runtime silently diverge. Boot applies it via
`/etc/rc.d/S95vpn-client`.
```bash
ssh root@192.168.9.1 "/usr/bin/rtp2.sh apply"
```

**Creating tunnels must go through the web UI**, not raw UCI — the UI
maintains backing state (numeric `group_id`/`peer_id` + profile files)
that hand-written UCI won't produce. *Editing MACs* on an
already-UI-created tunnel from CLI is fine, given `add_list` +
`rtp2.sh apply`.

**Client display names live in `/etc/config/gl-client` (`alias`).** The
`name` column in `/etc/oui-tertf/client.db` is volatile — recomputed
from DHCP hostnames on every refresh, so direct SQL edits are wiped on
service restart. LXC guests don't broadcast a hostname, so they show as
"Unknown" until an alias is set (UI → client → Modify, or that file).

**Prometheus exporter needs `listen_interface='*'`.** A named interface
(e.g. `lan`) leaves it bound to `127.0.0.1`, unreachable by Prometheus.

**Don't hardcode tunnel interface names in dashboards.** The
"Network: Router & Tunnels" panels pinned
`wgclient1|ovpnclient1|ovpnclient2`; after the rebuild Tunnel 1 became
WireGuard (`wgclient2`), so a retired interface was charted and the new
one was invisible. Now uses `wgclient.*|ovpnclient.*`.

**Latent trap:** ROM defaults the *guest* network to `192.168.9.1` —
the same address as our customized LAN. Guest is disabled
(`network.guest.disabled='1'`), so it's inert, but enabling it without
renumbering would collide.

## Known gap

`wg-snapshot.sh` polls only `wgclient1`. With two WireGuard tunnels
now, `wgclient2` (scraper) has no health history — notable because
that's the tunnel whose multi-hour outage on 2026-07-24 couldn't be
reconstructed for lack of history.
