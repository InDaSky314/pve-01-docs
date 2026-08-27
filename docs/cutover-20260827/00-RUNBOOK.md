# pve-01 / 3.1 cutover runbook — 2026-08-27

3.1 (GL-BE9300) takes over **two** roles at once: PPPoE edge router (from the
MT2500) and LAN gateway for 192.168.9.0/24 (from the MT6000). The Proxmox server
physically relocates next to 3.1. The MT6000 stays in place on a new address,
serving the TV/Chromecast corner.

**Claude runs on pve-01 and goes away the moment you shut it down.** Everything
below is written so you can finish alone.

## Topology

Before:
```
ONT ──PPPoE──> MT2500 2.1 (192.168.2.1)
                  ├── UDR (192.168.1.1) ── MT6000 9.1 (192.168.9.1) ── pve-01
                  └── BE9300 3.1 (192.168.3.1)
```
After:
```
ONT ──PPPoE──> BE9300 3.1 (192.168.9.1)  ← edge + LAN gateway
                  ├── pve-01 + containers (192.168.9.x)
                  ├── UDR (192.168.1.1)
                  └── MT6000 (192.168.5.1)  ← TV / Chromecast
   MT2500 2.1 — retired from the edge
```

## Addressing decisions

| Device | Was | Becomes |
|---|---|---|
| BE9300 "3.1" LAN | 192.168.3.1 | **192.168.9.1/24** |
| BE9300 WAN | dhcp (192.168.2.241) | **PPPoE** (Telekom) |
| MT6000 "9.1" LAN | 192.168.9.1 | **192.168.5.1/24** |
| MT6000 guest / iot (disabled) | 192.168.9.1 / 192.168.10.1 | 192.168.15.1 / 192.168.16.1 |
| BE9300 guest / iot | 192.168.30.1 / 192.168.10.1 | unchanged |

### On the 192.168.10.1 "collision" — verified, and it is not an active one

Measured on both routers rather than read off config:

- **BE9300 (3.1): IoT is live and load-bearing.** `br-iot` UP at 192.168.10.1/24,
  members `wlan06` + `wlan16` — both the **WALDO** SSID — one active lease, and
  it is the interface the `WALDO -> wgclient2` (Frankfurt) policy rule binds to.
  **Nothing in this cutover touches it.**
- **MT6000 (9.1): IoT and guest are dead.** Both `disabled=1`, neither bridge
  instantiates, zero leases, only `br-lan` is live. The four VAPs referencing
  them are all disabled with stock GL names.

So the duplicate address never actually conflicts — 9.1's IoT does not exist at
runtime. Same for `lan`/`guest` both reading 192.168.9.1 on the MT6000. These
are latent config duplicates, not live faults, and they are **not blockers**.

**DONE ALREADY (2026-08-27, before the cutover).** The disabled interfaces on
the MT6000 have been re-addressed live: guest 192.168.9.1 -> **192.168.15.1**,
iot 192.168.10.1 -> **192.168.16.1**, both pinned `disabled=1`. Config-only
edit, no reload — verified afterwards that uptime was unchanged (no reboot),
only `br-lan` was live, WAN and internet were fine, 8 wifi VAPs and 3 tunnels
still up, and container egress was untouched (media stack still Zurich, pve-01
host still Ashburn).

Backup: `/root/cfg-backups/network.pre-readdress-20260827-194613` on the MT6000.
Rollback: `cp /root/cfg-backups/network.pre-readdress-20260827-194613 /etc/config/network`

`91-mt6000-reip.sh` still sets these values (it is idempotent), so re-running it
during the cutover changes nothing here — it will only do the LAN re-IP.

The owner has decided **not** to delete these networks.

**Deliberately not deleting them during the cutover.** On the MT6000 those two
networks are referenced by 5 forwardings (`guest->wan`, `iot->wan`,
`guest->wgclient1/2/3`), 6+ firewall rules including named sections such as
`guest_drop_leaked_dns`, and `route_policy.global.append_source_if='iot'`.
Unwinding ~12 objects on a router that is also changing role the same day is how
you end up with dangling references and a wifi outage. Full removal exists in `92-mt6000-remove-guest-iot.sh` but the owner has chosen
**not** to delete these networks. The script is kept only as a reference; do not
run it unless that decision changes.

PPPoE is **untagged on eth0** — no VLAN 7 needed on this line. Credentials are
already staged at `/root/cutover/.pppoe-creds` (mode 600), copied from the
MT2500. You do not need to type them.

## Egress behaviour to preserve

Measured on the live system before the move:

| Host | Exit | Where |
|---|---|---|
| media-core, jellyfin-vod, jellyfin-npvr | 156.146.62.40 | Zürich (Swiss) |
| scraper | 172.216.8.6 | Buffalo |
| log-server, **pve-01 host** | 149.102.227.111 | Ashburn |

After the move, on 3.1: media stack stays on **Zürich**; scraper, log-server and
the pve-01 host all go to **Ashburn** (Buffalo is retired — the new Ashburn
WireGuard tunnel replaces it, which is what you asked for). `10-prestage.sh`
sets this up.

## Order of operations

**1. Prestage — ALREADY DONE (2026-08-27 19:52, verified)**
```sh
ssh root@192.168.3.1 'sh /root/cutover/10-prestage.sh'   # already run
```
Created the 7 Proxmox DHCP reservations on the 192.168.9.x scope and put
scraper + log-server + pve-01 host on the Ashburn tunnel. Verified afterwards:
LAN still 192.168.3.1, WAN still dhcp, no reboot, dnsmasq up, 20 wifi VAPs, all
four tunnels re-checked by exit IP and geolocation (Zürich / Frankfurt /
Ashburn / New York), and container egress on the MT6000 unchanged.

It is idempotent, so re-running it is harmless if you want to confirm.

Backup taken: `/root/cfg-backups/etc-config-precutover-20260827-195200.tar.gz`

**Also already done:** the MT6000's disabled guest/iot interfaces were
re-addressed to 192.168.15.1 / 192.168.16.1 so they can never collide with
3.1's guest (192.168.30.1) or iot (192.168.10.1). Step 3 below will therefore
only perform the LAN re-IP.

**2. Shut the Proxmox server down**
```sh
ssh root@192.168.9.11 'shutdown -h now'
```
Stop containers first if you want them clean. This is where Claude drops off.

**3. Move the MT6000 off 192.168.9.1** — run *on the MT6000*:
```sh
ssh root@192.168.9.1 'sh /root/cutover/91-mt6000-reip.sh'
```
It reboots onto **192.168.5.1** and re-scopes the TV reservations
(DE-Chromecast, LR-FireStick) from 192.168.9.x to 192.168.5.x.

**4. Recable**
- 3.1 WAN → the ONT port the MT2500's WAN used
- **Unplug the MT2500 from the ONT** — two PPPoE sessions will fight over one line
- pve-01 → a LAN port on 3.1
- UDR WAN → a LAN port on 3.1
- MT6000 WAN → a LAN port on 3.1

**5. Cut over** — on 3.1's console or SSH:
```sh
sh /root/cutover/20-cutover.sh
```
Prompts for a `YES`, then switches WAN to PPPoE, LAN to 192.168.9.1, and reboots.

**6. Verify** — renew your DHCP lease, then:
```sh
ssh root@192.168.9.1 'sh /root/cutover/30-verify.sh'
```
Checks uptime (proof it rebooted), the PPPoE session and public IP, internet and
DNS, all wifi VAPs including MLO and 6 GHz, and every tunnel **by exit IP**.

**7. Power on the Proxmox server**, then confirm from pve-01:
```sh
for c in 105 107 108 111 112; do
  echo -n "CT $c: "; pct exec $c -- wget -qO- https://api.ipify.org; echo
done
```
Expect Zürich for 105/111/112, Ashburn for 107/108.

## If it goes wrong

```sh
sh /root/cutover/90-rollback.sh          # 3.1 back to 192.168.3.1 + dhcp WAN
```
then plug the MT2500 back into the ONT — that alone restores internet for the
whole house, independent of anything else.

Full config snapshots are in `/root/cfg-backups/` on each router
(`etc-config-*.tar.gz`), taken automatically by every script above.

## Known risks

- **PPPoE session hold.** Telekom usually reconnects in seconds, but the old
  session can linger a few minutes. If `30-verify.sh` shows the session down,
  wait 5 minutes before assuming failure. The MT2500 must be unplugged.
- **Two role changes at once.** If something breaks, you cannot tell which half
  did it. The rollback path deliberately restores *both* halves together.
- **Tailscale on 3.1 has died across reboots before.** With the LAN at
  192.168.9.1 and you at the rack, wired access is the reliable path — do not
  depend on Tailscale during the cutover.
- **`wifi reload` is not enough on this box.** If a radio looks wrong after the
  move, reboot rather than reloading. See `docs/glinet-api-cli-runbook.md`.
- **UDR double-NAT.** The UDR keeps its own 192.168.1.0/25 behind 3.1, same as
  it was behind the MT2500. No change in behaviour, but it is still double NAT.

## Still unproven

Per-SSID VPN egress on 3.1 has never been tested with a real client. The rules
match on `iifname`, which only applies to forwarded traffic, so nothing run from
the router itself proves anything. After the cutover, connect a phone to each
SSID and check the exit IP: GIOT → New York, WALDO → Frankfurt, Open-Fields →
your own public IP.
