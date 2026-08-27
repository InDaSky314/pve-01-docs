#!/bin/bash
# ============================================================================
#  pve-01 / 3.1 cutover driver  -- run this ON YOUR MACBOOK
#  Walks the whole cutover: tells you which Wi-Fi to join, waits for the box
#  to answer, SSHes in, runs the right script, then gives the next step.
#
#  Resumable: it remembers the last completed step in ~/.cutover-state.
#  Run it again any time; it offers to pick up where you left off.
# ============================================================================
set -u

STATE="$HOME/.cutover-state"
LOG="$HOME/cutover-$(date +%Y%m%d-%H%M%S).log"

B=$'\033[1m'; R=$'\033[0m'; GRN=$'\033[32m'; YEL=$'\033[33m'; RED=$'\033[31m'; CYA=$'\033[36m'

say()  { printf "%s\n" "$*" | tee -a "$LOG"; }
hdr()  { printf "\n${B}${CYA}=== %s ===${R}\n" "$*" | tee -a "$LOG"; }
ok()   { printf "${GRN}  ok${R}  %s\n" "$*" | tee -a "$LOG"; }
warn() { printf "${YEL}  !!${R}  %s\n" "$*" | tee -a "$LOG"; }
bad()  { printf "${RED}  XX${R}  %s\n" "$*" | tee -a "$LOG"; }

pause() { printf "\n${B}%s${R}" "${1:-Press RETURN to continue...}"; read -r _; }

confirm() { # confirm "question"  -> 0 if yes
  local a
  printf "\n${B}%s [y/N] ${R}" "$1"; read -r a
  case "$a" in y|Y|yes|YES) return 0 ;; *) return 1 ;; esac
}

# ---------------------------------------------------------------- wifi helpers
wifi_dev() {
  networksetup -listallhardwareports 2>/dev/null \
    | awk '/Wi-Fi|AirPort/{getline; print $2; exit}'
}

current_ssid() {
  local d; d=$(wifi_dev); [ -z "$d" ] && { echo ""; return; }
  local s
  s=$(networksetup -getairportnetwork "$d" 2>/dev/null | sed -n 's/^Current Wi-Fi Network: //p')
  [ -z "$s" ] && s=$(ipconfig getsummary "$d" 2>/dev/null | awk -F' SSID : ' '/ SSID : /{print $2; exit}')
  printf "%s" "$s"
}

# need_network "SSID" "why"
need_network() {
  local want="$1" why="$2" have
  have=$(current_ssid)
  hdr "Network needed: $want"
  say "  reason: $why"
  say "  currently on: ${have:-<unknown / wired>}"
  if [ "$have" = "$want" ]; then ok "already on $want"; return 0; fi
  say ""
  say "  ${B}Join '$want' in the macOS Wi-Fi menu now${R} (or plug into that router's LAN port)."
  pause "Press RETURN once connected..."
  have=$(current_ssid)
  if [ "$have" = "$want" ]; then ok "on $want"; else warn "macOS reports '${have:-unknown}' - continuing anyway, reachability is the real test"; fi
}

# ------------------------------------------------------------- reachability
# macOS ping: -t is a deadline in SECONDS (not Linux -W)
alive() { ping -c 1 -t 2 "$1" >/dev/null 2>&1; }

wait_host() { # wait_host IP LABEL [max_seconds]
  local ip="$1" label="$2" max="${3:-300}" n=0
  printf "  waiting for %s (%s) " "$label" "$ip"
  while [ "$n" -lt "$max" ]; do
    if alive "$ip"; then printf "\n"; ok "$label is up at $ip"; return 0; fi
    printf "."; sleep 3; n=$((n+3))
  done
  printf "\n"; bad "$label did not answer at $ip after ${max}s"
  return 1
}

wait_gone() { # wait_gone IP LABEL [max]
  local ip="$1" label="$2" max="${3:-120}" n=0
  printf "  waiting for %s to go down " "$label"
  while [ "$n" -lt "$max" ]; do
    alive "$ip" || { printf "\n"; ok "$label is down (reboot underway)"; return 0; }
    printf "."; sleep 2; n=$((n+2))
  done
  printf "\n"; warn "$label never stopped answering - it may not have rebooted"
  return 1
}

# ------------------------------------------------------------------ ssh
SSH_OPTS="-o ConnectTimeout=10 -o StrictHostKeyChecking=accept-new"

# Host keys WILL change: 3.1 takes 192.168.9.1 (previously the MT6000) and the
# MT6000 moves to 192.168.5.1. That produces a scary MITM warning which is
# expected here. This shows you the situation and only clears the entry you agree to.
fix_hostkey() { # fix_hostkey IP
  local ip="$1"
  if ssh-keygen -F "$ip" >/dev/null 2>&1; then
    warn "known_hosts already has an entry for $ip (from the OLD device on that address)"
    say  "  its fingerprint:"
    ssh-keygen -F "$ip" -l 2>/dev/null | sed 's/^/    /' | tee -a "$LOG"
    say  "  the device now answering there is a DIFFERENT box, so the key legitimately differs."
    if confirm "Remove the stale known_hosts entry for $ip?"; then
      ssh-keygen -R "$ip" >/dev/null 2>&1 && ok "removed stale entry for $ip"
    else
      warn "left it - ssh will refuse to connect until you clear it"
    fi
  fi
}

rsh() { # rsh IP "command"
  local ip="$1"; shift
  say "  ${B}ssh root@$ip${R} :: $*"
  ssh $SSH_OPTS "root@$ip" "$@" 2>&1 | tee -a "$LOG"
  return "${PIPESTATUS[0]}"
}

rsh_tty() { # interactive (for scripts that prompt for YES)
  local ip="$1"; shift
  say "  ${B}ssh -t root@$ip${R} :: $*"
  ssh $SSH_OPTS -t "root@$ip" "$@" 2>&1 | tee -a "$LOG"
  return "${PIPESTATUS[0]}"
}

# ------------------------------------------------------------------ state
save_step() { echo "$1" > "$STATE"; }
last_step() { [ -f "$STATE" ] && cat "$STATE" || echo 0; }

# ============================================================================
#  STEPS
# ============================================================================

step1_preflight() {
  hdr "STEP 1 - Preflight"
  say "Prestage was already run and verified on 2026-08-27. This just re-checks."
  need_network "Open-Fields" "3.1's base LAN - it is the one SSID that survives the cutover"
  wait_host 192.168.3.1 "BE9300 (3.1)" 60 || { bad "cannot reach 3.1 - fix that before going on"; return 1; }
  rsh 192.168.3.1 'echo "  lan=$(uci -q get network.lan.ipaddr) wan=$(uci -q get network.wan.proto)"; \
    echo "  dhcp reservations: $(uci show dhcp | grep -c "@host\[[0-9]*\]=host")  (expect 7)"; \
    echo "  pppoe creds staged: $([ -s /root/cutover/.pppoe-creds ] && echo yes || echo NO)"; \
    echo "  scripts: $(ls /root/cutover/*.sh 2>/dev/null | wc -l)  (expect 6)"'
  say ""
  say "Expect: lan=192.168.3.1, wan=dhcp, 7 reservations, creds staged, 6 scripts."
  confirm "Does that look right?" || { bad "stop here and investigate"; return 1; }
  save_step 1; ok "step 1 done"
}

step2_shutdown_proxmox() {
  hdr "STEP 2 - Shut down the Proxmox server"
  say "${YEL}This is the point of no return for remote help - Claude runs on pve-01.${R}"
  say "Containers are stopped first so nothing is mid-write."
  confirm "Shut down pve-01 now?" || return 1
  wait_host 192.168.9.11 "pve-01" 30 || warn "pve-01 not answering at 192.168.9.11 - is it on another address?"
  rsh 192.168.9.11 'for c in $(pct list | awk "NR>1{print \$1}"); do pct shutdown $c --timeout 60 & done; wait; \
    for v in $(qm list | awk "NR>1{print \$1}"); do qm shutdown $v --timeout 60 & done; wait; \
    sleep 3; pct list; qm list' || warn "container shutdown reported an error - check above"
  confirm "Containers stopped cleanly - proceed to halt the host?" || return 1
  rsh 192.168.9.11 'nohup sh -c "sleep 2; shutdown -h now" >/dev/null 2>&1 &' || true
  wait_gone 192.168.9.11 "pve-01" 180
  say ""
  say "${B}Wait for the chassis lights/fans to stop before unplugging it.${R}"
  pause "Press RETURN once the server is fully powered off..."
  save_step 2; ok "step 2 done"
}

step3_mt6000_reip() {
  hdr "STEP 3 - Move the MT6000 off 192.168.9.1"
  say "It goes to 192.168.5.1 and re-scopes the TV reservations (DE-Chromecast, LR-FireStick)."
  say "Its guest/iot interfaces were already re-addressed, so this only does the LAN change."
  need_network "Big-GL" "the MT6000's own LAN SSID - you must be on IT, not on 3.1"
  wait_host 192.168.9.1 "MT6000" 60 || { bad "cannot reach the MT6000 at 192.168.9.1"; return 1; }
  confirm "Run 91-mt6000-reip.sh (it reboots the MT6000)?" || return 1
  rsh 192.168.9.1 'sh /root/cutover/91-mt6000-reip.sh' || warn "script returned non-zero - read the output"
  wait_gone 192.168.9.1 "MT6000" 120
  say ""
  say "It comes back on ${B}192.168.5.1${R}. Rejoin ${B}Big-GL${R} - your Mac needs a new DHCP lease in 192.168.5.x."
  pause "Press RETURN once you have rejoined Big-GL..."
  fix_hostkey 192.168.5.1
  wait_host 192.168.5.1 "MT6000 (new address)" 240 || warn "not answering yet - give it another minute"
  rsh 192.168.5.1 'echo "  lan=$(uci -q get network.lan.ipaddr)  uptime=$(cut -d. -f1 /proc/uptime)s"; \
    echo "  reservations:"; i=0; while [ $i -lt 20 ]; do m=$(uci -q get dhcp.@host[$i].mac 2>/dev/null); \
    [ -z "$m" ] && break; echo "    $(uci -q get dhcp.@host[$i].ip)  $(uci -q get dhcp.@host[$i].name)"; i=$((i+1)); done'
  save_step 3; ok "step 3 done"
}

step4_recable() {
  hdr "STEP 4 - Recable (nothing automated here)"
  cat <<'CABLE'

    [ ] 3.1 WAN port  ->  the ONT port the MT2500's WAN currently uses
    [ ] UNPLUG the MT2500 from the ONT
          Two PPPoE sessions fight over one line. This is not optional.
    [ ] pve-01        ->  a LAN port on 3.1
    [ ] UDR WAN       ->  a LAN port on 3.1
    [ ] MT6000 WAN    ->  a LAN port on 3.1

    Leave the Proxmox server POWERED OFF for now.

CABLE
  confirm "All cables moved and the MT2500 is unplugged from the ONT?" || return 1
  save_step 4; ok "step 4 done"
}

step5_cutover() {
  hdr "STEP 5 - Cut 3.1 over to PPPoE + 192.168.9.1"
  need_network "Open-Fields" "3.1's base LAN - still 192.168.3.x until the reboot"
  wait_host 192.168.3.1 "BE9300 (3.1)" 90 || { bad "cannot reach 3.1"; return 1; }
  say "20-cutover.sh will ask you to type YES, then reboot the router."
  rsh_tty 192.168.3.1 'sh /root/cutover/20-cutover.sh' || warn "script returned non-zero - read the output"
  wait_gone 192.168.3.1 "3.1 (old address)" 150
  say ""
  say "3.1 comes back as ${B}192.168.9.1${R}. Rejoin ${B}Open-Fields${R} for a fresh lease in 192.168.9.x."
  say "PPPoE can take a few minutes - Telekom sometimes holds the old session."
  pause "Press RETURN once you have rejoined Open-Fields..."
  fix_hostkey 192.168.9.1
  wait_host 192.168.9.1 "3.1 (new address)" 300 || warn "not up yet - wait, then re-run this step"
  save_step 5; ok "step 5 done"
}

step6_verify() {
  hdr "STEP 6 - Verify the cutover"
  wait_host 192.168.9.1 "3.1" 120 || return 1
  rsh 192.168.9.1 'sh /root/cutover/30-verify.sh'
  say ""
  say "Look for: small uptime, PPPoE up with a public IP, internet+dns OK,"
  say "wifi on 2.4/5/6 GHz incl. MLO, and all four tunnels exiting"
  say "${B}Zurich / Frankfurt / Ashburn / New York${R}."
  confirm "Does the output look right?" || { warn "consider: sh /root/cutover/90-rollback.sh  then replug the MT2500"; return 1; }
  save_step 6; ok "step 6 done"
}

step7_proxmox_up() {
  hdr "STEP 7 - Bring the Proxmox server back"
  pause "Power on pve-01, then press RETURN..."
  wait_host 192.168.9.11 "pve-01" 420 || { warn "not up yet - it may need longer, or check its DHCP lease on 3.1"; return 1; }
  say "Starting containers..."
  rsh 192.168.9.11 'for c in 105 107 108 111 112; do pct start $c 2>/dev/null; done; sleep 20; pct list'
  hdr "Container egress check"
  rsh 192.168.9.11 'for c in 105 107 108 111 112; do \
      n=$(pct config $c 2>/dev/null | sed -n "s/^hostname: //p"); \
      ip=$(pct exec $c -- sh -c "wget -qO- https://api.ipify.org 2>/dev/null" 2>/dev/null); \
      printf "  CT %-4s %-16s %s\n" "$c" "$n" "${ip:-<none>}"; done; \
    printf "  %-21s %s\n" "pve-01 host" "$(curl -s --max-time 15 https://api.ipify.org)"'
  say ""
  say "Expect: media-core / jellyfin-vod / jellyfin-npvr -> ${B}Zurich${R} (156.146.x)"
  say "        scraper / log-server / pve-01 host       -> ${B}Ashburn${R}"
  save_step 7; ok "step 7 done"
}

step8_ssid_test() {
  hdr "STEP 8 - Per-SSID VPN egress (never yet tested with a real client)"
  say "Nothing run on the router proves this - the rules match on inbound interface,"
  say "which only applies to forwarded traffic. It needs a real client."
  say ""
  for pair in "GIOT:New York, US" "WALDO:Frankfurt, DE" "Open-Fields:your own public IP"; do
    ssid="${pair%%:*}"; want="${pair#*:}"
    say "  ${B}Join '$ssid' on your phone or Mac${R}, then visit https://ifconfig.me"
    say "     expected: $want"
  done
  say ""
  say "If GIOT or WALDO show your own IP, the per-SSID binding is not working -"
  say "capture which SSID and which IP, that is the useful detail."
  pause "Press RETURN when you have checked all three..."
  save_step 8; ok "step 8 done - cutover complete"
}

step0_keys() {
  hdr "STEP 0 - Passwordless SSH (optional but recommended)"
  say "Without this you will type the router root password at every step - about a"
  say "dozen times, several of them while the network is half-migrated."
  say ""
  if [ ! -f "$HOME/.ssh/id_ed25519.pub" ] && [ ! -f "$HOME/.ssh/id_rsa.pub" ]; then
    warn "no SSH key found on this Mac"
    if confirm "Generate one now (ed25519, no passphrase prompt skipped)?"; then
      ssh-keygen -t ed25519 -f "$HOME/.ssh/id_ed25519" -N "" -C "macbook-cutover" | tee -a "$LOG"
    else
      warn "skipping - you will be prompted for passwords"
      return 0
    fi
  fi
  say "Copying your public key to the routers. You will be asked for each router's"
  say "root password ONCE - after that, no more prompts."
  for ip in 192.168.3.1 192.168.9.1; do
    if alive "$ip"; then
      say "  -> $ip"
      ssh-copy-id -o StrictHostKeyChecking=accept-new "root@$ip" 2>&1 | tail -3 | tee -a "$LOG"
    else
      warn "$ip not reachable right now - skipping (re-run step 0 later if needed)"
    fi
  done
  say ""
  say "After the cutover the addresses change; re-run step 0 to copy the key to"
  say "192.168.5.1 (MT6000) and the new 192.168.9.1 (3.1) if you want it there too."
  save_step 0; ok "step 0 done"
}

# ============================================================================
main() {
  clear
  say "${B}pve-01 / 3.1 cutover driver${R}   $(date)"
  say "log: $LOG"
  local last; last=$(last_step)
  [ "$last" != "0" ] && warn "last completed step was $last"

  say ""
  say "  0  Set up passwordless SSH to the routers (do this first)"
  say "  1  Preflight (re-check prestage)"
  say "  2  Shut down the Proxmox server   <- Claude goes offline here"
  say "  3  MT6000 -> 192.168.5.1"
  say "  4  Recable (checklist)"
  say "  5  Cut 3.1 over to PPPoE + 192.168.9.1"
  say "  6  Verify"
  say "  7  Bring Proxmox back up"
  say "  8  Per-SSID VPN egress test"
  say ""
  printf "${B}Start at which step? [default $((last+1))] ${R}"; read -r s
  [ -z "$s" ] && s=$((last+1))

  while [ "$s" -le 8 ]; do
    case "$s" in
      0) step0_keys            || { bad "step 0 stopped"; break ;} ;;
      1) step1_preflight       || { bad "step 1 stopped"; break ;} ;;
      2) step2_shutdown_proxmox|| { bad "step 2 stopped"; break ;} ;;
      3) step3_mt6000_reip     || { bad "step 3 stopped"; break ;} ;;
      4) step4_recable         || { bad "step 4 stopped"; break ;} ;;
      5) step5_cutover         || { bad "step 5 stopped"; break ;} ;;
      6) step6_verify          || { bad "step 6 stopped"; break ;} ;;
      7) step7_proxmox_up      || { bad "step 7 stopped"; break ;} ;;
      8) step8_ssid_test       || { bad "step 8 stopped"; break ;} ;;
    esac
    s=$((s+1))
    [ "$s" -le 8 ] && { confirm "Continue to step $s?" || { say "stopping here - re-run to resume"; break; } ; }
  done

  hdr "Done for now"
  say "Resume any time:  bash ~/cutover.command"
  say "Full log:         $LOG"
  say ""
  say "${YEL}If anything is broken: ssh root@192.168.9.1 'sh /root/cutover/90-rollback.sh'${R}"
  say "${YEL}and plug the MT2500 back into the ONT - that alone restores the house.${R}"
}

main "$@"
