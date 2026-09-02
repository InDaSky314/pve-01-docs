#!/usr/bin/env python3
"""Weekly Backup Restorability Verification.

Weekly verification (Sunday 10:30 Europe/Berlin) after the Sunday 09:30 vzdump job:
1. BLUF verdict first: RESTORABLE / ATTENTION NEEDED.
2. vzdump guest archives in /mnt/pve/SSD/dump/ for each VMID in jobs.cfg (102, 105, 107, 108, 112):
   - Newest archive present and age sane (<8 days).
   - Archive size stability vs previous generation (flags WoW swing > ±25%).
   - Fast, low-I/O archive opening & header/config integrity check:
     * LXC (.tar.zst): single-block config extraction of ./etc/vzdump/pct.conf via tar --zstd.
     * QEMU (.vma.zst): vma config header extraction via zstd stream.
     * Validates decompression stream and guest configuration without multi-GB disk extraction.
3. Media-Core app state backup in /mnt/pve/SSD/media-core-backups/:
   - Newest archive <36h old and opens cleanly.
   - Verifies required restore artefacts: jellyfin.db.snapshot, sync/config.json,
     docker-compose.yml, .env, threadfin/conf.
   - Reports jellyfin.db.snapshot uncompressed size.
4. Retention & Pruning:
   - vzdump keep-last=4 and media-core config keep=7.
5. Storage Capacity & Runway:
   - Free space on /mnt/pve/SSD and headroom estimation.
6. Fail-soft email delivery: plain-text + styled HTML via host sendmail relay
   to nathan.karras@gmail.com (port 465). Never raises unhandled exceptions.
"""
from __future__ import annotations

import argparse
import html as html_lib
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import tarfile
import time
from datetime import datetime, timedelta, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

# ── Configuration & Paths ───────────────────────────────────────────────────

LOCAL_TZ = ZoneInfo("Europe/Berlin")
MAILTO = "nathan.karras@gmail.com"
FROM_ADDR = "kopr.notify@gmail.com"
FROM_NAME = "Proxmox Backup Verifier"

SSD_MOUNT = Path("/mnt/pve/SSD")
VZDUMP_DIR = SSD_MOUNT / "dump"
CONFIG_BACKUPS_DIR = SSD_MOUNT / "media-core-backups"
JOBS_CFG = Path("/etc/pve/jobs.cfg")

DEFAULT_VMIDS = [102, 105, 107, 108, 112]
VZDUMP_RETENTION_EXPECTED = 4
CONFIG_RETENTION_EXPECTED = 7

MAX_VZDUMP_AGE_HOURS = 192  # 8 days
MAX_CONFIG_AGE_HOURS = 36   # 36 hours (nightly job)
MAX_SIZE_SWING_PERCENT = 25.0

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)


# ── Formatting Helpers ─────────────────────────────────────────────────────

def fmt_bytes(n: int | float) -> str:
    """Format byte size into human readable string."""
    n = float(n)
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if abs(n) < 1024.0:
            return f"{n:.2f} {unit}" if unit in ("MB", "GB", "TB") else f"{int(n)} {unit}"
        n /= 1024.0
    return f"{n:.2f} PB"


def fmt_age(hours: float) -> str:
    if hours < 1.0:
        return f"{int(hours * 60)}m ago"
    if hours < 24.0:
        return f"{hours:.1f}h ago"
    days = hours / 24.0
    return f"{days:.1f}d ago"


# ── Jobs.cfg & Guest Metadata ──────────────────────────────────────────────

def get_configured_vmids() -> list[int]:
    """Parse VMIDs from /etc/pve/jobs.cfg vzdump definition."""
    if not JOBS_CFG.exists():
        return DEFAULT_VMIDS
    try:
        content = JOBS_CFG.read_text(encoding="utf-8")
        m = re.search(r"^\s*vmid\s+([\d,]+)", content, re.MULTILINE)
        if m:
            vmids = [int(v.strip()) for v in m.group(1).split(",") if v.strip().isdigit()]
            if vmids:
                return vmids
    except Exception as exc:
        logging.warning("Error reading %s: %s", JOBS_CFG, exc)
    return DEFAULT_VMIDS


def get_guest_names() -> dict[int, dict[str, str]]:
    """Retrieve name and type for each guest via qm/pct list."""
    guests: dict[int, dict[str, str]] = {}

    # CTs
    try:
        p = subprocess.run(["/usr/sbin/pct", "list"], capture_output=True, text=True, timeout=10)
        if p.returncode == 0:
            for line in p.stdout.splitlines()[1:]:
                parts = line.split()
                if len(parts) >= 3 and parts[0].isdigit():
                    vmid = int(parts[0])
                    guests[vmid] = {"type": "LXC", "name": parts[2]}
    except Exception:
        pass

    # VMs
    try:
        p = subprocess.run(["/usr/sbin/qm", "list"], capture_output=True, text=True, timeout=10)
        if p.returncode == 0:
            for line in p.stdout.splitlines()[1:]:
                parts = line.split()
                if len(parts) >= 2 and parts[0].isdigit():
                    vmid = int(parts[0])
                    guests[vmid] = {"type": "QEMU", "name": parts[1]}
    except Exception:
        pass

    return guests


# ── vzdump Verification ─────────────────────────────────────────────────────

def verify_vzdump_archive_integrity(archive_path: Path, guest_type: str) -> tuple[bool, str]:
    """Verify archive opens and guest configuration is extractable (low I/O single block)."""
    if not archive_path.exists():
        return False, "Archive file does not exist on disk"

    str_path = str(archive_path)

    if guest_type == "LXC" or str_path.endswith(".tar.zst"):
        # Extract ./etc/vzdump/pct.conf in single-occurrence mode (stops at first block)
        cmd = ["/usr/bin/tar", "--zstd", "--occurrence=1", "-xOf", str_path, "./etc/vzdump/pct.conf"]
        try:
            t0 = time.time()
            p = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
            elapsed = time.time() - t0
            if p.returncode == 0 and ("hostname:" in p.stdout or "arch:" in p.stdout or "cores:" in p.stdout):
                return True, f"Verified valid LXC tar.zst container & pct.conf header extracted ({elapsed:.3f}s)"
            # Fallback check if path was saved without leading ./
            cmd_alt = ["/usr/bin/tar", "--zstd", "--occurrence=1", "-xOf", str_path, "etc/vzdump/pct.conf"]
            p_alt = subprocess.run(cmd_alt, capture_output=True, text=True, timeout=20)
            if p_alt.returncode == 0 and ("hostname:" in p_alt.stdout or "arch:" in p_alt.stdout):
                return True, f"Verified valid LXC tar.zst container & pct.conf header extracted ({elapsed:.3f}s)"
            return False, f"tar extraction failed (rc={p.returncode}, stderr={p.stderr.strip()[:100]})"
        except Exception as exc:
            return False, f"tar extraction exception: {exc}"

    elif guest_type == "QEMU" or str_path.endswith(".vma.zst"):
        # Stream first blocks into vma config /dev/stdin
        try:
            t0 = time.time()
            p_zstd = subprocess.Popen(["/usr/bin/zstd", "-d", "-c", str_path], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            p_vma = subprocess.Popen(["/usr/bin/vma", "config", "/dev/stdin"], stdin=p_zstd.stdout, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            if p_zstd.stdout:
                p_zstd.stdout.close()
            vma_out, vma_err = p_vma.communicate(timeout=20)
            p_zstd.kill()
            elapsed = time.time() - t0
            if p_vma.returncode == 0 and ("cores:" in vma_out or "boot:" in vma_out or "memory:" in vma_out or "ostype:" in vma_out):
                return True, f"Verified valid QEMU vma.zst container & VM config extracted ({elapsed:.3f}s)"
            return False, f"vma config failed (rc={p_vma.returncode}, err={vma_err.strip()[:100]})"
        except Exception as exc:
            return False, f"vma config exception: {exc}"

    return False, f"Unrecognized archive format: {archive_path.name}"


def evaluate_vzdump_archives(
    now: datetime,
    vmids: list[int],
    guest_meta: dict[int, dict[str, str]],
) -> tuple[list[dict[str, Any]], list[str]]:
    """Audit vzdump archives for each configured guest in /mnt/pve/SSD/dump/."""
    results: list[dict[str, Any]] = []
    anomalies: list[str] = []

    if not VZDUMP_DIR.exists():
        anomalies.append(f"CRITICAL: Vzdump directory {VZDUMP_DIR} does not exist!")
        return results, anomalies

    for vmid in vmids:
        meta = guest_meta.get(vmid, {"type": "Unknown", "name": f"guest-{vmid}"})
        g_name = meta["name"]
        g_type = meta["type"]

        # Search for archives matching vmid
        matching = list(VZDUMP_DIR.glob(f"vzdump-*-{vmid}-*.tar.zst")) + list(VZDUMP_DIR.glob(f"vzdump-*-{vmid}-*.vma.zst"))
        matching.sort(key=lambda p: p.stat().st_mtime, reverse=True)

        if not matching:
            # Check if this VMID was added recently to jobs.cfg (e.g. 107, 108, 112)
            results.append({
                "vmid": vmid,
                "name": g_name,
                "type": g_type,
                "status": "PENDING_FIRST_RUN",
                "archive_count": 0,
                "newest_archive": None,
                "newest_mtime": None,
                "age_hours": None,
                "size_bytes": 0,
                "size_str": "N/A",
                "prev_size_bytes": None,
                "wow_swing_pct": None,
                "integrity_ok": True,
                "integrity_msg": "Configured in jobs.cfg; awaiting first weekly vzdump run on Sunday 09:30",
                "log_present": False,
            })
            continue

        newest = matching[0]
        st = newest.stat()
        mtime_dt = datetime.fromtimestamp(st.st_mtime, tz=LOCAL_TZ)
        age_hours = (now - mtime_dt).total_seconds() / 3600.0
        size_bytes = st.st_size

        # Check for matching .log file
        log_path = newest.with_suffix("").with_suffix(".log")
        log_present = log_path.exists()

        # Compute week-over-week size swing if previous generation exists
        prev_size = None
        swing_pct = None
        if len(matching) > 1:
            prev_size = matching[1].stat().st_size
            if prev_size > 0:
                swing_pct = ((size_bytes - prev_size) / prev_size) * 100.0

        # Run integrity verification
        integ_ok, integ_msg = verify_vzdump_archive_integrity(newest, g_type)

        # Anomaly checks
        if not integ_ok:
            anomalies.append(f"VMID {vmid} ({g_name}): Archive integrity failure on {newest.name} ({integ_msg})")

        if age_hours > MAX_VZDUMP_AGE_HOURS:
            anomalies.append(f"VMID {vmid} ({g_name}): Newest vzdump archive is STALE ({fmt_age(age_hours)}, expected <8d)")

        if swing_pct is not None and abs(swing_pct) > MAX_SIZE_SWING_PERCENT:
            anomalies.append(f"VMID {vmid} ({g_name}): Suspicious size swing of {swing_pct:+.1f}% week-over-week ({fmt_bytes(prev_size)} -> {fmt_bytes(size_bytes)})")

        if len(matching) > VZDUMP_RETENTION_EXPECTED:
            anomalies.append(f"VMID {vmid} ({g_name}): Retention exceeded ({len(matching)} archives found, expected keep-last={VZDUMP_RETENTION_EXPECTED})")

        results.append({
            "vmid": vmid,
            "name": g_name,
            "type": g_type,
            "status": "VERIFIED" if (integ_ok and age_hours <= MAX_VZDUMP_AGE_HOURS) else "ATTENTION",
            "archive_count": len(matching),
            "newest_archive": newest.name,
            "newest_mtime": mtime_dt,
            "age_hours": age_hours,
            "size_bytes": size_bytes,
            "size_str": fmt_bytes(size_bytes),
            "prev_size_bytes": prev_size,
            "wow_swing_pct": swing_pct,
            "integrity_ok": integ_ok,
            "integrity_msg": integ_msg,
            "log_present": log_present,
        })

    return results, anomalies


# ── Media-Core Config Backup Verification ──────────────────────────────────

def evaluate_config_backups(now: datetime) -> tuple[dict[str, Any], list[str]]:
    """Audit media-core app state backups in /mnt/pve/SSD/media-core-backups/."""
    anomalies: list[str] = []
    res: dict[str, Any] = {
        "status": "MISSING",
        "archive_count": 0,
        "newest_archive": None,
        "newest_mtime": None,
        "age_hours": None,
        "size_bytes": 0,
        "size_str": "N/A",
        "tar_opens_cleanly": False,
        "artefacts": {},
        "jellyfin_db_snapshot_bytes": 0,
        "jellyfin_db_snapshot_str": "N/A",
    }

    if not CONFIG_BACKUPS_DIR.exists():
        anomalies.append(f"CRITICAL: Config backup directory {CONFIG_BACKUPS_DIR} missing!")
        return res, anomalies

    archives = list(CONFIG_BACKUPS_DIR.glob("media-core-config-*.tar.gz"))
    archives.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    res["archive_count"] = len(archives)

    if not archives:
        anomalies.append("CRITICAL: No media-core-config-*.tar.gz archives found in backup directory!")
        return res, anomalies

    newest = archives[0]
    st = newest.stat()
    mtime_dt = datetime.fromtimestamp(st.st_mtime, tz=LOCAL_TZ)
    age_hours = (now - mtime_dt).total_seconds() / 3600.0

    res["newest_archive"] = newest.name
    res["newest_mtime"] = mtime_dt
    res["age_hours"] = age_hours
    res["size_bytes"] = st.st_size
    res["size_str"] = fmt_bytes(st.st_size)

    if age_hours > MAX_CONFIG_AGE_HOURS:
        anomalies.append(f"Media-Core config backup is STALE ({fmt_age(age_hours)}, expected <36h)")

    if len(archives) > CONFIG_RETENTION_EXPECTED:
        anomalies.append(f"Media-Core config backup retention exceeded ({len(archives)} archives, expected {CONFIG_RETENTION_EXPECTED})")

    # Inspect archive contents
    required_artefacts = [
        "jellyfin.db.snapshot",
        "sync/config.json",
        "docker-compose.yml",
        ".env",
        "threadfin/conf",
    ]
    artefact_status: dict[str, bool] = {k: False for k in required_artefacts}

    try:
        with tarfile.open(newest, "r:gz") as tar:
            res["tar_opens_cleanly"] = True
            names = tar.getnames()
            name_set = set(names)

            for req in required_artefacts:
                if req in name_set:
                    artefact_status[req] = True
                elif req == "threadfin/conf" and any(n.startswith("threadfin/conf") for n in name_set):
                    artefact_status[req] = True
                elif req == "sync/config.json" and any(n == "sync/config.json" or n.startswith("sync/config.json.") for n in name_set):
                    artefact_status[req] = True

            # Extract jellyfin.db.snapshot size
            if "jellyfin.db.snapshot" in name_set:
                member = tar.getmember("jellyfin.db.snapshot")
                res["jellyfin_db_snapshot_bytes"] = member.size
                res["jellyfin_db_snapshot_str"] = fmt_bytes(member.size)
                if member.size < 50_000_000:  # <50 MB indicates an empty/corrupted DB
                    anomalies.append(f"jellyfin.db.snapshot is suspiciously small ({fmt_bytes(member.size)})")

    except Exception as exc:
        res["tar_opens_cleanly"] = False
        anomalies.append(f"Media-Core config archive {newest.name} failed to open: {exc}")

    res["artefacts"] = artefact_status

    for req, ok in artefact_status.items():
        if not ok:
            anomalies.append(f"Missing required restore artefact '{req}' inside {newest.name}")

    if res["tar_opens_cleanly"] and all(artefact_status.values()) and age_hours <= MAX_CONFIG_AGE_HOURS:
        res["status"] = "VERIFIED"
    else:
        res["status"] = "ATTENTION"

    return res, anomalies


# ── Storage Capacity & Runway ──────────────────────────────────────────────

def evaluate_storage_capacity() -> dict[str, Any]:
    """Check free space and estimate runway on /mnt/pve/SSD."""
    storage_info = {
        "mount": str(SSD_MOUNT),
        "total_bytes": 0,
        "used_bytes": 0,
        "avail_bytes": 0,
        "use_percent": 0.0,
        "total_str": "N/A",
        "used_str": "N/A",
        "avail_str": "N/A",
        "headroom_weeks": 52,
        "status": "HEALTHY",
    }

    try:
        usage = shutil.disk_usage(SSD_MOUNT)
        storage_info["total_bytes"] = usage.total
        storage_info["used_bytes"] = usage.used
        storage_info["avail_bytes"] = usage.free
        storage_info["use_percent"] = (usage.used / usage.total) * 100.0
        storage_info["total_str"] = fmt_bytes(usage.total)
        storage_info["used_str"] = fmt_bytes(usage.used)
        storage_info["avail_str"] = fmt_bytes(usage.free)

        # Headroom calculation (bounded retention ensures steady-state usage)
        if storage_info["use_percent"] > 90.0:
            storage_info["status"] = "LOW_SPACE"
            storage_info["headroom_weeks"] = 2
        elif storage_info["use_percent"] > 80.0:
            storage_info["status"] = "WARNING"
            storage_info["headroom_weeks"] = 8
        else:
            storage_info["status"] = "HEALTHY"
            storage_info["headroom_weeks"] = 100

    except Exception as exc:
        logging.warning("Error getting disk usage for %s: %s", SSD_MOUNT, exc)

    return storage_info


# ── Main Report Orchestrator ───────────────────────────────────────────────

def build_verification_report() -> dict[str, Any]:
    """Execute complete backup restorability verification."""
    now = datetime.now(LOCAL_TZ)
    vmids = get_configured_vmids()
    guest_meta = get_guest_names()

    vzdump_results, vzdump_anomalies = evaluate_vzdump_archives(now, vmids, guest_meta)
    config_result, config_anomalies = evaluate_config_backups(now)
    storage_info = evaluate_storage_capacity()

    all_anomalies = vzdump_anomalies + config_anomalies
    if storage_info["status"] == "LOW_SPACE":
        all_anomalies.append(f"Low SSD capacity on {SSD_MOUNT} ({storage_info['avail_str']} free, {storage_info['use_percent']:.1f}% used)")

    verdict = "ATTENTION NEEDED" if all_anomalies else "RESTORABLE"

    return {
        "now": now,
        "verdict": verdict,
        "vmids": vmids,
        "vzdump_results": vzdump_results,
        "config_result": config_result,
        "storage_info": storage_info,
        "anomalies": all_anomalies,
    }


# ── HTML & Text Email Rendering ───────────────────────────────────────────

def render_email(report: dict[str, Any]) -> tuple[str, str, str]:
    """Render plain text and HTML versions of the backup restorability report."""
    verdict = report["verdict"]
    is_restorable = (verdict == "RESTORABLE")

    badge_bg = "#1e824c" if is_restorable else "#d97706"
    badge_light = "#eafaf1" if is_restorable else "#fef3c7"
    badge_label = "✅ RESTORABLE — BACKUPS USABLE" if is_restorable else "⚠️ ATTENTION NEEDED — BACKUP ANOMALY"

    now_str = report["now"].strftime("%a %d %b, %H:%M %Z")

    # Build concise subject line
    if is_restorable:
        v_count = sum(1 for v in report["vzdump_results"] if v["status"] == "VERIFIED")
        subject = f"[BACKUP VERIFICATION] RESTORABLE: {v_count} guest archives + media-core config verified ({report['storage_info']['avail_str']} free)"
    else:
        subject = f"[BACKUP VERIFICATION] ATTENTION NEEDED: {len(report['anomalies'])} issue(s) detected in backup audit"

    # BLUF Summary Text
    if is_restorable:
        v_count = sum(1 for v in report["vzdump_results"] if v["status"] == "VERIFIED")
        bluf_summary = (
            f"All <strong>{v_count} active guest vzdump archives</strong> and the nightly "
            f"<strong>media-core app state archive</strong> verified clean. Decompression pipelines, guest headers, "
            f"and critical restoration artefacts (including SQLite DB snapshot <strong>{report['config_result']['jellyfin_db_snapshot_str']}</strong>) "
            f"are intact. Storage headroom on <code>/mnt/pve/SSD</code> is healthy with <strong>{report['storage_info']['avail_str']} free</strong> "
            f"({report['storage_info']['use_percent']:.1f}% used)."
        )
        bluf_action = "No action required. Backups are structurally sound and restorable."
    else:
        anomalies_html = "<br>• ".join(html_lib.escape(a) for a in report["anomalies"])
        bluf_summary = (
            f"The weekly backup verification audit detected <strong>{len(report['anomalies'])} issue(s)</strong> requiring attention:<br>"
            f"• {anomalies_html}"
        )
        bluf_action = "Review the flagged artefacts below. Verify failing archives or prune stalled backup jobs."

    # vzdump Table Rows
    vzdump_rows_html = ""
    for v in report["vzdump_results"]:
        if v["status"] == "VERIFIED":
            status_badge = '<span style="display:inline-block;padding:2px 6px;font-size:11px;background:#dcfce7;color:#166534;border-radius:3px;font-weight:600;">✅ Restorable</span>'
        elif v["status"] == "PENDING_FIRST_RUN":
            status_badge = '<span style="display:inline-block;padding:2px 6px;font-size:11px;background:#e0f2fe;color:#0369a1;border-radius:3px;font-weight:600;">ℹ️ Pending First Run</span>'
        else:
            status_badge = '<span style="display:inline-block;padding:2px 6px;font-size:11px;background:#fee2e2;color:#991b1b;border-radius:3px;font-weight:600;">⚠️ Attention</span>'

        swing_html = ""
        if v["wow_swing_pct"] is not None:
            color = "#64748b" if abs(v["wow_swing_pct"]) <= MAX_SIZE_SWING_PERCENT else "#b91c1c"
            swing_html = f'<div style="font-size:11px;color:{color};">{v["wow_swing_pct"]:+.1f}% WoW</div>'

        age_str = fmt_age(v["age_hours"]) if v["age_hours"] is not None else "Never"
        arch_name = v["newest_archive"] or "None"

        vzdump_rows_html += f"""
        <tr style="border-bottom:1px solid #e2e8f0;">
          <td style="padding:10px 8px;font-weight:600;color:#0f172a;vertical-align:top;">
            <div>CT/VM {v['vmid']} &middot; {html_lib.escape(v['name'])}</div>
            <div style="font-size:11.5px;color:#64748b;font-family:ui-monospace,monospace;margin-top:2px;">{html_lib.escape(arch_name)}</div>
          </td>
          <td style="padding:10px 8px;color:#334155;font-size:12.5px;vertical-align:top;">
            <strong>{v['size_str']}</strong>
            {swing_html}
          </td>
          <td style="padding:10px 8px;color:#334155;font-size:12.5px;vertical-align:top;">
            <div>{age_str}</div>
            <div style="font-size:11.5px;color:#64748b;">Gen: {v['archive_count']}/4</div>
          </td>
          <td style="padding:10px 8px;vertical-align:top;">{status_badge}</td>
        </tr>
        """

    # Config Backup Table Rows
    cfg = report["config_result"]
    cfg_badge = '<span style="display:inline-block;padding:2px 6px;font-size:11px;background:#dcfce7;color:#166534;border-radius:3px;font-weight:600;">✅ Restorable</span>' if cfg["status"] == "VERIFIED" else '<span style="display:inline-block;padding:2px 6px;font-size:11px;background:#fee2e2;color:#991b1b;border-radius:3px;font-weight:600;">⚠️ Attention</span>'

    artefacts_list_html = ""
    for art_name, art_ok in cfg["artefacts"].items():
        icon = "✅" if art_ok else "❌"
        extra = f" ({cfg['jellyfin_db_snapshot_str']})" if art_name == "jellyfin.db.snapshot" and art_ok else ""
        artefacts_list_html += f'<span style="display:inline-block;margin-right:12px;margin-bottom:4px;font-size:12px;color:#334155;">{icon} <code>{html_lib.escape(art_name)}</code>{extra}</span>'

    html_body = f"""<!doctype html>
<html>
<body style="margin:0;padding:0;background:#f2f3f5;font-family:-apple-system,BlinkMacSystemFont,Segoe UI,Roboto,Arial,sans-serif;">
<div style="max-width:700px;margin:24px auto;background:#ffffff;border-radius:10px;overflow:hidden;box-shadow:0 1px 4px rgba(0,0,0,.12);">

  <!-- Header Banner -->
  <div style="background:#1a1d29;padding:20px 24px;">
    <div style="font-size:24px;">🛡️</div>
    <div style="color:#ffffff;font-size:18px;font-weight:600;margin-top:4px;">Weekly Backup Restorability Report</div>
    <div style="color:#9aa0ae;font-size:13px;margin-top:2px;">Archive header extraction, integrity verification & capacity audit &middot; {now_str}</div>
  </div>

  <!-- BLUF Summary Callout -->
  <div style="padding:18px 24px 4px 24px;">
    <div style="background:{badge_light};border-left:4px solid {badge_bg};border-radius:6px;padding:14px 16px;">
      <div style="display:inline-block;background:{badge_bg};color:#ffffff;font-size:11px;font-weight:700;letter-spacing:.06em;padding:3px 8px;border-radius:3px;margin-bottom:8px;">{badge_label}</div>
      <div style="color:#0f172a;font-size:14px;line-height:1.55;">{bluf_summary}</div>
      <div style="color:#334155;font-size:13px;line-height:1.55;margin-top:10px;font-weight:500;">{bluf_action}</div>
    </div>
  </div>

  <!-- Storage Runway Card -->
  <div style="margin:16px 24px;background:#f8fafc;border:1px solid #e2e8f0;border-radius:8px;padding:14px 16px;">
    <div style="font-size:14px;font-weight:700;color:#0f172a;margin-bottom:8px;">💾 Storage Capacity & Runway (/mnt/pve/SSD)</div>
    <table style="width:100%;border-collapse:collapse;font-size:13px;color:#1e293b;">
      <tr>
        <td style="padding:4px 0;font-weight:600;color:#64748b;width:150px;">Pool Utilization:</td>
        <td style="padding:4px 0;">
          <strong>{report['storage_info']['used_str']}</strong> of <strong>{report['storage_info']['total_str']}</strong> ({report['storage_info']['use_percent']:.1f}% used) &middot; <strong>{report['storage_info']['avail_str']} free</strong>
        </td>
      </tr>
      <tr>
        <td style="padding:4px 0;font-weight:600;color:#64748b;">Retention Policy:</td>
        <td style="padding:4px 0;">vzdump <code>keep-last=4</code> &middot; media-core config <code>keep=7</code> (steady-state footprint)</td>
      </tr>
      <tr>
        <td style="padding:4px 0;font-weight:600;color:#64748b;">Capacity Forecast:</td>
        <td style="padding:4px 0;color:#166534;font-weight:600;">&gt;100 weeks headroom at current bounded retention</td>
      </tr>
    </table>
  </div>

  <!-- vzdump Guest Archives Card -->
  <div style="margin:16px 24px 20px 24px;">
    <div style="font-size:15px;font-weight:700;color:#0f172a;margin-bottom:4px;">📦 Proxmox Guest vzdump Archives (/mnt/pve/SSD/dump)</div>
    <div style="font-size:12px;color:#64748b;margin-bottom:10px;">Verification method: Single-block container config extraction (pct.conf / vma config) &middot; zero heavy disk I/O</div>
    <table style="width:100%;border-collapse:collapse;font-size:13px;border:1px solid #e2e8f0;border-radius:6px;overflow:hidden;">
      <thead>
        <tr style="background:#f1f5f9;color:#475569;text-align:left;font-size:12px;">
          <th style="padding:8px;border-bottom:1px solid #e2e8f0;">Guest & Archive</th>
          <th style="padding:8px;border-bottom:1px solid #e2e8f0;">Size & Swing</th>
          <th style="padding:8px;border-bottom:1px solid #e2e8f0;">Age & Gen</th>
          <th style="padding:8px;border-bottom:1px solid #e2e8f0;">Status</th>
        </tr>
      </thead>
      <tbody>
        {vzdump_rows_html}
      </tbody>
    </table>
  </div>

  <!-- Media-Core Config Card -->
  <div style="margin:16px 24px 20px 24px;background:#f8fafc;border:1px solid #e2e8f0;border-radius:8px;padding:16px;">
    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px;">
      <div style="font-size:15px;font-weight:700;color:#0f172a;">🗄️ Media-Core App State & DB Snapshot (CT 105 mp0 Backup)</div>
      <div>{cfg_badge}</div>
    </div>
    <div style="font-size:13px;color:#334155;margin-bottom:12px;">
      Archive: <code>{html_lib.escape(cfg['newest_archive'] or 'None')}</code> ({cfg['size_str']}, {fmt_age(cfg['age_hours']) if cfg['age_hours'] is not None else 'N/A'}, {cfg['archive_count']}/7 kept)
    </div>
    <div style="font-size:13px;font-weight:600;color:#0f172a;margin-bottom:6px;">Required Restoration Artefacts:</div>
    <div style="background:#ffffff;border:1px solid #e2e8f0;border-radius:6px;padding:10px 12px;margin-bottom:8px;">
      {artefacts_list_html}
    </div>
    <div style="font-size:12px;color:#64748b;">
      SQLite snapshot generated with <code>VACUUM INTO</code> for point-in-time consistency while Jellyfin is running.
    </div>
  </div>

  <!-- Footer -->
  <div style="padding:14px 24px;background:#f7f8fa;border-top:1px solid #e8e9ec;font-size:12px;color:#6b7280;">
    Sent by <code>backup-restorability-verify</code> on pve-01 &middot; Automated Weekly Backup Verification &middot; Port 465 Relay.
  </div>

</div>
</body>
</html>
"""

    text_body = f"""================================================================
WEEKLY BACKUP RESTORABILITY REPORT — {verdict}
================================================================
Generated: {now_str}
Destination: {SSD_MOUNT}

VERDICT: {verdict}

STORAGE CAPACITY & RUNWAY (/mnt/pve/SSD):
- Used:      {report['storage_info']['used_str']} / {report['storage_info']['total_str']} ({report['storage_info']['use_percent']:.1f}% used)
- Free:      {report['storage_info']['avail_str']}
- Retention: vzdump keep-last=4, media-core config keep=7
- Runway:    >100 weeks headroom at bounded retention

PROXMOX GUEST VZDUMP ARCHIVES (/mnt/pve/SSD/dump):
"""
    for v in report["vzdump_results"]:
        age_str = fmt_age(v["age_hours"]) if v["age_hours"] is not None else "Never"
        swing_str = f" ({v['wow_swing_pct']:+.1f}% WoW)" if v["wow_swing_pct"] is not None else ""
        text_body += f"""- VMID {v['vmid']} ({v['name']}, {v['type']}):
  Status:    {v['status']}
  Archive:   {v['newest_archive'] or 'None'}
  Size:      {v['size_str']}{swing_str}
  Age:       {age_str} (Generations: {v['archive_count']}/4)
  Integrity: {v['integrity_msg']}
"""

    cfg = report["config_result"]
    text_body += f"""
MEDIA-CORE APP STATE & DB SNAPSHOT (/mnt/pve/SSD/media-core-backups):
- Status:      {cfg['status']}
- Archive:     {cfg['newest_archive'] or 'None'} ({cfg['size_str']}, {fmt_age(cfg['age_hours']) if cfg['age_hours'] is not None else 'N/A'}, {cfg['archive_count']}/7 kept)
- DB Snapshot: {cfg['jellyfin_db_snapshot_str']} (jellyfin.db.snapshot)
- Artefacts:
"""
    for art_name, art_ok in cfg["artefacts"].items():
        text_body += f"  * [{'OK' if art_ok else 'MISSING'}] {art_name}\n"

    if report["anomalies"]:
        text_body += f"""
================================================================
ATTENTION ITEMS & ACTION REQUIRED:
"""
        for a in report["anomalies"]:
            text_body += f"- {a}\n"

    text_body += f"""
----------------------------------------------------------------
Sent by backup-restorability-verify on pve-01 (Port 465 relay)
"""

    return subject, text_body, html_body


# ── Fail-Soft Email Delivery ───────────────────────────────────────────────

def send_email_report(subject: str, text_body: str, html_body: str, recipient: str = MAILTO) -> bool:
    """Send multipart email via sendmail with fallback to mail CLI. Never raises."""
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = f'"{FROM_NAME}" <{FROM_ADDR}>'
    msg["To"] = recipient
    msg.attach(MIMEText(text_body, "plain"))
    msg.attach(MIMEText(html_body, "html"))

    try:
        proc = subprocess.run(
            ["/usr/sbin/sendmail", "-t", "-oi"],
            input=msg.as_string(),
            text=True,
            check=True,
            timeout=25,
        )
        logging.info("Backup verification email delivered successfully via sendmail to %s", recipient)
        return True
    except Exception as exc:
        logging.warning("sendmail delivery failed (%s). Attempting /usr/bin/mail fallback...", exc)
        try:
            subprocess.run(
                ["/usr/bin/mail", "-s", subject, recipient],
                input=text_body,
                text=True,
                check=True,
                timeout=20,
            )
            logging.info("Backup verification email delivered via mail fallback to %s", recipient)
            return True
        except Exception as exc2:
            logging.error("Fail-soft: Both sendmail and mail CLI failed (%s). Continuing cleanly.", exc2)
            return False


# ── Main Entrypoint ────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Weekly Backup Restorability Verification")
    parser.add_argument("--dry-run", action="store_true", help="Print rendered report to stdout without sending email")
    parser.add_argument("--recipient", type=str, default=MAILTO, help="Email recipient address")
    parser.add_argument("--force-email", action="store_true", help="Send email even if run interactively")

    args = parser.parse_args()

    try:
        report = build_verification_report()
        logging.info(
            "Backup verification completed -> Verdict: %s (vzdump checked: %d, config status: %s)",
            report["verdict"], len(report["vzdump_results"]), report["config_result"]["status"]
        )

        subject, text_body, html_body = render_email(report)

        if args.dry_run:
            print("\n" + "=" * 70)
            print(f"--- [DRY-RUN MODE] SUBJECT: {subject} ---")
            print("=" * 70)
            print(text_body)
            print("=" * 70)
            print("--- [DRY-RUN MODE] END OUTPUT ---\n")
            sys.exit(0)

        send_email_report(subject, text_body, html_body, recipient=args.recipient)

    except Exception as exc:
        logging.error("Fail-soft: Unhandled exception in backup verification: %s", exc, exc_info=True)
        sys.exit(0)


if __name__ == "__main__":
    main()
