# BE9300 recovery card — read before meshing anything

One page. Everything else is in `be9300-rebuild-20260905.md` and the
`4.10.0 addendum` of `glinet-api-cli-runbook.md`.

---

## Before you mesh — 60 seconds of insurance

**1. Arm the deadman on 9.1.** This is the single highest-value step; it turns a
lockout from "factory reset" into "wait 5 minutes".

```bash
ssh -F /root/.ssh/config glinet-9.1 \
  '/root/deadman.sh arm 600 /etc/config/network /etc/config/firewall /etc/config/route_policy /etc/config/wireguard'
```

If access survives, disarm it — otherwise cron reverts and reapplies within 60 s:

```bash
ssh -F /root/.ssh/config glinet-9.1 '/root/deadman.sh cancel'
```

**2. Take a fresh declarative snapshot** (safe, diffable — unlike a sysupgrade archive):

```bash
ssh -F /root/.ssh/config glinet-9.1 'uci export' \
  > /root/router-backups/be9300-uci-$(date -u +%Y%m%dT%H%M%SZ)-pre-mesh.txt
chmod 600 /root/router-backups/be9300-uci-*-pre-mesh.txt
```

**3. Confirm both admin paths answer** before you start:

```bash
ssh -F /root/.ssh/config glinet-9.1 'echo LAN ok'
ssh -i /root/.ssh/id_ed25519_routers -o IdentitiesOnly=yes root@100.106.8.35 'echo tailnet ok'
```

---

## Known-good reference

| Item | Path |
|---|---|
| Working 4-tunnel config | `/root/router-backups/be9300-uci-20260905T223729Z-4tunnels-COMPLETE.txt` |
| Slate 7 full backup | `/root/router-backups/slate7-20260905T224539Z/` |
| Slate 7 OpenVPN only | `/root/router-backups/slate7-openvpn-20260905T221213Z.tgz` |
| Router SSH pubkey | `/root/.ssh/id_ed25519_routers.pub` |

**Never restore a `sysupgrade -b` archive onto a reset unit.** That is what
bricked the router on 2026-09-05. Rebuild declaratively from the `uci export`.

---

## If 9.1 goes unreachable

1. **Do not panic-reset.** First check whether it is merely demoted, not dead:
   - Look for it by name in DHCP leases from any reachable router.
   - A meshed member keeps its **web UI on port 80** even with SSH gone.
   - Its old LAN IP / WAN IP / tailnet IP all going quiet is *expected* after
     meshing, not evidence of a brick.
2. **Wait 5 minutes** if the deadman was armed — it self-restores.
3. Only if genuinely bricked: factory reset, then rebuild in this order.

## Rebuild order after a factory reset

1. **Re-add the SSH key** (via web UI → System → Advanced, or LuCI terminal):
   ```
   ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIOjHpCyOPWmWSd8Z5cK6EYMBrdmr9xAS2Ij7WB2r6Nsq pve01-router-mgmt
   ```
   OpenWrt uses `/etc/dropbear/authorized_keys`, not `~/.ssh/`.
2. **Tailscale before anything risky.** `uci set tailscale.settings.enabled='1'`
   then `/etc/init.d/tailscale restart`, then `tailscale up`. Do **not** create a
   `network.tailscale` interface — netifd will cycle it endlessly.
3. **Reinstall the deadman** (`/root/deadman.sh` + the cron line) before touching VPN.
4. **Bridges**: `network.guest.disabled='0'`, `network.iot.disabled='0'`, then
   `ifup guest; ifup iot`.
5. **Tunnels**: create in the web UI (the CLI cannot do the profile bookkeeping),
   clear `killswitch`, scope each rule, then bind subnets with `ip rule`.
6. **OpenVPN**: transplant `/etc/config/ovpnclient` from the Slate 7 backup, and
   restore `/etc/openvpn/profiles/` from the tgz.

## Target state to rebuild to

| Source | Egress |
|---|---|
| pve-01 + Open-Fields | ISP WAN, no VPN |
| CT105 `BC:24:11:59:1F:60`, CT112 `BC:24:11:01:33:58` | Zürich `wgclient1` |
| CT107 `BC:24:11:EF:79:09`, CT108 `BC:24:11:28:55:77` | Ashburn `wgclient3` |
| WALDO `192.168.91.0/24` | Frankfurt `wgclient2` (ip rule 5910 → table 1002) |
| GIOT `192.168.90.0/24` | New York `ovpnclient1` (ip rule 5920 → table 1011) |

Addressing: router at `192.168.X.1` → guest `192.168.X0.1`, IoT `192.168.X1.1`.
