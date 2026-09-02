#!/usr/bin/env python3
"""DVR Post-Recording Report & Anomaly Investigator.

Observes completed Jellyfin DVR recordings on CT 105, verifies actual runtime
against scheduled window, evaluates comskip commercial removal results, tracks
any stall/restore/stitch events from sports-dvr-auto, and delivers a styled
HTML report email with BLUF verdict (GOOD / CHECK THIS / FAILED).

On any anomaly (short runtime, stall/restore failure, missing files, comskip
zero-detection on sports), dispatches AGY in diagnose-only mode to correlate
Jellyfin server logs, tuner contention (1 concurrent stream account), boot
history, and provider health before emailing findings to the owner.

Hard requirements:
- Idempotent: tracks state in /var/lib/dvr-recording-report/state.json.
- Fail-soft: never raises unhandled exceptions to avoid impacting DVR operations.
- Non-destructive: never deletes or moves any recording files.
- Waits for post-processing: only reports when comskip/stitch is verified done.
- Stdlib only: no external dependencies.
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
import time
import urllib.error
import urllib.parse
import urllib.request
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
FROM_NAME = "Media-Core DVR Reporter"

STATE_DIR = Path("/var/lib/dvr-recording-report")
STATE_FILE = STATE_DIR / "state.json"
PROMPTS_DIR = STATE_DIR / "prompts"

RECORDINGS_ROOT = "/srv/media-core/media/recordings"
CONTAINER_RECORDINGS = "/media/recordings"
PP_DIR = f"{RECORDINGS_ROOT}/.postprocess"
QUEUE_FILE = f"{PP_DIR}/queue"
LOCK_FILE = f"{PP_DIR}/.runner.lock"
COMSKIP_LOG = "/srv/media-core/comskip/logs/process-queue.log"
COMSKIP_LOGS_DIR = "/srv/media-core/comskip/logs"

JELLYFIN_KEY_FILES = [
    Path("/srv/media-core/.jellyfin_api_key"),
    Path("/config/.jellyfin_api_key"),
    Path("/var/lib/lxc/105/rootfs/srv/media-core/.jellyfin_api_key"),
]
JF_URLS = ["http://192.168.9.50:8096", "http://127.0.0.1:8096"]
JELLYFIN_TIMERS_PATH = "/srv/media-core/jellyfin/config/data/livetv/timers.json"

DVR_EVENTS_FILE = Path("/var/lib/dvr-dashboard/dvr-automation-events.jsonl")
DVR_RESTORE_STATE = Path("/var/lib/dvr-dashboard/recording-restore-state.json")

AGY_TASK = "/root/bin/agy-task.sh"
AGY_TIMEOUT_MIN = 10
DEFAULT_LOOKBACK_HOURS = 72

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)


# ── Jellyfin & Host Query Helpers ──────────────────────────────────────────

_token_cache: str | None = None


def get_jf_token() -> str:
    global _token_cache
    if _token_cache:
        return _token_cache
    for kf in JELLYFIN_KEY_FILES:
        if kf.exists():
            try:
                tok = kf.read_text().strip()
                if tok:
                    _token_cache = tok
                    return tok
            except Exception:
                pass
    try:
        res = subprocess.run(
            ["/usr/sbin/pct", "exec", "105", "--", "cat", "/srv/media-core/.jellyfin_api_key"],
            capture_output=True, text=True, timeout=10, check=True,
        )
        tok = res.stdout.strip()
        if tok:
            _token_cache = tok
            return tok
    except Exception:
        pass
    return ""


def jf_request(endpoint: str) -> Any:
    token = get_jf_token()
    headers = {
        "Content-Type": "application/json",
        "User-Agent": "DVRRecordingReport/1.0",
    }
    if token:
        headers["X-Emby-Token"] = token
        headers["Authorization"] = f'MediaBrowser Token="{token}"'

    for base in JF_URLS:
        url = f"{base}{endpoint}"
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = resp.read()
                return json.loads(data) if data else {}
        except Exception:
            continue
    return None


def get_channel_names() -> dict[str, str]:
    """Fetch channel list from Jellyfin API and return mapping to '<Number> <Name>'."""
    out: dict[str, str] = {}
    data = jf_request("/emby/LiveTv/Channels?Limit=2000")
    if not data or not isinstance(data, dict):
        return out
    for c in data.get("Items", []):
        num = str(c.get("ChannelNumber") or c.get("Number") or "").strip()
        name = (c.get("Name") or "").strip()
        cid = str(c.get("Id") or "").strip()
        display = f"{num} {name}".strip() if num else name
        if num:
            out[num] = display
            out[f"hdhr_{num}"] = display
        if cid:
            out[cid] = display
        if name:
            out[name] = display
            out[name.lower()] = display
    return out


def load_jellyfin_timers() -> list[dict[str, Any]]:
    """Load historical and active timers from CT 105 timers.json."""
    try:
        res = subprocess.run(
            ["/usr/sbin/pct", "exec", "105", "--", "cat", JELLYFIN_TIMERS_PATH],
            capture_output=True, text=True, timeout=15, check=True,
        )
        return json.loads(res.stdout)
    except Exception as exc:
        logging.warning("Could not read timers.json via pct exec: %s", exc)
        return []


def pct_exec(cmd: list[str], timeout: int = 30) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["/usr/sbin/pct", "exec", "105", "--", *cmd],
        capture_output=True, text=True, timeout=timeout,
    )


# ── Comskip & File Probing ─────────────────────────────────────────────────

def parse_comskip_log() -> dict[str, dict[str, Any]]:
    """Parse CT 105's process-queue.log to extract post-processing facts."""
    records: dict[str, dict[str, Any]] = {}
    try:
        res = pct_exec(["cat", COMSKIP_LOG], timeout=15)
        if res.returncode != 0:
            return records
        lines = res.stdout.splitlines()
    except Exception:
        return records

    for line in lines:
        line = line.strip()
        m_start = re.match(r"(\S+) COMSKIP start \((\d+)s\): (.+)", line)
        m_done = re.match(r"(\S+) DONE: (.+?) \((\d+)s, removed ([\d.]+) min of commercials\)", line)
        m_nocom = re.match(r"(\S+) NO COMMERCIALS detected \((\d+)s\) — no cut version made: (.+)", line)
        m_edl = re.match(r"(\S+) EDL: (\d+) commercial breaks, ([\d.]+) min to remove, (\d+) keep-segments", line)
        m_err = re.match(r"(\S+) ERROR \((.+?)\):? (.*)", line)

        if m_start:
            ts, dur, rel_path = m_start.groups()
            base = os.path.basename(rel_path)
            records[base] = {
                "start_ts": ts,
                "orig_dur_sec": float(dur),
                "rel_path": rel_path,
                "status": "STARTED",
                "breaks": 0,
                "removed_min": 0.0,
            }
        elif m_edl:
            ts, breaks, rem_min, keeps = m_edl.groups()
            if records:
                last_key = list(records.keys())[-1]
                records[last_key]["breaks"] = int(breaks)
                records[last_key]["removed_min"] = float(rem_min)
                records[last_key]["keep_segments"] = int(keeps)
        elif m_done:
            ts, out_path, out_dur, rem_min = m_done.groups()
            base = os.path.basename(out_path).replace(".mkv", ".ts")
            if base in records:
                records[base].update({
                    "done_ts": ts,
                    "status": "DONE",
                    "out_path": out_path,
                    "out_dur_sec": float(out_dur),
                    "removed_min": float(rem_min),
                })
            else:
                records[base] = {
                    "done_ts": ts,
                    "status": "DONE",
                    "out_path": out_path,
                    "out_dur_sec": float(out_dur),
                    "removed_min": float(rem_min),
                }
        elif m_nocom:
            ts, cut_sec, rel_path = m_nocom.groups()
            base = os.path.basename(rel_path)
            if base in records:
                records[base].update({
                    "done_ts": ts,
                    "status": "NO_COMMERCIALS",
                    "removed_sec": float(cut_sec),
                })
            else:
                records[base] = {
                    "done_ts": ts,
                    "status": "NO_COMMERCIALS",
                    "removed_sec": float(cut_sec),
                }
        elif m_err:
            ts, err_type, err_msg = m_err.groups()
            if records:
                last_key = list(records.keys())[-1]
                records[last_key]["status"] = f"ERROR: {err_type}"
                records[last_key]["error_detail"] = err_msg

    return records


def parse_edl_file(base_name: str) -> tuple[int, float] | None:
    """Read EDL file on CT 105 if present. Returns (breaks_count, total_seconds_removed)."""
    clean_base = os.path.splitext(base_name)[0]
    edl_path = f"{COMSKIP_LOGS_DIR}/{clean_base}.edl"
    res = pct_exec(["cat", edl_path], timeout=10)
    if res.returncode != 0:
        return None
    cuts = []
    for line in res.stdout.splitlines():
        parts = line.strip().split()
        if len(parts) >= 3 and parts[2] == "0":
            try:
                s, e = float(parts[0]), float(parts[1])
                if e > s:
                    cuts.append((s, e))
            except ValueError:
                continue
    total_sec = sum(e - s for s, e in cuts)
    return len(cuts), total_sec


def probe_file_info(container_path: str) -> dict[str, Any]:
    """Probe file existence, size, and duration on CT 105."""
    info: dict[str, Any] = {
        "exists": False,
        "size_bytes": 0,
        "duration_sec": 0.0,
        "path": container_path,
    }
    if not container_path:
        return info

    host_path = "/srv/media-core" + container_path if container_path.startswith("/media/") else container_path

    stat_res = pct_exec(["stat", "-c", "%s", host_path], timeout=10)
    if stat_res.returncode == 0 and stat_res.stdout.strip().isdigit():
        info["exists"] = True
        info["size_bytes"] = int(stat_res.stdout.strip())

    if not info["exists"]:
        return info

    probe_cmd = [
        "docker", "exec", "jellyfin",
        "/usr/lib/jellyfin-ffmpeg/ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        container_path,
    ]
    probe_res = pct_exec(probe_cmd, timeout=30)
    if probe_res.returncode == 0 and probe_res.stdout.strip():
        try:
            info["duration_sec"] = float(probe_res.stdout.strip())
        except ValueError:
            pass

    return info


def find_commercial_free_path(container_orig_path: str) -> str | None:
    """Find matching commercial free / cut MKV file path on CT 105."""
    if not container_orig_path or not container_orig_path.startswith(CONTAINER_RECORDINGS):
        return None
    rel = container_orig_path[len(CONTAINER_RECORDINGS):].lstrip("/")
    base_mkv = os.path.splitext(os.path.basename(rel))[0] + ".mkv"
    parent_rel = os.path.dirname(rel)

    # Candidate 1: /media/recordings/Commercial Free/<Category>/.../<file>.mkv
    cand1 = f"{CONTAINER_RECORDINGS}/Commercial Free/{parent_rel}/{base_mkv}"
    cand1_host = f"{RECORDINGS_ROOT}/Commercial Free/{parent_rel}/{base_mkv}"

    # Candidate 2: /media/recordings/<Category> (No Commercials)/.../<file>.mkv
    parts = rel.split("/")
    cand2_parts = [parts[0] + " (No Commercials)"] + parts[1:-1] + [base_mkv]
    cand2 = f"{CONTAINER_RECORDINGS}/" + "/".join(cand2_parts)
    cand2_host = f"{RECORDINGS_ROOT}/" + "/".join(cand2_parts)

    for cand_c, cand_h in [(cand1, cand1_host), (cand2, cand2_host)]:
        check = pct_exec(["test", "-f", cand_h], timeout=10)
        if check.returncode == 0:
            return cand_c

    # Candidate 3: Any .mkv in the Commercial Free parent folder
    cand3_dir = f"{RECORDINGS_ROOT}/Commercial Free/{parent_rel}"
    list_check = pct_exec(["find", cand3_dir, "-maxdepth", "1", "-name", "*.mkv"], timeout=10)
    if list_check.returncode == 0 and list_check.stdout.strip():
        first_mkv = list_check.stdout.strip().splitlines()[0]
        return first_mkv.replace("/srv/media-core", "")

    return None


# ── Automation Events & Stalls ─────────────────────────────────────────────

def load_automation_events(timer_id: str, file_name: str) -> list[dict[str, Any]]:
    """Gather automation events relevant to this recording/timer."""
    events = []
    if not DVR_EVENTS_FILE.exists():
        return events
    try:
        with open(DVR_EVENTS_FILE, "r", encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                try:
                    ev = json.loads(line)
                except Exception:
                    continue
                ev_tid = ev.get("timer_id")
                ev_path = ev.get("file_path") or ev.get("path") or ""
                if (ev_tid and ev_tid == timer_id) or (file_name and file_name in ev_path):
                    events.append(ev)
    except Exception as exc:
        logging.warning("Error reading dvr-automation-events.jsonl: %s", exc)
    return events


def load_restore_chain(timer_id: str) -> dict[str, Any] | None:
    """Check recording-restore-state.json for stall/restore chain info."""
    if not DVR_RESTORE_STATE.exists():
        return None
    try:
        data = json.loads(DVR_RESTORE_STATE.read_text(encoding="utf-8"))
        if timer_id in data:
            return data[timer_id]
        for orig_id, info in data.items():
            if info.get("restore_timer_id") == timer_id:
                return info
    except Exception as exc:
        logging.warning("Error reading recording-restore-state.json: %s", exc)
    return None


# ── State Tracking & Idempotence ───────────────────────────────────────────

def load_state() -> dict[str, Any]:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except Exception as exc:
            logging.warning("Could not read state file %s: %s", STATE_FILE, exc)
    return {"reported": {}}


def save_state(state: dict[str, Any]) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = STATE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2), encoding="utf-8")
    tmp.replace(STATE_FILE)


# ── Post-Processing Completion Gate ────────────────────────────────────────

def is_postprocessing_complete(
    timer: dict[str, Any],
    comskip_records: dict[str, dict[str, Any]],
) -> tuple[bool, str]:
    """Determine if a recording and all its post-processing steps are fully done.

    Returns (is_done, reason_string).
    """
    rec_path = timer.get("RecordingPath") or ""
    if not rec_path:
        return False, "No recording path on timer"

    base = os.path.basename(rec_path)
    host_path = "/srv/media-core" + rec_path if rec_path.startswith("/media/") else rec_path

    # 1. Check if timer is still active / InProgress
    status = timer.get("Status", "")
    if status == "InProgress":
        return False, "Timer is still actively recording"

    # 2. Check if recording file exists on disk
    stat_res = pct_exec(["stat", "-c", "%s %Y", host_path], timeout=10)
    if stat_res.returncode != 0:
        return False, "Recording file not found on disk"

    # 3. Check if file is currently present in comskip queue
    queue_res = pct_exec(["cat", QUEUE_FILE], timeout=10)
    if queue_res.returncode == 0:
        queued_lines = [ln.strip() for ln in queue_res.stdout.splitlines() if ln.strip()]
        if rec_path in queued_lines or host_path in queued_lines:
            return False, "File is queued in comskip post-process queue"

    # 4. Check if comskip runner lock or active work dir exists for this file
    clean_base = re.sub(r"[^A-Za-z0-9._-]", "_", os.path.splitext(base)[0])[:80]
    work_check = pct_exec(["test", "-d", f"{PP_DIR}/work/{clean_base}"], timeout=10)
    if work_check.returncode == 0:
        return False, "Comskip work directory actively present"

    # 5. Check if restore state is pending
    restore_info = load_restore_chain(timer.get("Id", ""))
    if restore_info and restore_info.get("status") == "restoring":
        return False, "Auto-restore continuation in progress"

    # 6. Check comskip terminal status
    comskip_info = comskip_records.get(base)
    if comskip_info and comskip_info.get("status") in ("DONE", "NO_COMMERCIALS"):
        return True, f"Comskip completed ({comskip_info.get('status')})"

    cut_path = find_commercial_free_path(rec_path)
    if cut_path:
        return True, "Commercial-free cut file confirmed on disk"

    # 7. Check time elapsed since end date
    end_str = timer.get("EndDate", "")
    if end_str:
        try:
            end_dt = datetime.fromisoformat(end_str.replace("Z", "+00:00"))
            post_pad = timer.get("PostPaddingSeconds") or 0
            effective_end = end_dt + timedelta(seconds=post_pad)
            now_utc = datetime.now(timezone.utc)
            if now_utc > (effective_end + timedelta(minutes=15)):
                return True, "Grace period elapsed (no comskip queued)"
        except Exception:
            pass

    return False, "Post-processing completion not yet confirmed"


# ── Recording Evaluation & Verdict ─────────────────────────────────────────

def fmt_dur(sec: float) -> str:
    """Format seconds into readable 'Xh Ym Zs' or 'Xm Ys'."""
    sec = max(0.0, sec)
    h = int(sec // 3600)
    m = int((sec % 3600) // 60)
    s = int(sec % 60)
    if h > 0:
        return f"{h}h {m:02d}m {s:02d}s" if s > 0 else f"{h}h {m:02d}m"
    return f"{m}m {s:02d}s"


def fmt_bytes(b: int) -> str:
    if b >= 1024 * 1024 * 1024:
        return f"{b / (1024**3):.2f} GB"
    return f"{b / (1024**2):.1f} MB"


def evaluate_recording(
    timer: dict[str, Any],
    chmap: dict[str, str],
    comskip_records: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Comprehensive evaluation of a finished recording."""
    tid = timer.get("Id", "")
    name = timer.get("Name", "Untitled Recording")
    subtitle = timer.get("Overview") or timer.get("EpisodeTitle") or ""
    raw_cid = str(timer.get("ChannelId") or "")
    ch_display = chmap.get(raw_cid) or chmap.get(raw_cid.replace("hdhr_", "")) or raw_cid
    is_sports = bool(timer.get("IsSports") or "Sports" in str(timer.get("Tags", [])) or "Sports" in str(timer.get("Genres", [])))

    start_utc = datetime.fromisoformat(timer["StartDate"].replace("Z", "+00:00"))
    end_utc = datetime.fromisoformat(timer["EndDate"].replace("Z", "+00:00"))
    pre_pad = timer.get("PrePaddingSeconds") or 0
    post_pad = timer.get("PostPaddingSeconds") or 0
    program_dur_sec = (end_utc - start_utc).total_seconds()
    sched_dur_sec = program_dur_sec + pre_pad + post_pad

    start_local = start_utc.astimezone(LOCAL_TZ)
    end_local = (end_utc + timedelta(seconds=post_pad)).astimezone(LOCAL_TZ)

    rec_path = timer.get("RecordingPath") or ""
    base_name = os.path.basename(rec_path)
    orig_info = probe_file_info(rec_path)

    # Cut file info
    cut_path = find_commercial_free_path(rec_path)
    cut_info = probe_file_info(cut_path) if cut_path else {"exists": False, "size_bytes": 0, "duration_sec": 0.0, "path": None}

    # Comskip info from logs / EDL
    comskip_entry = comskip_records.get(base_name, {})
    edl_res = parse_edl_file(base_name)
    breaks_detected = comskip_entry.get("breaks") or (edl_res[0] if edl_res else 0)
    removed_sec = (comskip_entry.get("removed_min", 0.0) * 60.0) or (edl_res[1] if edl_res else 0.0)

    # Actual duration from comskip or ffprobe
    actual_dur = orig_info["duration_sec"]
    if comskip_entry.get("orig_dur_sec"):
        actual_dur = comskip_entry["orig_dur_sec"]
    elif cut_info.get("duration_sec") and removed_sec > 0:
        actual_dur = cut_info["duration_sec"] + removed_sec

    # Automation events (stalls / restores / stitches)
    auto_events = load_automation_events(tid, base_name)
    restore_info = load_restore_chain(tid)

    stall_count = sum(1 for e in auto_events if e.get("event") == "stall_detected")
    restore_count = sum(1 for e in auto_events if e.get("event") == "restore_triggered")
    stitch_failed = any(e.get("event") == "stitch_failed" for e in auto_events)
    stitch_completed = any(e.get("event") == "stitch_completed" for e in auto_events)

    # Duration comparison baseline: evaluate against program duration (or scheduled if no padding)
    baseline_dur = program_dur_sec if program_dur_sec > 0 else sched_dur_sec
    runtime_ratio = (actual_dur / baseline_dur) if baseline_dur > 0 else 0.0

    # Determine Verdict
    verdict = "GOOD"
    anomaly_reasons = []

    # 1. Critical Failure Checks
    if not orig_info["exists"]:
        verdict = "FAILED"
        anomaly_reasons.append("Original recording file is missing on disk.")
    elif orig_info["size_bytes"] < 10 * 1024 * 1024 and baseline_dur > 600:
        verdict = "FAILED"
        anomaly_reasons.append(f"Recording file is severely undersized ({fmt_bytes(orig_info['size_bytes'])}).")
    elif runtime_ratio < 0.80:
        verdict = "FAILED"
        anomaly_reasons.append(f"Recording runtime ({fmt_dur(actual_dur)}) is critically short ({runtime_ratio*100:.1f}% of scheduled program {fmt_dur(baseline_dur)}).")
    elif stitch_failed:
        verdict = "FAILED"
        anomaly_reasons.append("Multi-segment stitch failed following stream stall.")

    # 2. Warning / Check This Checks
    if verdict != "FAILED":
        if 0.80 <= runtime_ratio < 0.95:
            verdict = "CHECK THIS"
            anomaly_reasons.append(f"Recording ended slightly early ({runtime_ratio*100:.1f}% of scheduled program window).")
        if stall_count > 0:
            verdict = "CHECK THIS"
            anomaly_reasons.append(f"Stream drop occurred ({stall_count} stall event(s) recorded).")
        if is_sports and breaks_detected == 0 and actual_dur > 1800:
            verdict = "CHECK THIS"
            anomaly_reasons.append("Comskip detected 0 commercial breaks on sports broadcast.")
        if comskip_entry.get("status", "").startswith("ERROR"):
            verdict = "CHECK THIS"
            anomaly_reasons.append(f"Comskip post-processing encountered error: {comskip_entry.get('status')}.")

    return {
        "timer_id": tid,
        "title": name,
        "subtitle": subtitle,
        "channel": ch_display,
        "is_sports": is_sports,
        "start_local": start_local,
        "end_local": end_local,
        "program_dur_sec": program_dur_sec,
        "scheduled_dur_sec": sched_dur_sec,
        "actual_dur_sec": actual_dur,
        "runtime_ratio": runtime_ratio,
        "orig_path": rec_path,
        "orig_size_bytes": orig_info["size_bytes"],
        "cut_path": cut_path,
        "cut_size_bytes": cut_info["size_bytes"],
        "cut_dur_sec": cut_info["duration_sec"],
        "comskip_status": comskip_entry.get("status", "NO_LOG"),
        "breaks_detected": breaks_detected,
        "commercial_removed_sec": removed_sec,
        "stall_count": stall_count,
        "restore_count": restore_count,
        "stitch_completed": stitch_completed,
        "stitch_failed": stitch_failed,
        "restore_info": restore_info,
        "verdict": verdict,
        "anomaly_reasons": anomaly_reasons,
    }


# ── AGY Post-Mortem Investigation Dispatch ────────────────────────────────

def split_plain_summary(body_text: str) -> tuple[str | None, str]:
    summary_re = re.compile(r"^##\s*Plain-English Summary\s*$(.*?)(?=^##\s|\Z)", re.MULTILINE | re.DOTALL)
    tech_re = re.compile(r"^##\s*Technical Details\s*$\n?", re.MULTILINE)
    m = summary_re.search(body_text)
    if not m:
        return None, body_text
    summary = m.group(1).strip()
    rest = (body_text[: m.start()] + body_text[m.end() :]).strip()
    rest = tech_re.sub("", rest, count=1).strip()
    return (summary or None), (rest or body_text)


def dispatch_agy_diagnosis(res: dict[str, Any]) -> dict[str, str] | None:
    """Dispatch AGY in diagnose-only mode to investigate a recording anomaly."""
    slug_title = "".join(c if c.isalnum() else "-" for c in res["title"].lower())[:30].strip("-") or "dvr"
    slug = f"dvr-diag-{slug_title}-{int(time.time())}"
    PROMPTS_DIR.mkdir(parents=True, exist_ok=True)
    prompt_file = PROMPTS_DIR / f"{slug}.prompt.md"

    prompt_content = f"""A DVR recording anomaly was detected on pve-01 (CT 105 media-core).

Recording Details:
- Title: {res['title']}
- Subtitle / Fixture: {res['subtitle']}
- Channel: {res['channel']}
- Scheduled Window: {res['start_local'].strftime('%Y-%m-%d %H:%M')} to {res['end_local'].strftime('%H:%M %Z')} (Expected: {fmt_dur(res['program_dur_sec'])})
- Actual Duration: {fmt_dur(res['actual_dur_sec'])} ({res['runtime_ratio']*100:.1f}% of scheduled window)
- Original File: {res['orig_path']} ({fmt_bytes(res['orig_size_bytes'])})
- Commercial-Free Cut: {res['cut_path'] or 'None produced'} ({fmt_bytes(res['cut_size_bytes']) if res['cut_path'] else 'N/A'})
- Comskip Status: {res['comskip_status']} ({res['breaks_detected']} breaks, {res['commercial_removed_sec']/60:.1f}m removed)
- Stalls / Restores: {res['stall_count']} stalls, {res['restore_count']} restores
- Detected Anomalies: {'; '.join(res['anomaly_reasons'])}

Investigate the root cause of this anomaly on pve-01 / CT 105.
Do NOT modify any files, restart services, or apply changes. This is a DIAGNOSIS ONLY.

Correlate at minimum:
1. Jellyfin recording & server logs in CT 105 (/srv/media-core/jellyfin/config/log/) around the recording window for socket disconnects, tuner errors, or stream resets.
2. Tuner contention on the single IPTV stream (Threadfin logs, overlapping timers in timers.json, or live viewing sessions during that window).
3. Host boot / uptime history (journalctl --list-boots, power timer schedule off 22:24-05:05 Europe/Berlin).
4. Provider stream health and network logs.

REQUIRED FORMAT:
Begin your report with a section headed exactly:
## Plain-English Summary
Containing 3-5 concise sentences explaining what happened in plain homeowner language, why it occurred, and what (if anything) should be done next.

Follow with:
## Technical Details
Containing your full technical investigation, log excerpts, and root cause evidence.
"""
    prompt_file.write_text(prompt_content, encoding="utf-8")

    logging.info("Dispatching AGY diagnosis for '%s' (slug: %s)...", res["title"], slug)
    try:
        proc = subprocess.run(
            [AGY_TASK, "run", slug, "diagnose", f"@{prompt_file}", "--timeout", f"{AGY_TIMEOUT_MIN}m"],
            capture_output=True, text=True, timeout=(AGY_TIMEOUT_MIN + 2) * 60,
        )
    except Exception as exc:
        logging.warning("Failed to dispatch AGY: %s", exc)
        return None

    report_path = None
    for line in proc.stdout.splitlines():
        line = line.strip()
        if line.startswith("/root/agy-reports/") and line.endswith(".md"):
            report_path = line

    if not report_path:
        matches = list(Path("/root/agy-reports").glob(f"*{slug}*.md"))
        if matches:
            report_path = str(sorted(matches)[-1])

    if report_path and Path(report_path).exists():
        raw_report = Path(report_path).read_text(encoding="utf-8")
        summary, technical = split_plain_summary(raw_report)
        return {
            "full_report": raw_report,
            "summary": summary or "Root-cause investigation completed.",
            "technical": technical,
            "report_path": report_path,
        }

    return None


# ── HTML Email Rendering ───────────────────────────────────────────────────

def render_email(res: dict[str, Any], agy_diag: dict[str, str] | None = None) -> tuple[str, str, str]:
    """Render plain text and HTML versions of the DVR recording report."""
    verdict = res["verdict"]
    badge_colors = {
        "GOOD": ("#1e824c", "#eafaf1", "✅ GOOD — RECORDING HEALTHY"),
        "CHECK THIS": ("#d97706", "#fef3c7", "⚠️ CHECK THIS — ANOMALY DETECTED"),
        "FAILED": ("#c0392b", "#fdecea", "❌ FAILED — RECORDING DEFECTIVE"),
    }
    accent_color, bg_color, badge_text = badge_colors.get(verdict, ("#6b7280", "#f3f4f6", verdict))

    sched_str = f"{fmt_dur(res['program_dur_sec'])} ({res['start_local'].strftime('%a %d %b, %H:%M')} – {res['end_local'].strftime('%H:%M %Z')})"
    actual_str = f"{fmt_dur(res['actual_dur_sec'])} ({res['runtime_ratio']*100:.1f}% of scheduled program)"

    subject = f"[DVR REPORT] {verdict}: {res['title']} ({res['channel']})"

    # Summary BLUF text
    if verdict == "GOOD":
        bluf_summary = (
            f"Recording of <strong>{html_lib.escape(res['title'])}</strong> on <strong>{html_lib.escape(res['channel'])}</strong> "
            f"completed successfully with full scheduled runtime (<strong>{actual_str}</strong>). "
            f"Commercial detection removed <strong>{res['commercial_removed_sec']/60:.1f} min</strong> across "
            f"<strong>{res['breaks_detected']} breaks</strong>. Both original and cut versions are ready."
        )
        bluf_action = "No action required. Media is intact and ready for viewing."
    elif verdict == "CHECK THIS":
        reasons_html = " ".join(f"• {r}" for r in res["anomaly_reasons"])
        bluf_summary = (
            f"Recording of <strong>{html_lib.escape(res['title'])}</strong> on <strong>{html_lib.escape(res['channel'])}</strong> "
            f"completed but flagged anomalies: {html_lib.escape(reasons_html)} "
            f"Recorded runtime: <strong>{actual_str}</strong>."
        )
        bluf_action = "Review the findings below. If video was clipped or ads were missed, inspect the preserved original."
    else:
        reasons_html = " ".join(f"• {r}" for r in res["anomaly_reasons"])
        bluf_summary = (
            f"Recording of <strong>{html_lib.escape(res['title'])}</strong> on <strong>{html_lib.escape(res['channel'])}</strong> "
            f"failed quality checks: {html_lib.escape(reasons_html)} "
            f"Only <strong>{actual_str}</strong> was captured."
        )
        bluf_action = "Inspection needed. See automated root-cause post-mortem below for tuner/stream diagnostics."

    # AGY Section HTML
    agy_html = ""
    if agy_diag:
        summary_escaped = html_lib.escape(agy_diag.get("summary", ""))
        tech_escaped = html_lib.escape(agy_diag.get("technical", ""))
        agy_html = f"""
  <!-- AGY Root-Cause Investigation Card -->
  <div style="margin:0 24px 18px 24px;">
    <div style="font-size:12px;font-weight:700;letter-spacing:.04em;color:#475569;text-transform:uppercase;margin-bottom:8px;">
      🔎 Automated Root-Cause Post-Mortem (AGY Diagnosis)
    </div>
    <div style="background:#eef4ff;border-left:4px solid #2f6fed;border-radius:6px;padding:14px 16px;margin-bottom:10px;">
      <div style="color:#1d4ed8;font-size:11px;font-weight:700;letter-spacing:.08em;margin-bottom:6px;">PLAIN-ENGLISH DIAGNOSIS</div>
      <div style="color:#0f172a;font-size:14px;line-height:1.55;">{summary_escaped}</div>
    </div>
    <div style="font-size:11.5px;font-weight:700;letter-spacing:.04em;text-transform:uppercase;color:#8a8f9c;margin:4px 0 6px 2px;">Technical Findings & Log Evidence</div>
    <pre style="background:#0f1117;color:#d7dae0;font-size:12px;line-height:1.5;padding:14px;border-radius:6px;overflow-x:auto;white-space:pre-wrap;word-break:break-word;font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;margin:0;">{tech_escaped}</pre>
  </div>"""

    # Commercial Free Details
    if res["cut_path"]:
        cut_row = f"""
      <tr>
        <td style="padding:6px 0;font-weight:600;color:#64748b;">Commercial-Free Cut:</td>
        <td style="padding:6px 0;font-weight:600;color:#1e824c;">
          {fmt_dur(res['cut_dur_sec'])} &middot; {fmt_bytes(res['cut_size_bytes'])}
          <div style="font-size:11.5px;color:#64748b;font-family:ui-monospace,monospace;margin-top:2px;">{html_lib.escape(res['cut_path'])}</div>
        </td>
      </tr>"""
        comskip_detail = (
            f"Detected <strong>{res['breaks_detected']} commercial breaks</strong>, removing "
            f"<strong>{res['commercial_removed_sec']/60:.1f} min</strong> of ads. Output duration: <strong>{fmt_dur(res['cut_dur_sec'])}</strong>."
        )
    else:
        cut_row = f"""
      <tr>
        <td style="padding:6px 0;font-weight:600;color:#64748b;">Commercial-Free Cut:</td>
        <td style="padding:6px 0;color:#64748b;">None produced (Comskip status: {html_lib.escape(res['comskip_status'])})</td>
      </tr>"""
        comskip_detail = f"Comskip status: <code>{html_lib.escape(res['comskip_status'])}</code>. No cut file produced."

    # Stalls Detail
    if res["stall_count"] > 0:
        stall_detail = f"⚠️ {res['stall_count']} stream stall(s) detected. Restores: {res['restore_count']}. Stitch status: {'Completed' if res['stitch_completed'] else ('Failed' if res['stitch_failed'] else 'None')}."
    else:
        stall_detail = "None (continuous stream capture)."

    html_body = f"""<!doctype html>
<html>
<body style="margin:0;padding:0;background:#f2f3f5;font-family:-apple-system,BlinkMacSystemFont,Segoe UI,Roboto,Arial,sans-serif;">
<div style="max-width:680px;margin:24px auto;background:#ffffff;border-radius:10px;overflow:hidden;box-shadow:0 1px 4px rgba(0,0,0,.12);">
  
  <!-- Header Banner -->
  <div style="background:#1a1d29;padding:20px 24px;">
    <div style="font-size:24px;">📼</div>
    <div style="color:#ffffff;font-size:18px;font-weight:600;margin-top:4px;">DVR Post-Recording Report</div>
    <div style="color:#9aa0ae;font-size:13px;margin-top:2px;">Recording quality audit, commercial analysis & anomaly diagnosis</div>
  </div>

  <!-- BLUF Summary Callout -->
  <div style="padding:18px 24px 4px 24px;">
    <div style="background:{bg_color};border-left:4px solid {accent_color};border-radius:6px;padding:14px 16px;">
      <div style="display:inline-block;background:{accent_color};color:#ffffff;font-size:11px;font-weight:700;letter-spacing:.06em;padding:3px 8px;border-radius:3px;margin-bottom:8px;">{badge_text}</div>
      <div style="color:#0f172a;font-size:14.5px;line-height:1.55;">{bluf_summary}</div>
      <div style="color:#334155;font-size:13px;line-height:1.55;margin-top:8px;font-weight:500;">{bluf_action}</div>
    </div>
  </div>

  <!-- Title & Channel Header -->
  <div style="padding:16px 24px 8px 24px;">
    <div style="font-size:18px;font-weight:700;color:#0f172a;">{html_lib.escape(res['title'])}</div>
    {f'<div style="font-size:13.5px;color:#475569;margin-top:2px;">{html_lib.escape(res["subtitle"])}</div>' if res['subtitle'] else ''}
  </div>

  <!-- Key Attributes Table Card -->
  <div style="margin:0 24px 16px 24px;background:#f8fafc;border:1px solid #e2e8f0;border-radius:8px;padding:16px;">
    <table style="width:100%;border-collapse:collapse;font-size:13.5px;color:#1e293b;">
      <tr>
        <td style="padding:6px 0;font-weight:600;width:160px;color:#64748b;">Channel:</td>
        <td style="padding:6px 0;font-weight:700;color:#0f172a;">{html_lib.escape(res['channel'])}</td>
      </tr>
      <tr>
        <td style="padding:6px 0;font-weight:600;color:#64748b;">Scheduled Program:</td>
        <td style="padding:6px 0;color:#0f172a;">{html_lib.escape(sched_str)}</td>
      </tr>
      <tr>
        <td style="padding:6px 0;font-weight:600;color:#64748b;">Actual Runtime:</td>
        <td style="padding:6px 0;font-weight:700;color:{accent_color};">{actual_str}</td>
      </tr>
      <tr>
        <td style="padding:6px 0;font-weight:600;color:#64748b;">Original Preserved:</td>
        <td style="padding:6px 0;">
          <strong>{fmt_dur(res['actual_dur_sec'])}</strong> &middot; {fmt_bytes(res['orig_size_bytes'])}
          <div style="font-size:11.5px;color:#64748b;font-family:ui-monospace,monospace;margin-top:2px;">{html_lib.escape(res['orig_path'])}</div>
        </td>
      </tr>
      {cut_row}
      <tr>
        <td style="padding:6px 0;font-weight:600;color:#64748b;">Stall / Stitch Activity:</td>
        <td style="padding:6px 0;color:#334155;">{html_lib.escape(stall_detail)}</td>
      </tr>
    </table>
  </div>

  <!-- Comskip & Preservation Card -->
  <div style="margin:0 24px 16px 24px;background:#f1f5f9;border-left:3px solid #0ea5e9;border-radius:4px;padding:12px 16px;font-size:13px;color:#334155;line-height:1.55;">
    <div><strong>Commercial Detection:</strong> {comskip_detail}</div>
    <div style="margin-top:6px;font-size:12px;color:#475569;">💾 <strong>File Preservation:</strong> Both original (.ts) and commercial-free (.mkv) files are preserved in separate library folders.</div>
  </div>

  {agy_html}

  <!-- Footer -->
  <div style="padding:14px 24px;background:#f7f8fa;border-top:1px solid #e8e9ec;font-size:12px;color:#6b7280;">
    Sent by <code>dvr-recording-report</code> on pve-01 &middot; Automated Post-Recording Quality Verification &middot; Standalone reporting path.
  </div>

</div>
</body>
</html>
"""

    text_body = f"""================================================================
DVR RECORDING REPORT — {verdict}
================================================================

Title:            {res['title']}
Subtitle:         {res['subtitle']}
Channel:          {res['channel']}
Verdict:          {verdict}
Scheduled Window: {sched_str}
Actual Runtime:   {actual_str}

FILES ON DISK:
- Original (.ts):         {res['orig_path']} ({fmt_bytes(res['orig_size_bytes'])}, {fmt_dur(res['actual_dur_sec'])})
- Commercial-Free (.mkv): {res['cut_path'] or 'None'} ({fmt_bytes(res['cut_size_bytes']) if res['cut_path'] else 'N/A'})
* Note: Both original and commercial-free files are permanently preserved.

POST-PROCESSING:
- Comskip Status:         {res['comskip_status']}
- Commercials Removed:    {res['breaks_detected']} breaks, {res['commercial_removed_sec']/60:.1f} min
- Stalls / Restores:      {stall_detail}
"""
    if agy_diag:
        text_body += f"""
----------------------------------------------------------------
AGY ROOT-CAUSE INVESTIGATION
----------------------------------------------------------------
Summary:
{agy_diag.get('summary', '')}

Technical Details:
{agy_diag.get('technical', '')}
"""
    text_body += f"""
----------------------------------------------------------------
Sent by dvr-recording-report on pve-01
"""

    return subject, text_body, html_body


# ── Fail-Soft Email Sender ─────────────────────────────────────────────────

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
        logging.info("Report email delivered successfully via sendmail to %s", recipient)
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
            logging.info("Report email delivered via mail fallback to %s", recipient)
            return True
        except Exception as exc2:
            logging.error("Fail-soft: Both sendmail and mail CLI failed (%s). Continuing cleanly.", exc2)
            return False


# ── Main Orchestration ─────────────────────────────────────────────────────

def run_reporting_cycle(
    dry_run: bool = False,
    target_rec: str | None = None,
    force: bool = False,
    no_agy: bool = False,
    all_history: bool = False,
    recipient: str = MAILTO,
) -> int:
    """Scan recordings, evaluate finished items, dispatch agy on anomalies, send email."""
    state = load_state()
    reported = state.get("reported", {})

    chmap = get_channel_names()
    timers = load_jellyfin_timers()
    comskip_records = parse_comskip_log()

    now_utc = datetime.now(timezone.utc)
    lookback_cutoff = now_utc - timedelta(hours=DEFAULT_LOOKBACK_HOURS)

    candidates = []
    for t in timers:
        tid = t.get("Id", "")
        rpath = t.get("RecordingPath", "")
        if not rpath:
            continue
        if tid in reported and not force:
            continue
        if target_rec:
            if target_rec != tid and target_rec not in rpath:
                continue
            candidates.append(t)
        else:
            # Filter to recent window if not running for all history
            if not all_history:
                end_str = t.get("EndDate", "")
                if end_str:
                    try:
                        end_dt = datetime.fromisoformat(end_str.replace("Z", "+00:00"))
                        if end_dt < lookback_cutoff and not force:
                            continue
                    except Exception:
                        pass
            candidates.append(t)

    logging.info("Found %d recording candidate(s) to evaluate.", len(candidates))
    reports_sent = 0

    for t in candidates:
        tid = t.get("Id", "")
        rpath = t.get("RecordingPath", "")

        is_done, reason = is_postprocessing_complete(t, comskip_records)
        if not is_done and not force and not target_rec:
            logging.info("Skipping '%s' (%s): %s", t.get("Name"), tid, reason)
            continue

        eval_res = evaluate_recording(t, chmap, comskip_records)
        verdict = eval_res["verdict"]
        logging.info("Evaluated '%s' -> Verdict: %s (Runtime: %s, Ratio: %.1f%%)",
                     eval_res["title"], verdict, fmt_dur(eval_res["actual_dur_sec"]),
                     eval_res["runtime_ratio"] * 100)

        agy_diag = None
        if verdict in ("CHECK THIS", "FAILED") and not no_agy and not dry_run:
            agy_diag = dispatch_agy_diagnosis(eval_res)
        elif verdict in ("CHECK THIS", "FAILED") and no_agy:
            logging.info("AGY diagnosis skipped via --no-agy.")

        subject, text_body, html_body = render_email(eval_res, agy_diag)

        if dry_run:
            print("\n" + "=" * 70)
            print(f"--- [DRY-RUN MODE] SUBJECT: {subject} ---")
            print("=" * 70)
            print(text_body)
            print("=" * 70)
            print("--- [DRY-RUN MODE] END OUTPUT ---\n")
            reports_sent += 1
            continue

        success = send_email_report(subject, text_body, html_body, recipient)
        if success or not dry_run:
            reported[tid] = {
                "timestamp": datetime.now(timezone.utc).timestamp(),
                "verdict": verdict,
                "title": eval_res["title"],
                "channel": eval_res["channel"],
                "orig_path": eval_res["orig_path"],
                "cut_path": eval_res["cut_path"],
                "runtime_ratio": eval_res["runtime_ratio"],
            }
            state["reported"] = reported
            save_state(state)
            reports_sent += 1

    return reports_sent


def main() -> None:
    parser = argparse.ArgumentParser(description="DVR Post-Recording Quality Report & Anomaly Investigator")
    parser.add_argument("--dry-run", action="store_true", help="Evaluate and print rendered report without sending email")
    parser.add_argument("--recording", type=str, default=None, help="Evaluate specific recording path or timer ID")
    parser.add_argument("--force", action="store_true", help="Force evaluation ignoring state history")
    parser.add_argument("--no-agy", action="store_true", help="Skip AGY diagnosis dispatch on anomalies")
    parser.add_argument("--all-history", action="store_true", help="Evaluate all historical timers regardless of lookback window")
    parser.add_argument("--recipient", type=str, default=MAILTO, help="Email recipient address")

    args = parser.parse_args()
    try:
        run_reporting_cycle(
            dry_run=args.dry_run,
            target_rec=args.recording,
            force=args.force,
            no_agy=args.no_agy,
            all_history=args.all_history,
            recipient=args.recipient,
        )
    except Exception as exc:
        logging.error("Fatal exception in main reporting loop: %s", exc, exc_info=True)
        sys.exit(0)


if __name__ == "__main__":
    main()
