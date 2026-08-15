#!/usr/bin/env python3
"""Test script for SafetyRailManager collateral damage detection & auto-revert.

Injects a harmless blackhole rule discrepancy during a test write action on 3.1
and verifies that SafetyRailManager detects the collateral damage, executes
auto-revert, logs the system-wide before/after snapshot diff, and alerts.
"""
import importlib.machinery
import json
import logging
import os
import subprocess
import sys

# Load router-dashboard module dynamically
loader = importlib.machinery.SourceFileLoader("router_dashboard", "/usr/local/bin/router-dashboard")
rd = loader.load_module()

SafetyRailManager = rd.SafetyRailManager
get_ssh_args_for_router = rd.get_ssh_args_for_router

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")


def test_collateral_damage_detection():
    router = "3.1"
    ssh_args = get_ssh_args_for_router(router)
    injected_rule_str = "ip rule add prio 9920 iif br-test blackhole"
    cleanup_rule_str = "ip rule del prio 9920 iif br-test 2>/dev/null || true"

    print("=== Step 1: Baseline Health Snapshot ===")
    before_snap = SafetyRailManager.capture_system_health_snapshot(router)
    print(f"Blackhole rules count before: {len(before_snap.get('blackhole_rules', []))}")
    print(json.dumps(before_snap, indent=2))

    def get_before():
        return {"test": "safety_wrapper_collateral_damage_injected_test"}

    def perform_write_with_injection():
        print("\n--- [Test Action] Performing target write AND injecting collateral discrepancy on 3.1 ---")
        # Harmless injection: add priority 9920 blackhole rule for non-existent br-test bridge
        cmd = ["ssh", "-o", "ConnectTimeout=5", *ssh_args, injected_rule_str]
        res = subprocess.run(cmd, capture_output=True, text=True)
        if res.returncode != 0:
            return False, f"Failed to inject test rule: {res.stderr}"
        return True, "Simulated target write succeeded (with collateral damage injected)"

    def verify_target():
        return True, "Simulated target verification passed"

    def revert_collateral():
        print("\n--- [Revert Fn Executing] Cleaning up injected rule on 3.1 ---")
        cmd = ["ssh", "-o", "ConnectTimeout=5", *ssh_args, cleanup_rule_str]
        res = subprocess.run(cmd, capture_output=True, text=True)
        if res.returncode == 0:
            return True, "Injected rule removed successfully"
        return False, f"Cleanup failed: {res.stderr}"

    print("\n=== Step 2: Executing Safe Action with Injected Discrepancy ===")
    success, result_msg = SafetyRailManager.execute_safe_action(
        user="test_runner",
        action_name="test_collateral_damage_injection",
        router=router,
        get_before_fn=get_before,
        perform_write_fn=perform_write_with_injection,
        verify_fn=verify_target,
        revert_fn=revert_collateral,
        allowed_target_interfaces={"lan4"}
    )

    print("\n=== Step 3: Action Result ===")
    print(f"Success: {success}")
    print(f"Message: {result_msg}")

    # Ensure cleanup ran even if revert didn't
    subprocess.run(["ssh", "-o", "ConnectTimeout=5", *ssh_args, cleanup_rule_str], capture_output=True)

    print("\n=== Step 4: Post-Revert Health Verification ===")
    after_revert_snap = SafetyRailManager.capture_system_health_snapshot(router)
    clean, diff_disc, diff_sum = SafetyRailManager.diff_system_health_snapshots(before_snap, after_revert_snap, {"lan4"})
    print(f"System restored to clean state: {clean}")
    if not clean:
        print(f"Remaining discrepancies: {diff_disc}")

    print("\n=== Step 5: Recent Audit Log Entry ===")
    audit_file = rd.AUDIT_LOG_FILE
    if audit_file.exists():
        lines = audit_file.read_text().splitlines()
        if lines:
            last_entry = json.loads(lines[-1])
            print(json.dumps(last_entry, indent=2))

    assert not success, "Action should have failed due to collateral damage!"
    assert "COLLATERAL DAMAGE DETECTED" in result_msg, "Result message must state collateral damage was detected!"
    assert clean, "System health must be 100% restored after revert!"
    print("\n✓ SUCCESS: Collateral damage detection, alert, revert, and audit logging verified!")


if __name__ == "__main__":
    test_collateral_damage_detection()
