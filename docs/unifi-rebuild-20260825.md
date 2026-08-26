# UniFi (R2D2 / UDR) rebuild — 2026-08-25

Full session: client cleanup, VLAN segmentation, WireGuard split-tunnelling,
and the gotchas that cost the most time. Router is a **UniFi Dream Router
(UDR)**, `192.168.1.1`, SSH alias `unifi-1.1` (root + `id_ed25519_routers`).

## Access — two different user stores (this confused me for a while)

| Store | Who | Used for |
|---|---|---|
| Linux `/etc/passwd` | `root`, `ui` | SSH to the box |
| UniFi OS console admins (mongo `ace.admin`) | `pve01-automation` | the API |

Grepping `/etc/passwd` for `pve01-automation` returns nothing and is
**not** evidence the account is missing — it lives in mongo, has no email /
`ubic_name` (local-only, no cloud identity), and authenticates the API.
Credential: `/etc/unifi-automation.auth` (root, 0600). Helper written this
session: **`/root/uni.sh`** — `source` it, then `api METHOD PATH [BODY]`.

> Watch the CSRF extraction: `awk "{print $2}"` inside double quotes lets the
> *outer* shell eat `$2`, so awk prints the whole header and every write
> returns **403**. Use single quotes.

## Topology reality

- The UDR is **not** the internet edge. Its WAN (`eth4`) is `192.168.2.110`,
  default route `192.168.2.1` — it sits behind the GL-MT2500, which does the
  PPPoE to Telekom. Double-NAT.
- The UDR has **two radios only** (`ng` 2.4 + `na` 5). No 6 GHz — any
  "third band missing" symptom is a BE9300 issue, not this box.
- APs: R2D2, Basement-Express (wired), U6 Lite (wired), AC-Lite-Mid-Floor
  (wired), **Mid-Express (wireless mesh — will not carry tagged VLANs)**.

## Final layout

| SSID | VLAN | Network | Exit |
|---|---|---|---|
| `Allianz` | 1 | `192.168.1.0/25` | native Telekom (VPN route built but **disabled**) |
| `Lambeau` | 40 | `192.168.40.0/24` | Ashburn WireGuard, **fail-closed** |
| `IOT` | 20 | `192.168.20.0/24` | native Telekom |

`IOT` keeps its original name + PSK deliberately, so IoT gear reconnects with
no re-onboarding; only the VLAN changed. Its settings are max-compatibility
on purpose: **WPA2 only, no WPA3/transition, PMF disabled, fast-roaming off.**

## WireGuard on UniFi — three real gotchas

1. **Field name is `x_wireguard_private_key`** (not `..._client_...`). Found
   by `strings /usr/lib/unifi/lib/unifi | grep -i wireguard`, after four
   wrong guesses returned `api.err.WireguardMissingPrivateKey`.
2. **Individual fields aren't enough** — setting the key alone then fails
   `api.err.WireguardMissingInterfaceDns`. Use
   `wireguard_client_mode: "file"` and pass a whole `.conf` in
   `wireguard_client_configuration_file`; DNS/MTU ride along inside it.
3. **UniFi refuses two clients whose interface addresses overlap**
   (`api.err.SubnetOverlapped`). Surfshark hands *every* peer the same
   `10.14.0.2/24`, so the second always collides. Fix: `/32` on both and a
   distinct host (`10.14.0.2/32`, `10.14.0.3/32`). Verified empirically that
   Surfshark does **not** pin the address to the key — `10.14.0.3` passes
   traffic fine. Note 9.1 sidesteps this entirely by running three tunnels on
   one key at `10.14.0.2/24`, which UniFi will not allow.

Credentials came from the GL routers' existing Surfshark peer list
(`uci show wireguard` on 2.1/9.1 — 71 peers incl. `de-fra`, `us-ash`,
`ch-zur`). No Surfshark login needed. Both handshook on first try; the
assumption that they were stale was wrong.

## ⚠️ Policy routing silently breaks LAN-to-LAN (the big one)

UniFi traffic routes mark traffic with
`! --match-set UBIOS_local_network dst`, which sounds like it protects local
traffic. **It only covers the UDR's own networks:**

```
UBIOS4local_network: 192.168.20.0/24, 192.168.4.0/24, 192.168.40.0/24, 192.168.1.0/25
```

`192.168.9.0/24` (pve-01 + the whole media stack) is **not** in it, so client
traffic to it was marked and pushed into the Frankfurt tunnel, where
Surfshark drops RFC1918. Effect: clients on VLAN 1 could not reach
`192.168.9.11`, and TVs would have lost Jellyfin.

**Testing note that matters:** verifying this from the UDR itself gives a
false pass — router-originated traffic bypasses PBR entirely. The same flaw
makes `curl --interface` on the router useless for checking VPN egress. Test
from a real client, or read `conntrack` / the mangle counters.

Mitigation applied (runtime, **may not survive a re-provision**):

```
for t in 178 179; do for n in 192.168.9.0/24 192.168.2.0/24 192.168.3.0/24 192.168.8.0/24; do
  ip route replace $n via 192.168.2.1 dev eth4 table $t; done; done
```

Static routes added via `/rest/routing` land in `main` only — marked traffic
never consults `main`, so they do **not** fix this on their own.

## Why the German VPN is disabled

Routing VLAN 1 through Frankfurt swaps a **residential** German Telekom IP
for a **datacenter** German IP: same geolocation, but worse for banking and
government portals, more CAPTCHAs, throughput capped by the UDR's MT7622, and
it introduces the LAN fragility above. The WAN is already German, so the
tunnel bought nothing. Route `Allianz via Frankfurt` is therefore **built,
tested, and left disabled** — re-enable in Settings → Routing → Traffic
Routes. `Lambeau` stays tunnelled because a US exit is genuinely unobtainable
otherwise.

## Cleanup done

- 42 stale clients forgotten (128 → 86). Snapshot with original names:
  `/root/unifi-clients-snapshot-20260825.jsonl`. Cross-checked against 2.1
  and 9.1 first — this saved two devices that were live elsewhere
  (`Living room Fire Stick Wired`, a Shenzhen Ferex device). **3.1 could not
  be enumerated**, so that remains a small blind spot.
- `USW-Flex-Mini-Finley` removed. Device removal is `cmd/sitemgr`, **not**
  `cmd/devmgr` — devmgr returns `rc: ok` and does nothing.
- 4 dead VPN configs (Surf-Lath-UDP, AlbaniaWG, Ashburn, NYC) deleted.
- Stale Guest network deleted — it was disabled yet still held
  `br3 = 192.168.3.1/24`, **conflicting with the GL-BE9300's LAN IP** and
  explaining why `192.168.3.1` was unreachable from pve-01.
- 3.1 now reachable via Tailscale: SSH alias **`glinet-3.1-ts`**
  (`100.82.158.23`).

## Not done / open

- **Inter-VLAN isolation rules are NOT in place.** VLAN 20 can still reach
  VLAN 1 — the VLAN move is done but true segmentation needs firewall rules,
  deliberately deferred rather than applied untested against the media stack.
- `Mid-Express` still wireless-meshed (owner deferring); it will not serve
  tagged VLANs and is the leading suspect for a reported ~21:30 slowdown.
- Erin Laptop, Galaxy-A25-5G, Hisense TV and BIG-GL were on `IOT` at retag
  time and should be moved to `Allianz`.
- No scheduled throttling or content blocking exists on this router
  (0 firewall rules, 0 traffic rules, no bandwidth profiles) — the 21:30
  slowdown is not coming from UniFi config.

## Backout

| Change | Undo |
|---|---|
| `IOT` on VLAN 20 | set wlan `networkconf_id` back to `57ad623e89d584ab10363989` (saved in `/root/.iot-wlan-prev-network`) |
| Allianz/Lambeau SSIDs | delete the two `wlanconf` entries |
| VLANs 20/40, WG tunnels | delete the `networkconf` entries |
| LAN bypass routes | runtime only — clear on reboot/re-provision |
| Forgotten clients | names restorable from the snapshot; devices themselves return on reconnect |

## Reboot persistence test — 2026-08-26 07:33 (owner-approved, executed live)

Rebooted the UDR and diffed a captured baseline against post-reboot state.
Harness: `/root/udr-verify.sh` (baseline|check) and `/root/udr-reboot-test.sh`
(pre-flights for in-progress DVR recordings and refuses to reboot if any are
running, or if that state can't be determined).

**Back in ~120 s via the LAN path.** Tailscale fallback was not needed.

Survived byte-for-byte: both WireGuard tunnels (up and handshaking within
2 min), all SSIDs (`Allianz`, `Lambeau`, `IOT` on VLAN 20), all networks/VLANs,
the IoT firewall zone, Tailscale on the UDR, and the Swiss IPTV egress from
CT105 (unaffected — that path never touches this router).

**Did NOT survive — as predicted:** all 8 LAN-bypass routes in wg policy
tables 178/179. The diff contained nothing else but timestamps. This confirms
the earlier suspicion as fact: those routes are runtime-only.

Durable fix installed: **`/data/on_boot.d/20-lan-bypass.sh`** (the same
`on_boot.d` mechanism Tailscale uses). Two things learned writing it:

- A wg policy table only exists while its traffic route is **enabled**. Table
  178 is currently absent because the Frankfurt route is disabled — that is
  normal, not a fault. A first draft gated its wait-loop on table 178 having
  content and would have stalled ~5 min at every boot; it now checks each
  table independently and skips missing ones.
- Verified live: `table 179 default=1 bypass=1` (active route, bypass applied),
  `table 178 default=0 bypass=0` (disabled route, correctly skipped).

Also fixed this session: **Tailscale on the UDR was dead again after the
2026-08-19 firmware build.** Root cause was NOT the OS-version check from the
previous incident — that `'5'` patch is still intact. The upgrade wiped the
binaries (which live outside `/data`) while `/data/tailscale/` config and
`tailscaled.state` survived, so `manage.sh install` + `start` restored it with
the same node identity and IP (`100.114.159.40`), no re-auth.
`tailscale-install.timer` is enabled and is meant to auto-repair this; it did
not fire, which is the thing to investigate if it recurs.

**Inter-VLAN isolation** was implemented via the zone-based firewall: a new
`IoT` zone holds VLAN 20 + VLAN 40. UniFi's auto-generated policies are
correct for the security direction — `IoT -> Internal` BLOCK, `IoT -> External`
ALLOW, `IoT -> IoT` BLOCK (so the TV is walled off from IoT gear). One gap
remains: `Internal -> IoT` is also BLOCK, which breaks casting to the Nest
devices. Predefined zone policies are **not editable via the API**
(`api.err.FirewallPolicyNotFound`), and creating a custom override failed
schema validation — so that one allow rule must be added in the UI:
Settings -> Security -> Firewall -> Policies, Internal -> IoT, Allow.
