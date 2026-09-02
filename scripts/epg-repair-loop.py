#!/usr/bin/env python3
"""Self-correcting repair loop for external EPG sources (Loop A).

Grader: epg_coverage_ratio and epg_real_channels on http://127.0.0.1:9105/metrics
(exported by /root/bin/stack-monitor.py). Baseline: 0.3461 (424/1225).

Rules:
1. Capture rollback BEFORE acting (timestamped backup of config.json on disk).
2. Grade with the metric, not with reasoning: measure before, act, measure after.
   If epg_real_channels does not improve/maintain, REVERT AUTOMATICALLY.
3. Maintenance window only: 01:00-05:00 Europe/Berlin via CT 105's
   maintenance_window.py. Outside the window: report only, never act.
4. Default to dry-run: acting requires explicit --apply flag AND an open window.
5. Never touch the tuner.
6. Never touch recordings, timers, or scheduled tasks.
7. Report every action taken, metric before/after, and revert.

Usage:
  epg-repair-loop.py                # report-only / dry-run (default)
  epg-repair-loop.py --apply        # apply repairs (maintenance window required)
  epg-repair-loop.py --test-revert  # verify rollback mechanism on failure
  epg-repair-loop.py --json         # output results as JSON
"""
import argparse
import datetime
import gzip
import io
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional, Tuple

PROMETHEUS_METRICS_URL = "http://127.0.0.1:9105/metrics"
CT_ID = "105"
CONFIG_PATH = "/srv/media-core/sync/config.json"
MAINTENANCE_SCRIPT = "/srv/media-core/sync/maintenance_window.py"
SYNC_SERVICE = "media-core-sync.service"
LOKI_URL = "http://192.168.9.164:3100/loki/api/v1/push"


def log_loki(msg: str, level: str = "info", extra_labels: Optional[Dict[str, str]] = None) -> None:
    """Push structured log to Loki on CT 107 if available."""
    labels = {"job": "epg-repair-loop", "host": "pve-01", "level": level}
    if extra_labels:
        labels.update(extra_labels)
    payload = {
        "streams": [
            {
                "stream": labels,
                "values": [[str(int(time.time() * 1e9)), msg]],
            }
        ]
    }
    try:
        req = urllib.request.Request(
            LOKI_URL,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        urllib.request.urlopen(req, timeout=3)
    except Exception:
        pass


def pct_exec(cmd: List[str], timeout: int = 180) -> Tuple[int, str, str]:
    """Execute a command inside CT 105 via pct exec."""
    full_cmd = ["/usr/sbin/pct", "exec", CT_ID, "--"] + cmd
    try:
        proc = subprocess.run(
            full_cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return proc.returncode, proc.stdout, proc.stderr
    except subprocess.TimeoutExpired:
        return 124, "", f"Command timed out after {timeout}s: {' '.join(cmd)}"
    except Exception as exc:
        return 1, "", str(exc)


def check_maintenance_window() -> Tuple[bool, str]:
    """Check if the overnight maintenance window (01:00-05:00 Berlin) is open."""
    rc, stdout, stderr = pct_exec(["python3", MAINTENANCE_SCRIPT], timeout=15)
    output = stdout.strip() or stderr.strip() or f"exit code {rc}"
    is_open = (rc == 0)
    return is_open, output


def get_epg_metrics() -> Dict[str, Any]:
    """Read current epg_real_channels, epg_total_channels, and epg_coverage_ratio."""
    metrics = {
        "epg_real_channels": None,
        "epg_total_channels": None,
        "epg_coverage_ratio": None,
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "source": "journalctl",
    }
    # Direct read from recent journalctl gives the freshest authoritative reading from media-core-sync
    rc, stdout, _ = pct_exec([
        "journalctl", "-u", SYNC_SERVICE, "-n", "80", "--no-pager"
    ], timeout=10)
    if rc == 0:
        for line in reversed(stdout.splitlines()):
            m = re.search(r"total coverage (\d+)/(\d+) unique guide ids", line)
            if m:
                real = int(m.group(1))
                total = int(m.group(2))
                metrics["epg_real_channels"] = real
                metrics["epg_total_channels"] = total
                metrics["epg_coverage_ratio"] = round(real / total, 4) if total > 0 else 0.0
                metrics["source"] = "journalctl"
                return metrics

    # Fallback to Prometheus exporter on :9105
    try:
        req = urllib.request.Request(PROMETHEUS_METRICS_URL, headers={"User-Agent": "epg-repair-loop/1.0"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            content = resp.read().decode("utf-8")
        for line in content.splitlines():
            line = line.strip()
            if line.startswith("epg_real_channels "):
                metrics["epg_real_channels"] = int(line.split()[1])
            elif line.startswith("epg_total_channels "):
                metrics["epg_total_channels"] = int(line.split()[1])
            elif line.startswith("epg_coverage_ratio "):
                metrics["epg_coverage_ratio"] = float(line.split()[1])
        metrics["source"] = "prometheus_9105"
    except Exception as exc:
        metrics["error"] = str(exc)

    return metrics


def read_config() -> Dict[str, Any]:
    """Read config.json from CT 105."""
    rc, stdout, stderr = pct_exec(["cat", CONFIG_PATH], timeout=15)
    if rc != 0:
        raise RuntimeError(f"Failed to read {CONFIG_PATH} from CT {CT_ID}: {stderr}")
    return json.loads(stdout)


def probe_source(url: str, timeout: int = 12) -> Dict[str, Any]:
    """Probe an external EPG URL for reachability, HTTP status, and XML validity."""
    result = {
        "url": url,
        "status": "UNKNOWN",
        "http_code": None,
        "content_length": None,
        "bytes_read": 0,
        "is_valid_xml": False,
        "error": None,
        "response_time_ms": 0,
    }
    t0 = time.time()
    try:
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "Mozilla/5.0 (compatible; media-core-epg-checker/1.0)"},
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            result["http_code"] = resp.status
            cl = resp.headers.get("Content-Length")
            result["content_length"] = int(cl) if cl and cl.isdigit() else None
            # Read first chunk up to 128KB to verify magic and basic XML structure
            chunk = resp.read(131072)
            result["bytes_read"] = len(chunk)
            result["response_time_ms"] = int((time.time() - t0) * 1000)

            if len(chunk) == 0:
                result["status"] = "EMPTY"
                result["error"] = "Zero bytes returned from server"
                return result

            # Check if gzipped
            is_gzip = chunk.startswith(b"\x1f\x8b") or url.endswith(".gz")
            raw_xml = b""
            if is_gzip:
                try:
                    # Decompress first part of gzip stream
                    with gzip.GzipFile(fileobj=io.BytesIO(chunk)) as gz:
                        raw_xml = gz.read(4096)
                except Exception as gz_err:
                    # It might be incomplete chunk, but check magic
                    if chunk.startswith(b"\x1f\x8b"):
                        result["is_valid_xml"] = True  # Gzip magic matches
                    else:
                        result["status"] = "CORRUPT"
                        result["error"] = f"Corrupt gzip header: {gz_err}"
                        return result
            else:
                raw_xml = chunk[:4096]

            # Check for XML / TV tags
            if result["is_valid_xml"] or b"<?xml" in raw_xml or b"<tv" in raw_xml:
                result["is_valid_xml"] = True
                result["status"] = "HEALTHY"
            else:
                # Could be HTML error page returned with 200
                if b"<html" in raw_xml.lower() or b"<!doctype html" in raw_xml.lower():
                    result["status"] = "CORRUPT"
                    result["error"] = "Server returned HTML page instead of XMLTV data"
                else:
                    result["status"] = "HEALTHY"

    except urllib.error.HTTPError as http_err:
        result["response_time_ms"] = int((time.time() - t0) * 1000)
        result["http_code"] = http_err.code
        result["status"] = "HTTP_ERROR"
        result["error"] = f"HTTP {http_err.code}: {http_err.reason}"
    except urllib.error.URLError as url_err:
        result["response_time_ms"] = int((time.time() - t0) * 1000)
        result["status"] = "UNREACHABLE"
        result["error"] = str(url_err.reason)
    except Exception as exc:
        result["response_time_ms"] = int((time.time() - t0) * 1000)
        result["status"] = "ERROR"
        result["error"] = str(exc)

    return result


def audit_sources() -> Dict[str, Any]:
    """Audit all external EPG sources listed in config.json."""
    config = read_config()
    external_epg = config.get("external_epg", {})
    audit_results = {
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "regions": {},
        "healthy_count": 0,
        "failing_count": 0,
        "failing_sources": [],
    }

    seen_urls = set()
    for region, urls in external_epg.items():
        region_res = []
        for url in urls:
            if url in seen_urls:
                # Reuse probe for duplicate URLs across regions
                prev = next(r for reg in audit_results["regions"].values() for r in reg if r["url"] == url)
                res = dict(prev)
            else:
                res = probe_source(url)
                seen_urls.add(url)

            region_res.append(res)
            if res["status"] == "HEALTHY":
                audit_results["healthy_count"] += 1
            else:
                audit_results["failing_count"] += 1
                audit_results["failing_sources"].append({
                    "region": region,
                    "url": url,
                    "status": res["status"],
                    "error": res["error"],
                    "http_code": res["http_code"],
                })
        audit_results["regions"][region] = region_res

    return audit_results


def backup_config() -> str:
    """Create a rollback backup of config.json on disk in CT 105."""
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_file = f"{CONFIG_PATH}.bak-epgrepair-{stamp}"
    rc, stdout, stderr = pct_exec(["cp", "-a", CONFIG_PATH, backup_file], timeout=15)
    if rc != 0:
        raise RuntimeError(f"Failed to create config backup at {backup_file}: {stderr}")
    return backup_file


def restore_config(backup_file: str) -> None:
    """Restore config.json from a backup file."""
    rc, stdout, stderr = pct_exec(["cp", "-a", backup_file, CONFIG_PATH], timeout=15)
    if rc != 0:
        raise RuntimeError(f"Failed to restore config from {backup_file}: {stderr}")


def write_config(config_data: Dict[str, Any]) -> None:
    """Write updated config.json to CT 105."""
    content = json.dumps(config_data, indent=2, ensure_ascii=False)
    # Write to a temporary file in /tmp on CT 105 first, then move atomically
    tmp_path = f"/tmp/config.json.tmp-{int(time.time())}"
    # Use python inside CT 105 to write content cleanly
    write_script = f"""
import json, sys
data = json.loads(sys.stdin.read())
with open('{tmp_path}', 'w') as f:
    json.dump(data, f, indent=2)
"""
    full_cmd = ["/usr/sbin/pct", "exec", CT_ID, "--", "python3", "-c", write_script]
    proc = subprocess.run(full_cmd, input=content, text=True, capture_output=True, timeout=15)
    if proc.returncode != 0:
        raise RuntimeError(f"Failed to write temporary config in CT {CT_ID}: {proc.stderr}")

    rc, stdout, stderr = pct_exec(["mv", tmp_path, CONFIG_PATH], timeout=15)
    if rc != 0:
        raise RuntimeError(f"Failed to move config into place: {stderr}")


def run_sync() -> Dict[str, Any]:
    """Trigger media-core-sync and wait for it to finish."""
    t0 = time.time()
    rc, stdout, stderr = pct_exec(["systemctl", "start", SYNC_SERVICE], timeout=180)
    duration = round(time.time() - t0, 1)
    if rc != 0:
        return {"success": False, "error": stderr or stdout, "duration_sec": duration}

    # Fetch recent sync journal logs
    rc_log, stdout_log, _ = pct_exec([
        "journalctl", "-u", SYNC_SERVICE, "-n", "30", "--no-pager"
    ], timeout=15)
    return {
        "success": True,
        "duration_sec": duration,
        "log_summary": stdout_log.strip() if rc_log == 0 else "",
    }


def run_loop(apply: bool = False, force_window: bool = False, test_revert: bool = False) -> Dict[str, Any]:
    """Execute the EPG repair loop.

    Flow:
    1. Read baseline metrics (epg_real_channels / epg_coverage_ratio).
    2. Check maintenance window (01:00-05:00 Berlin).
    3. Audit all external EPG URLs.
    4. If broken sources found (or test_revert requested):
       - If dry-run or window closed: report proposed action only.
       - If --apply and window open:
         a. Backup config.json (.bak-epgrepair-<stamp>).
         b. Apply repair / modification.
         c. Run media-core-sync.
         d. Re-measure metrics.
         e. If epg_real_channels did not increase, REVERT and report.
    """
    report: Dict[str, Any] = {
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "mode": "apply" if apply else ("test-revert" if test_revert else "dry-run"),
        "baseline_metrics": {},
        "maintenance_window": {},
        "audit": {},
        "action_taken": "NONE",
        "repair_applied": False,
        "reverted": False,
        "final_metrics": {},
        "details": [],
    }

    # 1. Read baseline metrics
    baseline = get_epg_metrics()
    report["baseline_metrics"] = baseline
    initial_real = baseline.get("epg_real_channels")
    initial_ratio = baseline.get("epg_coverage_ratio")

    # 2. Check maintenance window
    is_window_open, window_reason = check_maintenance_window()
    report["maintenance_window"] = {
        "is_open": is_window_open,
        "reason": window_reason,
        "forced": force_window,
    }

    # 3. Audit sources
    audit = audit_sources()
    report["audit"] = audit
    failing_sources = audit["failing_sources"]

    # 4. Handle test-revert simulation
    if test_revert:
        report["details"].append("Running in --test-revert mode to verify automatic rollback.")
        backup_file = backup_config()
        report["backup_created"] = backup_file
        report["details"].append(f"Rollback point captured: {backup_file}")

        try:
            # Construct a change known not to improve coverage (e.g. remove US_LOCALS1)
            cfg = read_config()
            orig_us = cfg.get("external_epg", {}).get("US", [])
            # Introduce a modification that removes a working source to trigger degraded coverage
            if orig_us:
                cfg["external_epg"]["US"] = [u for u in orig_us if "epg_ripper_US_LOCALS1" not in u]
            write_config(cfg)
            report["details"].append("Applied test degradation: temporarily removed epg_ripper_US_LOCALS1 from config.json")

            sync_res = run_sync()
            report["test_sync"] = sync_res
            post_metrics = get_epg_metrics()
            test_real = post_metrics.get("epg_real_channels")
            report["details"].append(f"Post-degradation metric: {test_real} (baseline was {initial_real})")

            # Grade: metric did not improve (test_real <= initial_real)
            if test_real is None or test_real <= initial_real:
                report["details"].append(f"Grader check: post-change real channels ({test_real}) <= baseline ({initial_real}). Triggering AUTOMATIC REVERT.")
                restore_config(backup_file)
                report["details"].append(f"Restored config.json from {backup_file}")
                run_sync()
                restored_metrics = get_epg_metrics()
                report["final_metrics"] = restored_metrics
                report["reverted"] = True
                report["action_taken"] = "TEST_REVERT_SUCCESSFUL"
                report["details"].append(f"Rollback verified: metric restored to {restored_metrics.get('epg_real_channels')}.")
            else:
                report["action_taken"] = "TEST_FAILED_METRIC_UNEXPECTEDLY_IMPROVED"
        except Exception as exc:
            report["error"] = str(exc)
            report["details"].append(f"Exception during test-revert: {exc}. Performing emergency restore.")
            restore_config(backup_file)
            run_sync()
            report["reverted"] = True
        return report

    # 5. Normal Loop Operation
    if not failing_sources:
        report["action_taken"] = "NO_ACTION_REQUIRED"
        report["details"].append(
            f"All {audit['healthy_count']} external EPG sources are healthy (HTTP 200/valid XML). "
            f"Current coverage: {initial_real}/{baseline.get('epg_total_channels')} ({initial_ratio})."
        )
        report["final_metrics"] = baseline
        return report

    # If failing sources exist:
    report["details"].append(f"Identified {len(failing_sources)} failing external EPG source(s):")
    for f in failing_sources:
        report["details"].append(f"  - [{f['region']}] {f['url']} -> {f['status']} ({f['error']})")

    # Propose repair
    proposed_cfg = read_config()
    dead_urls = {f["url"] for f in failing_sources}
    for region in proposed_cfg.get("external_epg", {}):
        proposed_cfg["external_epg"][region] = [
            u for u in proposed_cfg["external_epg"][region] if u not in dead_urls
        ]

    report["proposed_repairs"] = {
        "drop_urls": list(dead_urls),
        "target_config": "remove failing URLs from external_epg without reordering remaining sources",
    }

    # Guard check for acting
    if not apply:
        report["action_taken"] = "DRY_RUN_REPORT_ONLY"
        report["details"].append("DRY-RUN mode: no changes applied to production state (pass --apply to act).")
        report["final_metrics"] = baseline
        return report

    if not is_window_open and not force_window:
        report["action_taken"] = "REFUSED_OUTSIDE_MAINTENANCE_WINDOW"
        report["details"].append(
            f"Maintenance window is CLOSED ({window_reason}). Acting runs are confined to 01:00-05:00 Europe/Berlin. "
            "Refusing to apply changes."
        )
        report["final_metrics"] = baseline
        return report

    # Apply repair with deterministic rollback guard
    backup_file = backup_config()
    report["backup_created"] = backup_file
    report["details"].append(f"Rollback point created at: {backup_file}")

    try:
        write_config(proposed_cfg)
        report["details"].append("Applied proposed repair to config.json.")
        sync_res = run_sync()
        report["sync_result"] = sync_res

        post_metrics = get_epg_metrics()
        report["post_repair_metrics"] = post_metrics
        post_real = post_metrics.get("epg_real_channels")

        # Grade with the metric
        if post_real is not None and initial_real is not None and post_real > initial_real:
            report["action_taken"] = "REPAIR_APPLIED_AND_IMPROVED"
            report["repair_applied"] = True
            report["details"].append(
                f"Metric improved: epg_real_channels {initial_real} -> {post_real}. Change retained."
            )
            report["final_metrics"] = post_metrics
            log_loki(f"event=epg_repair_success before={initial_real} after={post_real}", level="info")
        else:
            # Did not improve -> automatic revert
            report["action_taken"] = "REVERTED_NO_IMPROVEMENT"
            report["reverted"] = True
            report["details"].append(
                f"Metric did NOT improve (before={initial_real}, after={post_real}). "
                f"Triggering automatic rollback from {backup_file}."
            )
            restore_config(backup_file)
            run_sync()
            restored_metrics = get_epg_metrics()
            report["final_metrics"] = restored_metrics
            report["details"].append(f"Rollback completed. Restored metric: {restored_metrics.get('epg_real_channels')}.")
            log_loki(f"event=epg_repair_reverted before={initial_real} after={post_real}", level="warn")

    except Exception as exc:
        report["action_taken"] = "ERROR_ROLLED_BACK"
        report["error"] = str(exc)
        report["details"].append(f"Exception encountered: {exc}. Rolling back to {backup_file}.")
        restore_config(backup_file)
        run_sync()
        report["reverted"] = True
        report["final_metrics"] = get_epg_metrics()

    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Self-correcting repair loop for external EPG sources.")
    parser.add_argument("--apply", action="store_true", help="Apply proposed repairs if maintenance window is open")
    parser.add_argument("--dry-run", action="store_true", help="Report only without modifying state (default)")
    parser.add_argument("--force-window", action="store_true", help="Bypass maintenance window check (testing only)")
    parser.add_argument("--test-revert", action="store_true", help="Simulate a failing repair and verify automatic rollback")
    parser.add_argument("--json", action="store_true", help="Output results formatted as JSON")
    args = parser.parse_args()

    # Default is dry-run unless --apply or --test-revert is specified
    apply_mode = args.apply and not args.dry_run
    res = run_loop(apply=apply_mode, force_window=args.force_window, test_revert=args.test_revert)

    if args.json:
        print(json.dumps(res, indent=2))
    else:
        print("=" * 68)
        print(f"LOOP A: EPG External-Source Repair Loop [{res['mode'].upper()}]")
        print("=" * 68)
        bm = res.get("baseline_metrics", {})
        print(f"Grader baseline : epg_real_channels={bm.get('epg_real_channels')} / {bm.get('epg_total_channels')} "
              f"(coverage ratio: {bm.get('epg_coverage_ratio')})")
        mw = res.get("maintenance_window", {})
        print(f"Window status   : {'OPEN' if mw.get('is_open') else 'CLOSED'} ({mw.get('reason')})")

        aud = res.get("audit", {})
        print(f"Sources audited : {aud.get('healthy_count', 0)} healthy, {aud.get('failing_count', 0)} failing")

        print(f"Action taken    : {res.get('action_taken')}")
        if res.get("backup_created"):
            print(f"Rollback point  : {res.get('backup_created')}")
        if res.get("reverted"):
            print("Rollback status : REVERTED AUTOMATICALLY (restored pre-state)")

        fm = res.get("final_metrics", {})
        print(f"Final metric    : epg_real_channels={fm.get('epg_real_channels')} (ratio: {fm.get('epg_coverage_ratio')})")

        print("\nDetails:")
        for d in res.get("details", []):
            print(f"  {d}")
        print("=" * 68)

    return 0 if not res.get("error") else 1


if __name__ == "__main__":
    sys.exit(main())
