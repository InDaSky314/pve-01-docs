#!/usr/bin/env python3
"""Tuner Availability Broker — dynamic single-tuner concurrency coordinator.

Gates MAINTENANCE ONLY. Authoritative recordings (Jellyfin DVR, MCT bookings)
never ask permission; they are authoritative reservations read by the broker.
If the broker is broken, absent, or unreachable, recordings proceed normally
and maintenance is denied (fail-closed for maintenance, zero-impact for recordings).

Non-negotiable design rules:
1. Broker gates MAINTENANCE ONLY. Never gates recordings.
2. Jellyfin reservations derived from timers and in-progress recordings. MCT
   reservations derived from /srv/media-core/sync/mct-windows.json.
3. Live viewing blocks maintenance: active Jellyfin sessions with Live TV,
   Threadfin active stream state.
4. Never use provider active_cons as a mutex (lags badly and causes 511s).
5. Leases must expire: TTL + heartbeat, stale leases reclaimable, persistent across
   restarts and scheduled nightly power-down.
6. Lookahead with safety margin: requested duration + margin must fit before next reservation.
7. Prefer the human: active lease holders yield if a household member starts watching Live TV.
"""
from __future__ import annotations

import argparse
import fcntl
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

LOCAL_TZ = ZoneInfo("Europe/Berlin")

# Paths inside CT 105
JF_URL = "http://127.0.0.1:8096"
JF_KEY_FILE = Path("/srv/media-core/.jellyfin_api_key")
THREADFIN_API_URL = "http://127.0.0.1:34400/api/"
MCT_WINDOWS_FILE = Path("/srv/media-core/sync/mct-windows.json")
MCT_WINDOWS_MAX_AGE = timedelta(minutes=10)
LEASE_FILE = Path("/srv/media-core/sync/tuner_lease.json")
LOCK_FILE = Path("/srv/media-core/sync/.tuner_broker.lock")

TIVIMATE_DIRS = [
    Path("/srv/media-core/media/recordings/Tivimate"),
    Path("/srv/media-core/media/recordings/Sports"),
]
TIVIMATE_STALE_AFTER = 60  # seconds

DEFAULT_LEASE_MINUTES = 5
DEFAULT_MARGIN_MINUTES = 2
DEFAULT_HEARTBEAT_TTL_SECONDS = 120
UA = "MediaCoreTunerBroker/1.0"


def _now_utc(at: Optional[datetime] = None) -> datetime:
    if at is not None:
        if at.tzinfo is None:
            return at.replace(tzinfo=timezone.utc)
        return at.astimezone(timezone.utc)
    return datetime.now(timezone.utc)


def _get_jf_token() -> Optional[str]:
    if not JF_KEY_FILE.exists():
        return None
    try:
        tok = JF_KEY_FILE.read_text().strip()
        return tok or None
    except Exception:
        return None


def _check_tivimate_recording(now_ts: float) -> bool:
    """True if a .ts file under TiviMate write directories was modified recently."""
    for d in TIVIMATE_DIRS:
        if not d.is_dir():
            continue
        try:
            for f in d.rglob("*.ts"):
                try:
                    if now_ts - f.stat().st_mtime < TIVIMATE_STALE_AFTER:
                        return True
                except OSError:
                    continue
        except OSError:
            continue
    return False


def _check_jellyfin_recordings(token: Optional[str]) -> Tuple[bool, List[str]]:
    """Checks for active/in-progress Jellyfin recordings. Fails closed."""
    if not token:
        return True, ["Jellyfin API key unavailable (assumed busy)"]
    try:
        req = urllib.request.Request(
            f"{JF_URL}/emby/LiveTv/Recordings?IsInProgress=true&Limit=10",
            headers={"Authorization": f'MediaBrowser Token="{token}"', "User-Agent": UA},
        )
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = json.load(resp)
        items = data.get("Items", [])
        if items:
            titles = [item.get("Name", "Recording") for item in items]
            return True, titles
        return False, []
    except Exception as exc:
        return True, [f"Could not check Jellyfin recordings ({exc}) — assumed busy"]


def _check_jellyfin_live_sessions(token: Optional[str]) -> Tuple[bool, List[str]]:
    """Checks for active Jellyfin sessions watching Live TV. Fails closed."""
    if not token:
        return True, ["Jellyfin API key unavailable (assumed busy)"]
    try:
        req = urllib.request.Request(
            f"{JF_URL}/Sessions",
            headers={"Authorization": f'MediaBrowser Token="{token}"', "User-Agent": UA},
        )
        with urllib.request.urlopen(req, timeout=8) as resp:
            sessions = json.load(resp)
        active_live = []
        for s in sessions:
            now_playing = s.get("NowPlayingItem")
            if not now_playing:
                continue
            item_type = now_playing.get("Type", "")
            is_live = (
                now_playing.get("IsLive", False)
                or item_type in ("TvChannel", "LiveTvProgram", "LiveTvChannel")
                or bool(now_playing.get("ChannelId"))
            )
            if is_live:
                client = s.get("Client", "client")
                dev = s.get("DeviceName", "device")
                user = s.get("UserName", "user")
                name = now_playing.get("Name") or "Live TV"
                active_live.append(f"{dev} ({client}/{user}) watching '{name}'")
        return bool(active_live), active_live
    except Exception as exc:
        return True, [f"Could not check Jellyfin sessions ({exc}) — assumed busy"]


def _check_threadfin_active() -> Tuple[bool, str]:
    """Checks Threadfin API responsiveness and active tuner stream status.

    Fails closed if Threadfin is down or unreachable.
    Inspects recent log status for active tuners ('Tuner: 1 / 1').
    Note: A raw TCP socket count on :34400 is not used alone because Jellyfin
    maintains an idle HTTP/1.1 keep-alive connection to Threadfin 24/7 as an
    HDHomeRun tuner host.
    """
    try:
        req = urllib.request.Request(
            THREADFIN_API_URL,
            data=json.dumps({"cmd": "status"}).encode("utf-8"),
            headers={"Content-Type": "application/json", "User-Agent": UA},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            res = json.load(resp)
            if not res.get("status"):
                return True, "Threadfin status reported unhealthy"
    except Exception as exc:
        return True, f"Threadfin is unreachable ({exc}) — fail closed"

    # Check Threadfin recent log entries for active tuner usage
    try:
        proc = subprocess.run(
            ["docker", "logs", "--tail", "50", "threadfin"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if proc.returncode == 0:
            for line in reversed(proc.stdout.splitlines()):
                if "Streaming Status" in line or "Tuner:" in line:
                    m = re.search(r"Tuner:\s*(\d+)\s*/\s*(\d+)", line)
                    if m:
                        active_tuners = int(m.group(1))
                        if active_tuners > 0:
                            return True, f"Threadfin stream active ({active_tuners} tuner(s) in use)"
                        else:
                            return False, "Threadfin idle (0 tuners in use)"
    except Exception:
        pass

    return False, "Threadfin idle"


def _get_jellyfin_timers(token: Optional[str], now_utc: datetime) -> List[Dict[str, Any]]:
    """Returns padded scheduled Jellyfin timers. Fails closed."""
    if not token:
        return [{
            "title": "Jellyfin API key unavailable (assumed busy)",
            "start": now_utc,
            "end": now_utc + timedelta(hours=1),
            "source": "jellyfin",
        }]
    try:
        req = urllib.request.Request(
            f"{JF_URL}/emby/LiveTv/Timers",
            headers={"Authorization": f'MediaBrowser Token="{token}"', "User-Agent": UA},
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            timers = json.load(resp).get("Items", [])
        out = []
        for t in timers:
            try:
                start = datetime.fromisoformat(t["StartDate"].replace("Z", "+00:00"))
                end = datetime.fromisoformat(t["EndDate"].replace("Z", "+00:00"))
                pre = timedelta(seconds=t.get("PrePaddingSeconds", 0) or 0)
                post = timedelta(seconds=t.get("PostPaddingSeconds", 0) or 0)
                padded_start = start - pre
                padded_end = end + post
                if padded_end > now_utc:
                    out.append({
                        "title": t.get("Name", "Jellyfin timer"),
                        "start": padded_start,
                        "end": padded_end,
                        "source": "jellyfin",
                    })
            except Exception:
                continue
        return out
    except Exception as exc:
        return [{
            "title": f"Jellyfin timer fetch failed ({exc}) — assumed busy",
            "start": now_utc,
            "end": now_utc + timedelta(hours=1),
            "source": "jellyfin",
        }]


def _get_mct_windows(now_utc: datetime) -> List[Dict[str, Any]]:
    """Reads MCT booked/running windows from sync file. Fails closed on corruption/staleness."""
    if not MCT_WINDOWS_FILE.exists():
        return []
    try:
        data = json.loads(MCT_WINDOWS_FILE.read_text(encoding="utf-8"))
        gen_str = data.get("generated_at")
        if gen_str:
            gen = datetime.fromisoformat(gen_str)
            if datetime.now(gen.tzinfo) - gen > MCT_WINDOWS_MAX_AGE:
                return [{
                    "title": "mct-windows.json is stale — assumed busy",
                    "start": now_utc,
                    "end": now_utc + timedelta(hours=1),
                    "source": "mct",
                }]
        out = []
        for w in data.get("windows", []):
            try:
                st = datetime.fromisoformat(w["start"])
                en = datetime.fromisoformat(w["end"])
                if en > now_utc:
                    out.append({
                        "title": w.get("title", "MCT capture"),
                        "start": st,
                        "end": en,
                        "source": "mct",
                    })
            except Exception:
                continue
        return out
    except Exception as exc:
        return [{
            "title": f"Could not read MCT windows ({exc}) — assumed busy",
            "start": now_utc,
            "end": now_utc + timedelta(hours=1),
            "source": "mct",
        }]


class _FileLock:
    """Reentrant process-level lock around lease modifications."""
    _lock = threading.RLock()
    _count = 0
    _fd: Optional[int] = None

    def __init__(self, path: Optional[Path] = None):
        self.path = path or LOCK_FILE

    def __enter__(self):
        _FileLock._lock.acquire()
        if _FileLock._count == 0:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            _FileLock._fd = os.open(str(self.path), os.O_CREAT | os.O_RDWR, 0o644)
            fcntl.flock(_FileLock._fd, fcntl.LOCK_EX)
        _FileLock._count += 1
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        _FileLock._count -= 1
        if _FileLock._count == 0:
            try:
                if _FileLock._fd is not None:
                    fcntl.flock(_FileLock._fd, fcntl.LOCK_UN)
                    os.close(_FileLock._fd)
            finally:
                _FileLock._fd = None
        _FileLock._lock.release()


def get_current_lease(now_utc: Optional[datetime] = None) -> Tuple[Optional[Dict[str, Any]], bool, bool, str]:
    """Reads current lease state.

    Returns: (lease_dict_or_none, is_active, is_stale, description)
    """
    now = _now_utc(now_utc)
    if not LEASE_FILE.exists():
        return None, False, False, "no lease file"
    try:
        data = json.loads(LEASE_FILE.read_text(encoding="utf-8"))
    except Exception as exc:
        return None, False, True, f"unparseable lease file ({exc})"

    if not isinstance(data, dict) or "lease_id" not in data:
        return None, False, True, "invalid lease structure"

    status = data.get("status", "active")
    if status != "active":
        return data, False, False, f"lease status is '{status}'"

    try:
        heartbeat_at = datetime.fromisoformat(data["heartbeat_at"])
        heartbeat_ttl = int(data.get("heartbeat_ttl", DEFAULT_HEARTBEAT_TTL_SECONDS))
        hard_expires_at = datetime.fromisoformat(data["hard_expires_at"])
        expires_at = datetime.fromisoformat(data["expires_at"])
    except Exception as exc:
        return data, False, True, f"invalid timestamps in lease ({exc})"

    if now >= hard_expires_at:
        return data, False, True, f"lease reached maximum duration (hard expiry at {hard_expires_at.isoformat()})"
    if (now - heartbeat_at).total_seconds() > heartbeat_ttl:
        return data, False, True, f"lease heartbeat timed out (last heartbeat at {heartbeat_at.isoformat()}, ttl={heartbeat_ttl}s)"
    if now >= expires_at:
        return data, False, True, f"lease expired at {expires_at.isoformat()}"

    return data, True, False, "lease active"


def reclaim_stale_lease(now_utc: Optional[datetime] = None) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
    """Reclaims any expired or stale lease file."""
    now = _now_utc(now_utc)
    with _FileLock():
        lease, is_active, is_stale, desc = get_current_lease(now)
        if not lease and not is_stale:
            return True, "no lease to reclaim", None
        if is_active:
            return False, f"lease {lease['lease_id'][:8]} is active and cannot be reclaimed", lease
        if is_stale or (lease and lease.get("status") != "active"):
            lease_id = lease.get("lease_id", "unknown") if lease else "corrupt"
            purpose = lease.get("purpose", "") if lease else ""
            try:
                if LEASE_FILE.exists():
                    LEASE_FILE.unlink()
            except OSError as exc:
                return False, f"failed to remove stale lease file: {exc}", lease
            msg = f"reclaimed stale lease {lease_id[:8]} (purpose='{purpose}', reason: {desc})"
            return True, msg, lease
    return True, "no stale lease", None


def check(
    minutes: int = DEFAULT_LEASE_MINUTES,
    purpose: str = "",
    margin_minutes: int = DEFAULT_MARGIN_MINUTES,
    at: Optional[datetime] = None,
) -> Tuple[bool, str, Dict[str, Any]]:
    """Checks whether the tuner is available for a maintenance job of `minutes` duration.

    Evaluates:
    1. Active lease status (and reclaims if stale)
    2. Active in-progress recordings (Jellyfin, TiviMate, MCT)
    3. Live viewing activity (Jellyfin active sessions, Threadfin streams)
    4. Scheduled reservations lookahead + margin (Jellyfin timers, MCT windows)

    Returns: (is_available, reason_str, details_dict)
    """
    now = _now_utc(at)
    details: Dict[str, Any] = {
        "checked_at": now.isoformat(),
        "requested_minutes": minutes,
        "margin_minutes": margin_minutes,
        "purpose": purpose,
    }

    try:
        # 1. Check existing lease
        lease, is_active, is_stale, lease_desc = get_current_lease(now)
        if is_stale:
            reclaimed, reclaim_msg, _ = reclaim_stale_lease(now)
            details["reclaim"] = reclaim_msg
            lease, is_active, is_stale, lease_desc = get_current_lease(now)

        if is_active and lease:
            details["active_lease"] = lease
            return False, f"tuner currently leased to '{lease.get('purpose')}' ({lease.get('lease_id')[:8]}) until {lease.get('expires_at')}", details

        # 2. Check active in-progress recordings
        jf_token = _get_jf_token()
        jf_rec_active, jf_rec_titles = _check_jellyfin_recordings(jf_token)
        if jf_rec_active:
            details["recording_active"] = {"engine": "jellyfin", "titles": jf_rec_titles}
            return False, f"tuner busy with Jellyfin recording: {', '.join(jf_rec_titles)}", details

        if _check_tivimate_recording(now.timestamp()):
            details["recording_active"] = {"engine": "tivimate"}
            return False, "tuner busy with in-progress TiviMate recording", details

        # 3. Check live viewing activity
        jf_live_active, jf_live_descs = _check_jellyfin_live_sessions(jf_token)
        if jf_live_active:
            details["live_viewing"] = {"engine": "jellyfin", "sessions": jf_live_descs}
            return False, f"live viewing active in Jellyfin ({'; '.join(jf_live_descs)})", details

        tf_active, tf_desc = _check_threadfin_active()
        if tf_active:
            details["live_viewing"] = {"engine": "threadfin", "description": tf_desc}
            return False, f"live viewing active: {tf_desc}", details

        # 4. Check scheduled reservations (Jellyfin timers + MCT windows)
        intervals = _get_jellyfin_timers(jf_token, now) + _get_mct_windows(now)
        details["reservations_found"] = len(intervals)

        # Check if current time is inside any reservation interval
        for res in intervals:
            st = res["start"]
            en = res["end"]
            if st <= now <= en:
                details["collision"] = {
                    "title": res["title"],
                    "source": res["source"],
                    "start": st.isoformat(),
                    "end": en.isoformat(),
                }
                return False, f"tuner reserved for '{res['title']}' ({res['source']}) [{st.isoformat()} - {en.isoformat()}]", details

        # Lookahead: check upcoming reservations starting after now
        upcoming = [r for r in intervals if r["start"] > now]
        upcoming.sort(key=lambda r: r["start"])
        details["upcoming_reservations"] = [
            {
                "title": r["title"],
                "source": r["source"],
                "start": r["start"].isoformat(),
                "end": r["end"].isoformat(),
            }
            for r in upcoming
        ]

        if upcoming:
            next_res = upcoming[0]
            gap_sec = (next_res["start"] - now).total_seconds()
            gap_min = gap_sec / 60.0
            details["next_reservation"] = {
                "title": next_res["title"],
                "source": next_res["source"],
                "start": next_res["start"].isoformat(),
                "minutes_until": round(gap_min, 1),
            }
            required_sec = (minutes + margin_minutes) * 60.0
            if gap_sec < required_sec:
                return (
                    False,
                    f"requested {minutes}m + {margin_minutes}m margin exceeds available gap ({gap_min:.1f}m until '{next_res['title']}' at {next_res['start'].isoformat()})",
                    details,
                )

        return True, f"tuner available for {minutes}m", details
    except Exception as exc:
        return False, f"broker check error ({exc}) — fail closed", details


def acquire(
    minutes: int = DEFAULT_LEASE_MINUTES,
    purpose: str = "",
    heartbeat_ttl: int = DEFAULT_HEARTBEAT_TTL_SECONDS,
    margin_minutes: int = DEFAULT_MARGIN_MINUTES,
    at: Optional[datetime] = None,
) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
    """Attempts to acquire a dynamic tuner lease.

    Returns: (acquired, message, lease_dict_or_none)
    """
    now = _now_utc(at)
    with _FileLock():
        avail, reason, details = check(minutes=minutes, purpose=purpose, margin_minutes=margin_minutes, at=now)
        if not avail:
            return False, reason, None

        lease_id = str(uuid.uuid4())
        hard_expires = now + timedelta(minutes=minutes)
        ttl_sec = min(minutes * 60, heartbeat_ttl)
        lease_data = {
            "lease_id": lease_id,
            "purpose": purpose or "maintenance",
            "owner_pid": os.getpid(),
            "acquired_at": now.isoformat(),
            "duration_minutes": minutes,
            "margin_minutes": margin_minutes,
            "heartbeat_ttl": heartbeat_ttl,
            "heartbeat_at": now.isoformat(),
            "expires_at": (now + timedelta(seconds=ttl_sec)).isoformat(),
            "hard_expires_at": hard_expires.isoformat(),
            "status": "active",
        }

        # Atomic write
        LEASE_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = LEASE_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(lease_data, indent=2) + "\n", encoding="utf-8")
        tmp.replace(LEASE_FILE)

        return True, f"acquired lease {lease_id[:8]} for {minutes}m", lease_data


def heartbeat(lease_id: str, at: Optional[datetime] = None) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
    """Renews a lease heartbeat and checks if the holder must yield to a human or recording.

    Returns: (ok, message, lease_dict)
    - ok is False with 'yield: ...' when human viewing or an authoritative recording starts.
    """
    now = _now_utc(at)
    with _FileLock():
        lease, is_active, is_stale, desc = get_current_lease(now)
        if not lease or lease.get("lease_id") != lease_id:
            return False, "lease not found or id mismatch", None
        if not is_active:
            return False, f"lease is inactive: {desc}", lease

        # Check hard expiry
        hard_expires_at = datetime.fromisoformat(lease["hard_expires_at"])
        if now >= hard_expires_at:
            lease["status"] = "expired"
            _save_lease(lease)
            return False, f"lease reached maximum duration ({lease['duration_minutes']}m)", lease

        # Prefer the human & protect recordings: check if live viewing or recording started
        jf_token = _get_jf_token()
        jf_live_active, jf_live_descs = _check_jellyfin_live_sessions(jf_token)
        if jf_live_active:
            lease["status"] = "yielded"
            lease["yield_reason"] = f"household live viewing started: {'; '.join(jf_live_descs)}"
            _save_lease(lease)
            return False, f"yield: {lease['yield_reason']}", lease

        tf_active, tf_desc = _check_threadfin_active()
        if tf_active:
            lease["status"] = "yielded"
            lease["yield_reason"] = f"threadfin stream started: {tf_desc}"
            _save_lease(lease)
            return False, f"yield: {lease['yield_reason']}", lease

        jf_rec_active, jf_rec_titles = _check_jellyfin_recordings(jf_token)
        if jf_rec_active:
            lease["status"] = "yielded"
            lease["yield_reason"] = f"recording started: {', '.join(jf_rec_titles)}"
            _save_lease(lease)
            return False, f"yield: {lease['yield_reason']}", lease

        if _check_tivimate_recording(now.timestamp()):
            lease["status"] = "yielded"
            lease["yield_reason"] = "tivimate recording started"
            _save_lease(lease)
            return False, f"yield: {lease['yield_reason']}", lease

        # Check upcoming reservation starting in < 60s
        intervals = _get_jellyfin_timers(jf_token, now) + _get_mct_windows(now)
        for res in intervals:
            if res["start"] <= (now + timedelta(seconds=60)) and now <= res["end"]:
                lease["status"] = "yielded"
                lease["yield_reason"] = f"imminent reservation '{res['title']}' starting at {res['start'].isoformat()}"
                _save_lease(lease)
                return False, f"yield: {lease['yield_reason']}", lease

        # All clear -> renew heartbeat
        heartbeat_ttl = int(lease.get("heartbeat_ttl", DEFAULT_HEARTBEAT_TTL_SECONDS))
        lease["heartbeat_at"] = now.isoformat()
        lease["expires_at"] = min(now + timedelta(seconds=heartbeat_ttl), hard_expires_at).isoformat()
        _save_lease(lease)
        return True, f"heartbeat renewed until {lease['expires_at']}", lease


def release(lease_id: str, at: Optional[datetime] = None) -> Tuple[bool, str]:
    """Releases an active lease."""
    now = _now_utc(at)
    with _FileLock():
        lease, is_active, _, _ = get_current_lease(now)
        if not lease or lease.get("lease_id") != lease_id:
            return True, "no active lease matching id"
        try:
            if LEASE_FILE.exists():
                LEASE_FILE.unlink()
        except OSError as exc:
            return False, f"failed to remove lease file: {exc}"
        return True, f"released lease {lease_id[:8]}"


def _save_lease(lease: Dict[str, Any]) -> None:
    LEASE_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = LEASE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(lease, indent=2) + "\n", encoding="utf-8")
    tmp.replace(LEASE_FILE)


def status(at: Optional[datetime] = None) -> Dict[str, Any]:
    """Full diagnostic status of tuner availability, reservations, and leases."""
    now = _now_utc(at)
    avail, reason, details = check(minutes=DEFAULT_LEASE_MINUTES, purpose="status_probe", at=now)
    lease, is_active, is_stale, lease_desc = get_current_lease(now)

    out: Dict[str, Any] = {
        "timestamp_utc": now.isoformat(),
        "timestamp_local": now.astimezone(LOCAL_TZ).isoformat(),
        "tuner_available": avail,
        "availability_reason": reason,
        "active_lease": lease if is_active else None,
        "stale_lease": lease if is_stale else None,
        "lease_status": lease_desc,
        "details": details,
    }
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description="Tuner Availability Broker")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # check
    p_check = subparsers.add_parser("check", help="Check if tuner is available for N minutes")
    p_check.add_argument("--minutes", "--duration", dest="minutes", type=int, default=DEFAULT_LEASE_MINUTES)
    p_check.add_argument("--margin", type=int, default=DEFAULT_MARGIN_MINUTES)
    p_check.add_argument("--purpose", type=str, default="check")

    # acquire
    p_acq = subparsers.add_parser("acquire", help="Acquire tuner lease for N minutes")
    p_acq.add_argument("--minutes", "--duration", dest="minutes", type=int, default=DEFAULT_LEASE_MINUTES)
    p_acq.add_argument("--margin", type=int, default=DEFAULT_MARGIN_MINUTES)
    p_acq.add_argument("--ttl", type=int, default=DEFAULT_HEARTBEAT_TTL_SECONDS)
    p_acq.add_argument("--purpose", type=str, default="maintenance")

    # heartbeat
    p_hb = subparsers.add_parser("heartbeat", help="Send heartbeat for active lease")
    p_hb.add_argument("lease_id", type=str)

    # release
    p_rel = subparsers.add_parser("release", help="Release an acquired lease")
    p_rel.add_argument("lease_id", type=str)

    # reclaim
    subparsers.add_parser("reclaim", help="Reclaim stale or expired lease")

    # status
    p_stat = subparsers.add_parser("status", help="Get tuner and lease status")
    p_stat.add_argument("--json", action="store_true", help="Output JSON format")

    args = parser.parse_args()

    if args.command == "check":
        ok, reason, details = check(minutes=args.minutes, purpose=args.purpose, margin_minutes=args.margin)
        if ok:
            print(f"AVAILABLE: {reason}")
            return 0
        else:
            print(f"UNAVAILABLE: {reason}")
            return 1

    elif args.command == "acquire":
        ok, reason, lease = acquire(minutes=args.minutes, purpose=args.purpose, heartbeat_ttl=args.ttl, margin_minutes=args.margin)
        if ok and lease:
            print(f"ACQUIRED: lease_id={lease['lease_id']} expires={lease['expires_at']} purpose='{lease['purpose']}'")
            return 0
        else:
            print(f"DENIED: {reason}")
            return 1

    elif args.command == "heartbeat":
        ok, msg, lease = heartbeat(args.lease_id)
        if ok:
            print(f"HEARTBEAT_OK: {msg}")
            return 0
        elif msg.startswith("yield:"):
            print(f"YIELD: {msg}")
            return 2
        else:
            print(f"ERROR: {msg}")
            return 1

    elif args.command == "release":
        ok, msg = release(args.lease_id)
        if ok:
            print(f"RELEASED: {msg}")
            return 0
        else:
            print(f"ERROR: {msg}")
            return 1

    elif args.command == "reclaim":
        ok, msg, lease = reclaim_stale_lease()
        print(f"RECLAIM: {msg}")
        return 0 if ok else 1

    elif args.command == "status":
        st = status()
        if args.json:
            print(json.dumps(st, default=str, indent=2))
        else:
            avail_str = "AVAILABLE" if st["tuner_available"] else "UNAVAILABLE"
            print(f"Tuner Status: {avail_str} ({st['availability_reason']})")
            if st.get("active_lease"):
                l = st["active_lease"]
                print(f"Active Lease: {l['lease_id'][:8]} purpose='{l['purpose']}' expires={l['expires_at']}")
            elif st.get("stale_lease"):
                l = st["stale_lease"]
                print(f"Stale Lease: {l['lease_id'][:8]} purpose='{l['purpose']}' (reclaimable)")
            else:
                print("Active Lease: None")
            nxt = st.get("details", {}).get("next_reservation")
            if nxt:
                print(f"Next Reservation: '{nxt['title']}' ({nxt['source']}) in {nxt['minutes_until']}m at {nxt['start']}")
            else:
                print("Next Reservation: None scheduled")
        return 0

    return 0


if __name__ == "__main__":
    sys.exit(main())
