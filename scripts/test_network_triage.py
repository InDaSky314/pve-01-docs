#!/usr/bin/env python3
"""Comprehensive automated verification suite for network-triage tool.
Tests:
  1. Live healthy system diagnosis (verifies PASS, 8/8 layers, no action suggested)
  2. JSON format validation and schema integrity
  3. Layered attribution across all 8 simulation modes (verifies exact layer localization)
  4. DNS failure is attributed to DNS, never to WAN
  5. Zombie PPPoE is attributed to end_to_end_egress, not link/WAN state
  6. Tuner guard: --fix REFUSES during active recording
  7. Tuner guard: --fix PROCEEDS with targeted WAN bounce when idle (dry-run)
  8. Credential redaction: verifies secrets and stream URLs are never emitted
"""

import json
import subprocess
import sys
import unittest

TRIAGE_BIN = "/usr/local/bin/network-triage"


class TestNetworkTriage(unittest.TestCase):
    def run_triage(self, args: list[str]) -> tuple[int, str, str]:
        cmd = [TRIAGE_BIN] + args
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        return p.returncode, p.stdout.strip(), p.stderr.strip()

    def test_01_live_healthy_system(self):
        code, out, _ = self.run_triage([])
        self.assertEqual(code, 0, "Live healthy triage must exit 0")
        self.assertIn("OVERALL VERDICT: PASS", out)
        self.assertIn("NEXT ACTION:     None (System healthy)", out)
        self.assertIn("[PASS]   Layer 1: Host Link", out)
        self.assertIn("[PASS]   Layer 2: Router Reachable", out)
        self.assertIn("[PASS]   Layer 3: WAN / PPPoE State", out)
        self.assertIn("[PASS]   Layer 4: End-to-End Egress", out)
        self.assertIn("[PASS]   Layer 5: DNS Resolution", out)
        self.assertIn("[PASS]   Layer 6: VPN Tunnel State", out)
        self.assertIn("[PASS]   Layer 7: IPTV Provider Reachability", out)
        self.assertIn("[PASS]   Layer 8: Media Stack Health", out)

    def test_02_json_schema_and_completeness(self):
        code, out, _ = self.run_triage(["--json"])
        self.assertEqual(code, 0)
        data = json.loads(out)
        self.assertEqual(data["verdict"], "PASS")
        self.assertIsNone(data["failed_layer"])
        self.assertIn("layers", data)
        layers = data["layers"]
        expected_layers = [
            "host_link",
            "router_reachable",
            "wan_pppoe",
            "end_to_end_egress",
            "dns",
            "vpn_tunnels",
            "iptv_provider",
            "media_stack",
        ]
        for l in expected_layers:
            self.assertIn(l, layers, f"Layer {l} must be present in JSON")
            self.assertEqual(layers[l]["status"], "PASS")
            self.assertIn("elapsed_seconds", layers[l])
            self.assertIn("details", layers[l])

    def test_03_simulate_host_link_down(self):
        code, out, _ = self.run_triage(["--simulate", "host-link-down"])
        self.assertEqual(code, 1)
        self.assertIn("OVERALL VERDICT: FAIL", out)
        self.assertIn("Outage localized to Layer 1: Host Link", out)
        self.assertIn("Check physical ethernet cabling", out)
        self.assertIn("[SKIP]   Layer 2: Router Reachable", out)

    def test_04_simulate_router_unreachable(self):
        code, out, _ = self.run_triage(["--simulate", "router-unreachable"])
        self.assertEqual(code, 1)
        self.assertIn("OVERALL VERDICT: FAIL", out)
        self.assertIn("Outage localized to Layer 2: Router Reachable", out)
        self.assertIn("Verify power and LAN connection to GL-BE9300", out)
        self.assertIn("[PASS]   Layer 1: Host Link", out)
        self.assertIn("[SKIP]   Layer 3: WAN / PPPoE State", out)

    def test_05_simulate_wan_down(self):
        code, out, _ = self.run_triage(["--simulate", "wan-down"])
        self.assertEqual(code, 1)
        self.assertIn("OVERALL VERDICT: FAIL", out)
        self.assertIn("Outage localized to Layer 3: WAN / PPPoE State", out)
        self.assertIn("[PASS]   Layer 1: Host Link", out)
        self.assertIn("[PASS]   Layer 2: Router Reachable", out)
        self.assertIn("[FAIL]   Layer 3: WAN / PPPoE State", out)
        self.assertIn("Bounce WAN interface", out)

    def test_06_simulate_zombie_pppoe_layer_attribution(self):
        code, out, _ = self.run_triage(["--simulate", "zombie-pppoe"])
        self.assertEqual(code, 1)
        self.assertIn("OVERALL VERDICT: FAIL", out)
        self.assertIn("Outage localized to Layer 4: End-to-End Egress", out)
        self.assertIn("Zombie PPPoE session detected", out)
        self.assertIn("[PASS]   Layer 3: WAN / PPPoE State", out)
        self.assertIn("[FAIL]   Layer 4: End-to-End Egress", out)

    def test_07_simulate_dns_failure_not_wan_failure(self):
        code, out, _ = self.run_triage(["--simulate", "dns-failure"])
        self.assertEqual(code, 1)
        self.assertIn("OVERALL VERDICT: FAIL", out)
        self.assertIn("Outage localized to Layer 5: DNS Resolution", out)
        self.assertIn("[PASS]   Layer 3: WAN / PPPoE State", out)
        self.assertIn("[PASS]   Layer 4: End-to-End Egress", out)
        self.assertIn("[FAIL]   Layer 5: DNS Resolution", out)
        self.assertIn("Restart dnsmasq on router", out)

    def test_08_simulate_vpn_down(self):
        code, out, _ = self.run_triage(["--simulate", "vpn-down"])
        self.assertEqual(code, 1)
        self.assertIn("OVERALL VERDICT: FAIL", out)
        self.assertIn("Outage localized to Layer 6: VPN Tunnel State", out)
        self.assertIn("[PASS]   Layer 4: End-to-End Egress", out)
        self.assertIn("[PASS]   Layer 5: DNS Resolution", out)
        self.assertIn("[FAIL]   Layer 6: VPN Tunnel State", out)
        self.assertIn("Bounce WireGuard tunnel wgclient1", out)

    def test_09_simulate_provider_down_http_511(self):
        code, out, _ = self.run_triage(["--simulate", "provider-down"])
        self.assertEqual(code, 1)
        self.assertIn("OVERALL VERDICT: FAIL", out)
        self.assertIn("Outage localized to Layer 7: IPTV Provider Reachability", out)
        self.assertIn("HTTP 511 Network Authentication Required", out)
        self.assertIn("[PASS]   Layer 6: VPN Tunnel State", out)
        self.assertIn("[FAIL]   Layer 7: IPTV Provider Reachability", out)
        self.assertIn("Bounce WireGuard tunnel wgclient1 on router via gl-session to draw a fresh exit IP", out)

    def test_10_simulate_media_stack_down(self):
        code, out, _ = self.run_triage(["--simulate", "media-stack-down"])
        self.assertEqual(code, 1)
        self.assertIn("OVERALL VERDICT: FAIL", out)
        self.assertIn("Outage localized to Layer 8: Media Stack Health", out)
        self.assertIn("[PASS]   Layer 7: IPTV Provider Reachability", out)
        self.assertIn("[FAIL]   Layer 8: Media Stack Health", out)
        self.assertIn("Restart threadfin container inside CT 105", out)

    def test_11_fix_refuses_during_recording(self):
        # Test 1: Live system during active capture
        code, out, _ = self.run_triage(["--simulate", "zombie-pppoe", "--fix"])
        self.assertEqual(code, 1)
        self.assertIn("REFUSED: Cannot bounce WAN while a recording is in progress", out)
        self.assertIn("Bouncing the WAN would abort the active capture", out)

        # Test 2: Explicit simulation of recording in progress
        code2, out2, _ = self.run_triage(["--simulate", "zombie-pppoe", "--fix", "--simulate-recording"])
        self.assertEqual(code2, 1)
        self.assertIn("REFUSED: Cannot bounce WAN while a recording is in progress", out2)

    def test_12_fix_proceeds_when_idle_dry_run(self):
        code, out, _ = self.run_triage(["--simulate", "zombie-pppoe", "--fix", "--simulate-idle", "--dry-run"])
        self.assertEqual(code, 0)
        self.assertIn("[DRY-RUN] Tuner is IDLE. Targeted WAN bounce authorized.", out)
        self.assertIn("ubus call network.interface.wan down && sleep 2 && ubus call network.interface.wan up", out)

    def test_13_credential_redaction(self):
        from network_triage import redact_sensitive
        # Test stream URL redaction
        sample_url = "http://stream.host.com/live/username123/password456/789.ts"
        redacted = redact_sensitive(sample_url)
        self.assertNotIn("username123", redacted)
        self.assertNotIn("password456", redacted)
        self.assertIn("<REDACTED>/<REDACTED>/789.ts", redacted)

        # Test PPPoE credential redaction
        pppoe_line = "option username 't-online-user@telekom.de'\noption password 'SecretPppPass'"
        redacted_ppp = redact_sensitive(pppoe_line)
        self.assertNotIn("SecretPppPass", redacted_ppp)
        self.assertIn("<REDACTED>", redacted_ppp)


if __name__ == "__main__":
    sys.path.insert(0, "/root/pve-01-docs/scripts")
    unittest.main(verbosity=2)
