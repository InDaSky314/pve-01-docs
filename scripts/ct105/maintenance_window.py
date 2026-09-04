#!/usr/bin/env python3
"""Dynamic tuner availability check (replaces fixed overnight maintenance window).

Migrated 2026-09-04: replaces the fixed 01:00-05:00 clock constraint with the
Tuner Availability Broker (tuner_broker.py). Preserves full backward
compatibility for existing callers:
  - epg-repair-loop
  - icon-repair-loop
  - CLAUDE.md / standing automation

Interface contract preserved:
  is_open(at=None) -> Tuple[bool, str]
    - True, "ok": tuner is dynamically available for maintenance work
    - False, "<reason>": tuner is in use, reserved, or unavailable
  CLI: exits 0 ("OPEN: ok") or 1 ("CLOSED: <reason>")
"""
import sys
from pathlib import Path
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from typing import Tuple, List

sys.path.insert(0, "/srv/media-core/sync")
try:
    import tuner_broker
    _import_err = None
except Exception as exc:
    tuner_broker = None
    _import_err = exc

TZ = ZoneInfo("Europe/Berlin")
MAINT_START_HOUR = 1
MAINT_END_HOUR = 5


def _scheduled_padded_intervals(at=None) -> List[Tuple[datetime, datetime]]:
    """Legacy helper preserved for backward compatibility."""
    if tuner_broker is None:
        now = at or datetime.now(timezone.utc)
        return [(now, now + timedelta(hours=1))]
    now_utc = at or datetime.now(timezone.utc)
    token = tuner_broker._get_jf_token()
    intervals = tuner_broker._get_jellyfin_timers(token, now_utc) + tuner_broker._get_mct_windows(now_utc)
    return [(r["start"], r["end"]) for r in intervals]


def is_open(at=None, minutes: int = 5) -> Tuple[bool, str]:
    """True if the tuner is available right now (or at aware datetime `at`)
    for a maintenance job of `minutes` duration.

    Dynamically verifies:
    1. No active maintenance lease held by another task.
    2. No Jellyfin or TiviMate recording in progress.
    3. No live viewing in progress (Jellyfin sessions, Threadfin stream connections).
    4. Free gap covers requested duration + safety margin before any scheduled
       Jellyfin timer or MCT capture window.
    5. Fails closed (False) if broker or media services are unreachable.
    """
    if tuner_broker is None:
        return False, f"tuner broker unavailable ({_import_err})"
    try:
        ok, reason, details = tuner_broker.check(
            minutes=minutes,
            purpose="maintenance_window.is_open",
            at=at,
        )
        if ok:
            return True, "ok"
        return False, reason
    except Exception as exc:
        return False, f"tuner broker error: {exc}"


if __name__ == "__main__":
    ok, reason = is_open()
    print(("OPEN" if ok else "CLOSED") + f": {reason}")
    sys.exit(0 if ok else 1)
