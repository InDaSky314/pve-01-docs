#!/usr/bin/env python3
"""DVR Morning Pre-Flight Recording Digest.

Daily morning audit (08:00 Europe/Berlin) covering the next 48 hours:
1. BLUF verdict first: ALL CLEAR / ATTENTION NEEDED.
2. Every scheduled Jellyfin timer: title, subtitle, channel ("<num> <name>"),
   scheduled and padded capture windows.
3. Power-window check: validates against physical mains timer (05:05 on, 22:25 cut;
   01:00 Fri/Sat) and flags recordings needing power (needsPower).
4. Current shutdown-override state: reads /var/lib/dvr-dashboard/override-until
   to confirm if host keep-awake is active.
5. Tuner contention check: strictly 1 concurrent stream on the IPTV account;
   flags any overlapping padded recording windows as genuine conflicts.
6. Followed-team fixture watch: checks /api/schedule for upcoming games without
   scheduled recording timers.
7. Fail-soft email delivery: plain-text + styled HTML via host sendmail relay
   to nathan.karras@gmail.com (port 465). Never raises unhandled exceptions.
8. AGY diagnosis: only dispatched in diagnose mode when real conflicts or
   untracked gaps exist.
"""
from __future__ import annotations

import argparse
import base64
import html as html_lib
import json
import logging
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone, time as dtime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

# ── Configuration & Paths ───────────────────────────────────────────────────

LOCAL_TZ = ZoneInfo("Europe/Berlin")
MAILTO = "nathan.karras@gmail.com"
FROM_ADDR = "kopr.notify@gmail.com"
FROM_NAME = "Media-Core Pre-Flight Digest"

AUTH_FILE = Path("/etc/dvr-dashboard.auth")
OVERRIDE_FILE = Path("/var/lib/dvr-dashboard/override-until")
DASHBOARD_URL = "http://127.0.0.1:8099"

JELLYFIN_KEY_FILES = [
    Path("/srv/media-core/.jellyfin_api_key"),
    Path("/config/.jellyfin_api_key"),
    Path("/var/lib/lxc/105/rootfs/srv/media-core/.jellyfin_api_key"),
]
JF_URLS = ["http://192.168.9.50:8096", "http://127.0.0.1:8096"]
JELLYFIN_TIMERS_PATH = "/srv/media-core/jellyfin/config/data/livetv/timers.json"

STATE_DIR = Path("/var/lib/dvr-preflight-digest")
PROMPTS_DIR = STATE_DIR / "prompts"

AGY_TASK = "/root/bin/agy-task.sh"
AGY_TIMEOUT_MIN = 10
DEFAULT_WINDOW_HOURS = 48

# Physical mains timer schedule (Europe/Berlin)
POWER_ON = dtime(5, 5)
POWER_OFF_NORMAL = dtime(22, 25)
POWER_OFF_LATE = dtime(1, 0)
LATE_NIGHT_WEEKDAYS = (4, 5)  # Fri=4, Sat=5

JF_DONE = {"Completed", "Cancelled", "CancelledPostProcessing", "Failed"}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)


# ── Physical Power Window Calculations ──────────────────────────────────────

def is_powered(moment: datetime) -> bool:
    """Is the physical mains timer supplying power at this instant?"""
    moment_local = moment.astimezone(LOCAL_TZ)
    t = moment_local.time()
    if t >= POWER_ON:
        if moment_local.weekday() in LATE_NIGHT_WEEKDAYS:
            return True
        return t <= POWER_OFF_NORMAL
    if (moment_local - timedelta(days=1)).weekday() in LATE_NIGHT_WEEKDAYS:
        return t <= POWER_OFF_LATE
    return False


def outside_window(start: datetime, end: datetime) -> bool:
    """Check if any 15-minute slice of the recording falls outside mains power."""
    cur = start
    while cur <= end:
        if not is_powered(cur):
            return True
        cur += timedelta(minutes=15)
    return not is_powered(end)


def get_override_state(now: datetime) -> dict[str, Any]:
    """Read /var/lib/dvr-dashboard/override-until and evaluate active state."""
    state = {
        "active": False,
        "expiry": None,
        "expiry_str": "None",
        "description": "Standard power schedule in effect (On 05:05, Cut 22:25 / Fri-Sat 01:00 CEST).",
    }
    if not OVERRIDE_FILE.exists():
        return state

    try:
        raw = OVERRIDE_FILE.read_text(encoding="utf-8").strip()
        if not raw:
            return state
        expiry = datetime.fromisoformat(raw)
        if expiry.tzinfo is None:
            expiry = expiry.replace(tzinfo=LOCAL_TZ)
        else:
            expiry = expiry.astimezone(LOCAL_TZ)

        state["expiry"] = expiry
        state["expiry_str"] = expiry.strftime("%a %d %b %H:%M %Z")

        if expiry > now:
            state["active"] = True
            remaining = expiry - now
            rem_hours = remaining.total_seconds() / 3600.0
            state["description"] = (
                f"Active keep-awake hold until {state['expiry_str']} (~{rem_hours:.1f}h remaining). "
                f"Nightly clean shutdown is suppressed."
            )
        else:
            state["description"] = f"Previous override expired at {state['expiry_str']}."
    except Exception as exc:
        logging.warning("Error reading override-until file: %s", exc)

    return state


# ── Jellyfin & Channel Queries ─────────────────────────────────────────────

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
        "User-Agent": "DVRPreflightDigest/1.0",
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
    """Fetch channel map from Jellyfin API and return '<Number> <Name>'."""
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
    """Load timers from CT 105 timers.json."""
    try:
        res = subprocess.run(
            ["/usr/sbin/pct", "exec", "105", "--", "cat", JELLYFIN_TIMERS_PATH],
            capture_output=True, text=True, timeout=15, check=True,
        )
        return json.loads(res.stdout)
    except Exception as exc:
        logging.warning("Could not read timers.json via pct exec: %s", exc)
        return []


# ── Dashboard /api/schedule Query ──────────────────────────────────────────

def get_auth_header() -> dict[str, str]:
    """Read /etc/dvr-dashboard.auth and format Basic auth header."""
    if not AUTH_FILE.exists():
        return {}
    try:
        content = AUTH_FILE.read_text().strip()
        if ":" in content:
            user, pwd = content.split(":", 1)
            token = base64.b64encode(f"{user.strip()}:{pwd.strip()}".encode()).decode()
            return {"Authorization": f"Basic {token}"}
    except Exception as exc:
        logging.warning("Error reading auth file %s: %s", AUTH_FILE, exc)
    return {}


def fetch_dashboard_schedule() -> dict[str, Any] | None:
    """Fetch full season schedule and matched recordings from /api/schedule."""
    headers = get_auth_header()
    headers["User-Agent"] = "DVRPreflightDigest/1.0"
    try:
        req = urllib.request.Request(f"{DASHBOARD_URL}/api/schedule", headers=headers)
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = resp.read()
            return json.loads(data) if data else None
    except Exception as exc:
        logging.warning("Could not fetch /api/schedule: %s", exc)
        return None


# ── Evaluation Engine ──────────────────────────────────────────────────────

def evaluate_scheduled_recordings(
    now: datetime,
    window_end: datetime,
    chmap: dict[str, str],
    override_state: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Parse scheduled timers in window and detect 1-stream tuner conflicts."""
    raw_timers = load_jellyfin_timers()
    timers: list[dict[str, Any]] = []

    for t in raw_timers:
        status = t.get("Status", "")
        if status in JF_DONE:
            continue

        start_str = t.get("StartDate", "")
        end_str = t.get("EndDate", "")
        if not start_str or not end_str:
            continue

        try:
            start_dt = datetime.fromisoformat(start_str.replace("Z", "+00:00")).astimezone(LOCAL_TZ)
            end_dt = datetime.fromisoformat(end_str.replace("Z", "+00:00")).astimezone(LOCAL_TZ)
        except Exception:
            continue

        pre_pad = int(t.get("PrePaddingSeconds") or 0)
        post_pad = int(t.get("PostPaddingSeconds") or 0)
        padded_start = start_dt - timedelta(seconds=pre_pad)
        padded_end = end_dt + timedelta(seconds=post_pad)

        # Include if padded capture window intersects [now, window_end]
        if padded_end < now or padded_start > window_end:
            continue

        raw_cid = str(t.get("ChannelId") or "")
        num = "".join(ch for ch in raw_cid if ch.isdigit())
        c_name = t.get("ChannelName") or ""
        channel_disp = (
            chmap.get(raw_cid)
            or chmap.get(num)
            or chmap.get(c_name)
            or (f"{num} {c_name}".strip() if num else c_name)
            or "Unknown Channel"
        )

        needs_pwr = outside_window(start_dt, end_dt)

        # Check if active override safely covers this recording's completion
        override_covers = False
        if needs_pwr and override_state["active"] and override_state["expiry"]:
            if override_state["expiry"] >= padded_end:
                override_covers = True

        timers.append({
            "id": t.get("Id", ""),
            "name": t.get("Name", "Recording"),
            "subtitle": t.get("EpisodeTitle") or t.get("ProgramId") or "",
            "channel": channel_disp,
            "start": start_dt,
            "end": end_dt,
            "pre_pad_sec": pre_pad,
            "post_pad_sec": post_pad,
            "padded_start": padded_start,
            "padded_end": padded_end,
            "status": status,
            "needsPower": needs_pwr,
            "overrideCovers": override_covers,
        })

    timers.sort(key=lambda x: x["padded_start"])

    # Single-tuner conflict audit (IPTV account has strictly ONE stream)
    conflicts: list[dict[str, Any]] = []
    for i in range(len(timers)):
        for j in range(i + 1, len(timers)):
            t1 = timers[i]
            t2 = timers[j]
            # Check if padded windows overlap
            if t1["padded_start"] < t2["padded_end"] and t2["padded_start"] < t1["padded_end"]:
                overlap_start = max(t1["padded_start"], t2["padded_start"])
                overlap_end = min(t1["padded_end"], t2["padded_end"])
                overlap_dur = int((overlap_end - overlap_start).total_seconds())
                conflicts.append({
                    "timer1": t1,
                    "timer2": t2,
                    "overlap_start": overlap_start,
                    "overlap_end": overlap_end,
                    "overlap_seconds": overlap_dur,
                    "overlap_str": f"{overlap_dur // 60}m {overlap_dur % 60}s",
                })

    return timers, conflicts


SPORTS_CONFIG_PATH = "/var/lib/dvr-dashboard/sports-config.json"


def _auto_record_enabled(team: str) -> bool:
    """True unless the dashboard has auto-record explicitly toggled off for this team.

    Fail open: if the file is missing or unreadable, treat the team as tracked so a
    real gap is still reported. Better a spurious flag than a silent miss.
    """
    try:
        with open(SPORTS_CONFIG_PATH, "r", encoding="utf-8") as fh:
            return bool(json.load(fh).get(team, True))
    except Exception:                                       # noqa: BLE001
        return True


def evaluate_followed_games(
    now: datetime,
    window_end: datetime,
    scheduled_timers: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Audit upcoming followed-team games in next 48h and find untracked matches.

    A team with auto-record switched OFF in the dashboard is a deliberate owner
    choice, not a gap: "Brewers" is false today, and with ~195 Brewers games a
    season an un-timered one would otherwise raise ATTENTION NEEDED almost every
    morning. That is how a daily digest gets ignored, and then the genuine gap --
    the 2026-09-05 Bayern match, which had no timer at all -- goes unread with it.
    Auto-off games are still listed, just as expected rather than as a problem.
    """
    sched_data = fetch_dashboard_schedule()
    games_in_window: list[dict[str, Any]] = []
    untracked_games: list[dict[str, Any]] = []

    if not sched_data or not isinstance(sched_data.get("games"), list):
        return games_in_window, untracked_games

    for g in sched_data["games"]:
        if g.get("done"):
            continue
        start_str = g.get("start")
        end_str = g.get("end")
        if not start_str or not end_str:
            continue

        try:
            start_dt = datetime.fromisoformat(start_str).astimezone(LOCAL_TZ)
            end_dt = datetime.fromisoformat(end_str).astimezone(LOCAL_TZ)
        except Exception:
            continue

        if start_dt < now - timedelta(hours=1) or start_dt > window_end:
            continue

        # Check for matching timer
        matched_rec = g.get("recording")
        has_timer = False

        if matched_rec:
            has_timer = True
        else:
            # Cross-verify against scheduled timers by time overlap and channel/team
            for t in scheduled_timers:
                if t["padded_start"] <= end_dt and t["padded_end"] >= start_dt:
                    has_timer = True
                    matched_rec = t["name"]
                    break

        item = {
            "team": g.get("team", "Unknown Team"),
            "name": g.get("name", "Game"),
            "start": start_dt,
            "end": end_dt,
            "channel": g.get("channel") or "not in guide yet",
            "broadcasts": g.get("broadcasts") or [],
            "needsPower": g.get("needsPower", False),
            "recording": matched_rec,
            "has_timer": has_timer,
        }
        item["auto_record"] = _auto_record_enabled(item["team"])
        # Inside a 48h window, "not in guide yet" means the fixture is simply not
        # carried on this lineup -- there is nothing to schedule, so it is not an
        # action item. (Verified 2026-09-02: the VfL Osnabruck v Bayern cup tie was
        # not broadcast on any subscribed channel; only a Sportschau highlights
        # slot existed.) Beyond 48h a missing channel would just mean the guide has
        # not reached that far, which is why this only applies inside the window.
        item["recordable"] = bool(item["channel"]) and item["channel"] != "not in guide yet"
        games_in_window.append(item)
        if not has_timer and item["auto_record"] and item["recordable"]:
            untracked_games.append(item)

    games_in_window.sort(key=lambda x: x["start"])
    untracked_games.sort(key=lambda x: x["start"])
    return games_in_window, untracked_games


def build_preflight_report(hours: int = DEFAULT_WINDOW_HOURS) -> dict[str, Any]:
    """Execute complete 48h pre-flight audit and compute verdict."""
    now = datetime.now(LOCAL_TZ)
    window_end = now + timedelta(hours=hours)

    chmap = get_channel_names()
    override_state = get_override_state(now)
    timers, conflicts = evaluate_scheduled_recordings(now, window_end, chmap, override_state)
    games, untracked_games = evaluate_followed_games(now, window_end, timers)

    anomalies: list[str] = []
    action_items: list[str] = []

    # 1. Check Tuner Conflicts
    if conflicts:
        for c in conflicts:
            msg = (
                f"TUNER CONTENTION: Overlap between '{c['timer1']['name']}' ({c['timer1']['channel']}) "
                f"and '{c['timer2']['name']}' ({c['timer2']['channel']}) for {c['overlap_str']}. "
                f"IPTV account supports only 1 stream; one recording WILL fail."
            )
            anomalies.append(msg)
            action_items.append(f"Cancel or reschedule one overlapping timer ({c['timer1']['name']} vs {c['timer2']['name']}).")

    # 2. Check Untracked Followed-Team Games
    if untracked_games:
        for ug in untracked_games:
            msg = (
                f"UNTRACKED FIXTURE: {ug['team']} game '{ug['name']}' at "
                f"{ug['start'].strftime('%a %d %b %H:%M')} has NO recording timer scheduled."
            )
            anomalies.append(msg)
            action_items.append(f"Schedule a recording in Jellyfin for {ug['team']} ('{ug['name']}').")

    # 3. Check Power Window Coverage
    uncovered_power_timers: list[dict[str, Any]] = []
    covered_power_timers: list[dict[str, Any]] = []
    for t in timers:
        if t["needsPower"]:
            if t["overrideCovers"]:
                covered_power_timers.append(t)
            else:
                uncovered_power_timers.append(t)
                msg = (
                    f"POWER WINDOW RISK: Recording '{t['name']}' ({t['channel']}) "
                    f"runs during mains power cut ({t['padded_start'].strftime('%H:%M')}–{t['padded_end'].strftime('%H:%M')}) "
                    f"with NO active shutdown override."
                )
                anomalies.append(msg)
                action_items.append(f"Extend keep-awake override via dvr-dashboard or leave mains switch ON.")

    # Determine Verdict
    if anomalies:
        verdict = "ATTENTION NEEDED"
    else:
        verdict = "ALL CLEAR"

    return {
        "now": now,
        "window_end": window_end,
        "hours": hours,
        "verdict": verdict,
        "override_state": override_state,
        "timers": timers,
        "conflicts": conflicts,
        "games": games,
        "untracked_games": untracked_games,
        "uncovered_power_timers": uncovered_power_timers,
        "covered_power_timers": covered_power_timers,
        "anomalies": anomalies,
        "action_items": action_items,
    }


# ── AGY Investigation Dispatch ─────────────────────────────────────────────

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


def dispatch_agy_investigation(report: dict[str, Any]) -> dict[str, str] | None:
    """Dispatch AGY in diagnose-only mode when real conflicts or untracked games occur."""
    slug = f"dvr-preflight-diag-{int(time.time())}"
    PROMPTS_DIR.mkdir(parents=True, exist_ok=True)
    prompt_file = PROMPTS_DIR / f"{slug}.prompt.md"

    anomalies_formatted = "\n".join(f"- {a}" for a in report["anomalies"])
    timers_formatted = "\n".join(
        f"- {t['name']} on {t['channel']}: {t['padded_start'].strftime('%Y-%m-%d %H:%M')} to {t['padded_end'].strftime('%H:%M %Z')} (needsPower: {t['needsPower']})"
        for t in report["timers"]
    )

    prompt_content = f"""A DVR pre-flight recording schedule check flagged anomalies on pve-01 (CT 105 media-core).

Time: {report['now'].strftime('%Y-%m-%d %H:%M %Z')}
Audit Window: Next {report['hours']} hours (until {report['window_end'].strftime('%Y-%m-%d %H:%M %Z')})
Shutdown Override Status: {report['override_state']['description']}

Detected Schedule Anomalies:
{anomalies_formatted}

Scheduled Timers in Window:
{timers_formatted or "None"}

Investigate the root cause of these conflicts/gaps on pve-01 / CT 105.
Do NOT modify any files, delete timers, restart services, or apply changes. This is a DIAGNOSIS ONLY.

Correlate at minimum:
1. Threadfin tuner allocation & IPTV account single-stream limit.
2. Jellyfin timer schedule in /srv/media-core/jellyfin/config/data/livetv/timers.json.
3. Sports-dvr-auto automation state and team schedule feeds (/api/schedule, ESPN, OpenLigaDB).
4. Mains power schedule & shutdown override (/var/lib/dvr-dashboard/override-until).

REQUIRED FORMAT:
Begin your report with a section headed exactly:
## Plain-English Summary
Containing 3-5 concise sentences explaining the conflict or gap in plain homeowner language, why it occurred, and recommended schedule adjustments.

Follow with:
## Technical Details
Containing your full technical investigation and log citations.
"""
    prompt_file.write_text(prompt_content, encoding="utf-8")
    logging.info("Dispatching AGY pre-flight diagnosis (slug: %s)...", slug)

    try:
        proc = subprocess.run(
            [AGY_TASK, "run", slug, "diagnose", f"@{prompt_file}", "--timeout", f"{AGY_TIMEOUT_MIN}m"],
            capture_output=True, text=True, timeout=(AGY_TIMEOUT_MIN + 2) * 60,
        )
    except Exception as exc:
        logging.warning("Failed to dispatch AGY pre-flight diagnosis: %s", exc)
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


# ── HTML & Text Email Rendering ───────────────────────────────────────────

def fmt_time_span(start: datetime, end: datetime) -> str:
    if start.date() == end.date():
        return f"{start.strftime('%a %d %b, %H:%M')} – {end.strftime('%H:%M %Z')}"
    return f"{start.strftime('%a %d %b, %H:%M')} – {end.strftime('%a %d %b, %H:%M %Z')}"


def render_email(report: dict[str, Any], agy_diag: dict[str, str] | None = None) -> tuple[str, str, str]:
    """Render plain text and HTML versions of the pre-flight digest."""
    verdict = report["verdict"]
    is_clear = (verdict == "ALL CLEAR")

    badge_bg = "#1e824c" if is_clear else "#d97706"
    badge_light = "#eafaf1" if is_clear else "#fef3c7"
    badge_label = "✅ ALL CLEAR — RECORDINGS READY" if is_clear else "⚠️ ATTENTION NEEDED — SCHEDULE REVIEW"

    now_str = report["now"].strftime("%a %d %b, %H:%M %Z")
    win_str = report["window_end"].strftime("%a %d %b, %H:%M %Z")

    num_timers = len(report["timers"])
    num_games = len(report["games"])
    num_untracked = len(report["untracked_games"])
    num_conflicts = len(report["conflicts"])

    # Build concise subject line
    if is_clear:
        if report["override_state"]["active"]:
            ov_exp = report["override_state"]["expiry"].strftime("%a %d %b %H:%M")
            subject = f"[DVR PRE-FLIGHT] ALL CLEAR: {num_timers} recording(s) scheduled (Override active until {ov_exp})"
        else:
            subject = f"[DVR PRE-FLIGHT] ALL CLEAR: {num_timers} recording(s) scheduled (Next 48h)"
    else:
        reasons_brief = []
        if num_conflicts:
            reasons_brief.append(f"{num_conflicts} tuner conflict(s)")
        if num_untracked:
            reasons_brief.append(f"{num_untracked} untracked game(s)")
        if report["uncovered_power_timers"]:
            reasons_brief.append(f"{len(report['uncovered_power_timers'])} recording(s) outside power window")
        subject = f"[DVR PRE-FLIGHT] ATTENTION NEEDED: {', '.join(reasons_brief)}"

    # BLUF Summary Text
    if is_clear:
        bluf_summary = (
            f"All <strong>{num_timers}</strong> scheduled recording(s) over the next 48 hours are verified. "
            f"No tuner conflicts detected on the single-stream account, and all tracked team fixtures are covered."
        )
        if report["override_state"]["active"]:
            bluf_action = (
                f"ℹ️ <strong>Host Override:</strong> Active keep-awake hold in place until "
                f"<strong>{report['override_state']['expiry_str']}</strong> (mains timer power cuts will not shut down system)."
            )
        else:
            bluf_action = "No action required. Media stack is healthy and ready for scheduled captures."
    else:
        anomalies_html = "<br>• ".join(html_lib.escape(a) for a in report["anomalies"])
        bluf_summary = (
            f"The morning pre-flight audit detected <strong>{len(report['anomalies'])} item(s)</strong> requiring attention:<br>"
            f"• {anomalies_html}"
        )
        actions_html = "<br>👉 ".join(html_lib.escape(act) for act in report["action_items"])
        bluf_action = f"<strong>Recommended Action:</strong><br>👉 {actions_html}"

    # Timers Table Rows
    timer_rows_html = ""
    if report["timers"]:
        for t in report["timers"]:
            pwr_badge = ""
            if t["needsPower"]:
                if t["overrideCovers"]:
                    pwr_badge = '<span style="display:inline-block;padding:2px 6px;font-size:11px;background:#dbeafe;color:#1e40af;border-radius:3px;font-weight:600;">Power Off Window (Protected by Override)</span>'
                else:
                    pwr_badge = '<span style="display:inline-block;padding:2px 6px;font-size:11px;background:#fee2e2;color:#991b1b;border-radius:3px;font-weight:600;">⚠️ Needs Power (No Override)</span>'
            else:
                pwr_badge = '<span style="display:inline-block;padding:2px 6px;font-size:11px;background:#dcfce7;color:#166534;border-radius:3px;font-weight:600;">Inside Power Window</span>'

            sub_html = f'<div style="font-size:12px;color:#64748b;margin-top:2px;">{html_lib.escape(t["subtitle"])}</div>' if t["subtitle"] else ""
            timer_rows_html += f"""
            <tr style="border-bottom:1px solid #e2e8f0;">
              <td style="padding:10px 8px;font-weight:600;color:#0f172a;vertical-align:top;">
                {html_lib.escape(t['name'])}
                {sub_html}
              </td>
              <td style="padding:10px 8px;color:#1e293b;font-weight:500;vertical-align:top;">{html_lib.escape(t['channel'])}</td>
              <td style="padding:10px 8px;color:#334155;vertical-align:top;font-size:12.5px;">
                <div><strong>{fmt_time_span(t['start'], t['end'])}</strong></div>
                <div style="font-size:11.5px;color:#64748b;margin-top:2px;">Padded: {t['padded_start'].strftime('%H:%M')} – {t['padded_end'].strftime('%H:%M %Z')} (+{t['post_pad_sec']//60}m)</div>
              </td>
              <td style="padding:10px 8px;vertical-align:top;">{pwr_badge}</td>
            </tr>
            """
    else:
        timer_rows_html = '<tr><td colspan="4" style="padding:16px;text-align:center;color:#64748b;">No Jellyfin recording timers scheduled in the next 48 hours.</td></tr>'

    # Games / Fixture Watch Table Rows
    game_rows_html = ""
    if report["games"]:
        for g in report["games"]:
            if g["has_timer"]:
                status_html = f'<span style="color:#166534;font-weight:600;font-size:12px;">✅ Timer Set: {html_lib.escape(g["recording"])}</span>'
            else:
                status_html = '<span style="display:inline-block;padding:2px 6px;font-size:11px;background:#fef3c7;color:#92400e;border-radius:3px;font-weight:700;">⚠️ NO TIMER SCHEDULED</span>'

            bcs = ", ".join(g["broadcasts"]) if g["broadcasts"] else "Standard Lineup"
            game_rows_html += f"""
            <tr style="border-bottom:1px solid #e2e8f0;">
              <td style="padding:10px 8px;font-weight:600;color:#0f172a;vertical-align:top;">
                <div>{html_lib.escape(g['team'])}</div>
                <div style="font-size:12px;color:#475569;font-weight:normal;">{html_lib.escape(g['name'])}</div>
              </td>
              <td style="padding:10px 8px;color:#334155;font-size:12.5px;vertical-align:top;">
                <strong>{g['start'].strftime('%a %d %b, %H:%M')}</strong>
              </td>
              <td style="padding:10px 8px;color:#1e293b;font-size:12.5px;vertical-align:top;">
                <div>{html_lib.escape(g['channel'])}</div>
                <div style="font-size:11.5px;color:#64748b;">{html_lib.escape(bcs)}</div>
              </td>
              <td style="padding:10px 8px;vertical-align:top;">{status_html}</td>
            </tr>
            """
    else:
        game_rows_html = '<tr><td colspan="4" style="padding:16px;text-align:center;color:#64748b;">No followed-team fixtures in the next 48 hours.</td></tr>'

    # Tuner Conflicts Section
    conflicts_html = ""
    if report["conflicts"]:
        conf_items = ""
        for c in report["conflicts"]:
            conf_items += f"""
            <div style="background:#fee2e2;border:1px solid #f87171;border-radius:6px;padding:12px;margin-bottom:8px;">
              <div style="font-weight:700;color:#991b1b;font-size:14px;">💥 1-STREAM TUNER CONTENTION CONFLICT</div>
              <div style="margin-top:4px;color:#7f1d1d;font-size:13px;">
                <strong>Timer 1:</strong> {html_lib.escape(c['timer1']['name'])} ({html_lib.escape(c['timer1']['channel'])})<br>
                <strong>Timer 2:</strong> {html_lib.escape(c['timer2']['name'])} ({html_lib.escape(c['timer2']['channel'])})<br>
                <strong>Overlapping Window:</strong> {c['overlap_start'].strftime('%a %d %b %H:%M')} to {c['overlap_end'].strftime('%H:%M %Z')} (Duration: {c['overlap_str']})
              </div>
            </div>
            """
        conflicts_html = f"""
        <div style="margin:0 24px 16px 24px;">
          <div style="font-size:15px;font-weight:700;color:#991b1b;margin-bottom:8px;">Tuner Contention Audit</div>
          {conf_items}
        </div>
        """
    else:
        conflicts_html = """
        <div style="margin:0 24px 16px 24px;background:#f8fafc;border:1px solid #e2e8f0;border-radius:6px;padding:10px 14px;font-size:13px;color:#334155;">
          ✅ <strong>Tuner Concurrency:</strong> Clean. All scheduled timers have non-overlapping capture windows (1 stream account verified).
        </div>
        """

    # AGY Section
    agy_html = ""
    if agy_diag:
        agy_html = f"""
        <div style="margin:0 24px 16px 24px;background:#f8fafc;border:1px solid #cbd5e1;border-radius:8px;overflow:hidden;">
          <div style="background:#e2e8f0;padding:10px 16px;font-weight:700;color:#1e293b;font-size:13.5px;">
            🤖 AGY Schedule Anomaly Root-Cause Diagnosis
          </div>
          <div style="padding:14px 16px;">
            <div style="font-weight:600;color:#0f172a;font-size:13.5px;margin-bottom:6px;">Summary:</div>
            <div style="color:#334155;font-size:13px;line-height:1.55;margin-bottom:12px;">{html_lib.escape(agy_diag.get('summary', ''))}</div>
            <div style="font-weight:600;color:#0f172a;font-size:13.5px;margin-bottom:6px;">Technical Evidence:</div>
            <pre style="background:#1e293b;color:#f8fafc;padding:12px;border-radius:6px;font-size:12px;line-height:1.45;overflow-x:auto;white-space:pre-wrap;">{html_lib.escape(agy_diag.get('technical', ''))}</pre>
          </div>
        </div>
        """

    html_body = f"""<!doctype html>
<html>
<body style="margin:0;padding:0;background:#f2f3f5;font-family:-apple-system,BlinkMacSystemFont,Segoe UI,Roboto,Arial,sans-serif;">
<div style="max-width:700px;margin:24px auto;background:#ffffff;border-radius:10px;overflow:hidden;box-shadow:0 1px 4px rgba(0,0,0,.12);">

  <!-- Header Banner -->
  <div style="background:#1a1d29;padding:20px 24px;">
    <div style="font-size:24px;">🌅</div>
    <div style="color:#ffffff;font-size:18px;font-weight:600;margin-top:4px;">DVR Morning Pre-Flight Digest</div>
    <div style="color:#9aa0ae;font-size:13px;margin-top:2px;">48-Hour recording schedule, power-window audit & conflict verification &middot; {now_str}</div>
  </div>

  <!-- BLUF Summary Callout -->
  <div style="padding:18px 24px 4px 24px;">
    <div style="background:{badge_light};border-left:4px solid {badge_bg};border-radius:6px;padding:14px 16px;">
      <div style="display:inline-block;background:{badge_bg};color:#ffffff;font-size:11px;font-weight:700;letter-spacing:.06em;padding:3px 8px;border-radius:3px;margin-bottom:8px;">{badge_label}</div>
      <div style="color:#0f172a;font-size:14px;line-height:1.55;">{bluf_summary}</div>
      <div style="color:#334155;font-size:13px;line-height:1.55;margin-top:10px;font-weight:500;">{bluf_action}</div>
    </div>
  </div>

  <!-- Power & Host State Overview -->
  <div style="margin:16px 24px;background:#f8fafc;border:1px solid #e2e8f0;border-radius:8px;padding:14px 16px;">
    <div style="font-size:14px;font-weight:700;color:#0f172a;margin-bottom:8px;">⚡ Host Power & Override Status</div>
    <table style="width:100%;border-collapse:collapse;font-size:13px;color:#1e293b;">
      <tr>
        <td style="padding:4px 0;font-weight:600;color:#64748b;width:150px;">Physical Mains Timer:</td>
        <td style="padding:4px 0;">ON at 05:05 &middot; CUT at 22:25 (Fri/Sat extended to 01:00 CEST)</td>
      </tr>
      <tr>
        <td style="padding:4px 0;font-weight:600;color:#64748b;">Shutdown Override:</td>
        <td style="padding:4px 0;">
          <strong>{'🟢 ACTIVE' if report['override_state']['active'] else '⚪ INACTIVE'}</strong> &middot; {report['override_state']['description']}
        </td>
      </tr>
      <tr>
        <td style="padding:4px 0;font-weight:600;color:#64748b;">Audit Window:</td>
        <td style="padding:4px 0;">{now_str} &rarr; {win_str} (48 hours)</td>
      </tr>
    </table>
  </div>

  <!-- Tuner Conflicts Banner -->
  {conflicts_html}

  <!-- Scheduled Jellyfin Timers Card -->
  <div style="margin:16px 24px 20px 24px;">
    <div style="font-size:15px;font-weight:700;color:#0f172a;margin-bottom:8px;">📹 Scheduled Jellyfin Recordings (Next 48 Hours)</div>
    <table style="width:100%;border-collapse:collapse;font-size:13px;border:1px solid #e2e8f0;border-radius:6px;overflow:hidden;">
      <thead>
        <tr style="background:#f1f5f9;color:#475569;text-align:left;font-size:12px;">
          <th style="padding:8px;border-bottom:1px solid #e2e8f0;">Program</th>
          <th style="padding:8px;border-bottom:1px solid #e2e8f0;">Channel</th>
          <th style="padding:8px;border-bottom:1px solid #e2e8f0;">Air Time</th>
          <th style="padding:8px;border-bottom:1px solid #e2e8f0;">Power Status</th>
        </tr>
      </thead>
      <tbody>
        {timer_rows_html}
      </tbody>
    </table>
  </div>

  <!-- Followed Teams Fixture Watch Card -->
  <div style="margin:16px 24px 20px 24px;">
    <div style="font-size:15px;font-weight:700;color:#0f172a;margin-bottom:8px;">⚽ Followed Teams Fixture Watch (48h Window)</div>
    <table style="width:100%;border-collapse:collapse;font-size:13px;border:1px solid #e2e8f0;border-radius:6px;overflow:hidden;">
      <thead>
        <tr style="background:#f1f5f9;color:#475569;text-align:left;font-size:12px;">
          <th style="padding:8px;border-bottom:1px solid #e2e8f0;">Matchup</th>
          <th style="padding:8px;border-bottom:1px solid #e2e8f0;">Kickoff</th>
          <th style="padding:8px;border-bottom:1px solid #e2e8f0;">Channel / Feeds</th>
          <th style="padding:8px;border-bottom:1px solid #e2e8f0;">Timer Status</th>
        </tr>
      </thead>
      <tbody>
        {game_rows_html}
      </tbody>
    </table>
  </div>

  {agy_html}

  <!-- Footer -->
  <div style="padding:14px 24px;background:#f7f8fa;border-top:1px solid #e8e9ec;font-size:12px;color:#6b7280;">
    Sent by <code>dvr-preflight-digest</code> on pve-01 &middot; Automated Daily Pre-Flight Verification &middot; Port 465 Relay.
  </div>

</div>
</body>
</html>
"""

    text_body = f"""================================================================
DVR PRE-FLIGHT DIGEST — {verdict}
================================================================
Generated: {now_str}
Coverage:  {now_str} to {win_str} (48 Hours)

VERDICT: {verdict}

HOST & POWER OVERVIEW:
- Mains Power Schedule: ON 05:05, CUT 22:25 (Fri/Sat 01:00 CEST)
- Shutdown Override:   {'ACTIVE until ' + report['override_state']['expiry_str'] if report['override_state']['active'] else 'INACTIVE'}
  Details: {report['override_state']['description']}

SCHEDULED JELLYFIN TIMERS ({num_timers} in window):
"""
    if report["timers"]:
        for t in report["timers"]:
            pwr_note = "NEEDS POWER" if t["needsPower"] else "Inside Power Window"
            if t["needsPower"] and t["overrideCovers"]:
                pwr_note += " (Covered by active override)"
            text_body += f"""- {t['name']}
  Channel:  {t['channel']}
  Air Time: {fmt_time_span(t['start'], t['end'])}
  Padded:   {t['padded_start'].strftime('%H:%M')} - {t['padded_end'].strftime('%H:%M %Z')} (+{t['post_pad_sec']//60}m)
  Power:    {pwr_note}
"""
    else:
        text_body += "- None scheduled in the next 48 hours.\n"

    text_body += f"""
TUNER CONCURRENCY (1 Stream Limit):
"""
    if report["conflicts"]:
        for c in report["conflicts"]:
            text_body += f"""- 💥 CONFLICT DETECTED:
  Timer 1: {c['timer1']['name']} ({c['timer1']['channel']})
  Timer 2: {c['timer2']['name']} ({c['timer2']['channel']})
  Overlap: {c['overlap_start'].strftime('%a %d %b %H:%M')} to {c['overlap_end'].strftime('%H:%M %Z')} ({c['overlap_str']})
"""
    else:
        text_body += "- ✅ Clean: No overlapping padded timer windows detected.\n"

    text_body += f"""
FOLLOWED TEAMS FIXTURE WATCH ({num_games} matches in window, {num_untracked} missing timers):
"""
    if report["games"]:
        for g in report["games"]:
            rec_str = f"✅ Recorded: {g['recording']}" if g["has_timer"] else "⚠️ NO TIMER SCHEDULED"
            text_body += f"""- {g['team']}: {g['name']}
  Kickoff: {g['start'].strftime('%a %d %b %H:%M %Z')}
  Channel: {g['channel']}
  Status:  {rec_str}
"""
    else:
        text_body += "- No followed-team games in the next 48 hours.\n"

    if report["anomalies"]:
        text_body += f"""
================================================================
ATTENTION ITEMS & ACTION REQUIRED:
"""
        for a in report["anomalies"]:
            text_body += f"- {a}\n"
        text_body += "\nRECOMMENDED ACTIONS:\n"
        for act in report["action_items"]:
            text_body += f"- {act}\n"

    if agy_diag:
        text_body += f"""
================================================================
AGY ROOT-CAUSE DIAGNOSIS:
================================================================
Summary:
{agy_diag.get('summary', '')}

Technical Details:
{agy_diag.get('technical', '')}
"""

    text_body += f"""
----------------------------------------------------------------
Sent by dvr-preflight-digest on pve-01 (Port 465 relay)
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
        logging.info("Pre-flight report email delivered successfully via sendmail to %s", recipient)
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
            logging.info("Pre-flight report email delivered via mail fallback to %s", recipient)
            return True
        except Exception as exc2:
            logging.error("Fail-soft: Both sendmail and mail CLI failed (%s). Continuing cleanly.", exc2)
            return False


# ── Main Entrypoint ────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="DVR Morning Pre-Flight Recording Digest")
    parser.add_argument("--dry-run", action="store_true", help="Print rendered report to stdout without sending email")
    parser.add_argument("--recipient", type=str, default=MAILTO, help="Email recipient address")
    parser.add_argument("--hours", type=int, default=DEFAULT_WINDOW_HOURS, help="Audit lookahead window in hours (default: 48)")
    parser.add_argument("--no-agy", action="store_true", help="Skip AGY diagnosis on schedule conflicts")
    parser.add_argument("--force-email", action="store_true", help="Send email even if run interactively")

    args = parser.parse_args()

    try:
        report = build_preflight_report(hours=args.hours)
        logging.info(
            "Pre-flight audit completed -> Verdict: %s (Timers: %d, Conflicts: %d, Untracked Games: %d)",
            report["verdict"], len(report["timers"]), len(report["conflicts"]), len(report["untracked_games"])
        )

        agy_diag = None
        if report["verdict"] == "ATTENTION NEEDED" and not args.no_agy and not args.dry_run:
            agy_diag = dispatch_agy_investigation(report)

        subject, text_body, html_body = render_email(report, agy_diag)

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
        logging.error("Fail-soft: Unhandled exception in pre-flight digest: %s", exc, exc_info=True)
        sys.exit(0)


if __name__ == "__main__":
    main()
