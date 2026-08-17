#!/usr/bin/env python3
"""
Bayern Munich English PPV Match Detector & Alerting Wrapper (Phase 2).

Runs bayern_ppv_detector's stream matching logic against target provider categories.
On a confident match (confidence score >= 150), formats a styled HTML email matching
the established project design (color-coded sections, action framing) and sends an
alert to nathan.karras@gmail.com.

Tracks state in /var/lib/bayern-ppv-alert/state.json to prevent duplicate emails for
the same match and slot.

Usage:
  python3 bayern_ppv_alert.py                      # Production check for Bayern Munich
  python3 bayern_ppv_alert.py --test              # Live test mode using substitute event
  python3 bayern_ppv_alert.py --dry-run           # Dry run (print email, don't send)
"""

import argparse
import html as html_lib
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

# Add scripts directory to path to import bayern_ppv_detector
SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import bayern_ppv_detector as detector

DEFAULT_RECIPIENT = "nathan.karras@gmail.com"
DEFAULT_MIN_SCORE = 150
STATE_DIR = Path("/var/lib/bayern-ppv-alert")
STATE_FILE = STATE_DIR / "state.json"
FROM_ADDR = "kopr.notify@gmail.com"
FROM_NAME = "Bayern PPV Alert System"


def load_state() -> dict:
    """Load notification state history."""
    try:
        if STATE_FILE.exists():
            return json.loads(STATE_FILE.read_text())
    except Exception as exc:
        print(f"Warning: Could not read state file: {exc}", file=sys.stderr)
    return {"notified": {}}


def save_state(state: dict) -> None:
    """Save notification state history."""
    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        tmp = STATE_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(state, indent=2))
        tmp.replace(STATE_FILE)
    except Exception as exc:
        print(f"Warning: Could not write state file: {exc}", file=sys.stderr)


def is_already_notified(state: dict, match_key: str) -> bool:
    """Check if we already sent an email for this match key within 24 hours."""
    notified = state.get("notified", {})
    if match_key not in notified:
        return False
    sent_timestamp = notified[match_key].get("timestamp", 0)
    now = datetime.now(timezone.utc).timestamp()
    # Suppress repeat alerts within 24 hours for the exact same match key
    return (now - sent_timestamp) < 86400


def record_notification(state: dict, match_key: str, match_info: dict) -> None:
    """Record that an email notification was sent."""
    if "notified" not in state:
        state["notified"] = {}
    state["notified"][match_key] = {
        "timestamp": datetime.now(timezone.utc).timestamp(),
        "stream_id": match_info.get("stream_id"),
        "channel_slot": match_info.get("channel_slot"),
        "raw_name": match_info.get("raw_name"),
        "score": match_info.get("score"),
    }
    save_state(state)


def render_email_content(match_info: dict, is_test: bool = False, test_context: dict = None) -> tuple[str, str, str]:
    """
    Renders (subject, text_body, html_body) for the alert email.
    Matches the established visual style (lineup-watch-email.py & alert-responder.py).
    """
    stream_id = match_info.get("stream_id", "N/A")
    raw_name = match_info.get("raw_name", "")
    channel_slot = match_info.get("channel_slot", "Unknown Slot")
    category = match_info.get("category", "Unknown Category")
    lang = match_info.get("lang", "EN")
    score = match_info.get("score", 0)
    reasons = match_info.get("reasons", [])

    espn_fixture = match_info.get("espn_fixture", {})
    fixture_name = espn_fixture.get("match_name") if espn_fixture else (test_context.get("match_name") if test_context else raw_name)
    kickoff = espn_fixture.get("kickoff") if espn_fixture else (test_context.get("kickoff") if test_context else None)
    
    if isinstance(kickoff, datetime):
        kickoff_str = kickoff.strftime("%Y-%m-%d %H:%M UTC")
    else:
        kickoff_str = str(kickoff) if kickoff else "N/A"

    if is_test:
        subject = "TEST: Bayern PPV Alert System Validation"
        badge_text = "TEST MATCH DETECTED"
        badge_bg = "#2f6fed"  # Blue for test
        test_banner = f"""
  <div style="margin:16px 24px 0 24px;background:#eef4ff;border:1px solid #c7d9ff;border-radius:6px;padding:12px 14px;">
    <div style="color:#1d4ed8;font-size:13px;font-weight:700;">⚠️ SYSTEM VALIDATION TEST</div>
    <div style="color:#1e293b;font-size:13px;margin-top:4px;line-height:1.5;">
      This email validates the <strong>Phase 2 Standalone PPV Detector & Alerting Engine</strong>.
      Testing was performed using live provider stream <code>{stream_id}</code> (<em>{html_lib.escape(test_context.get('substitute_team', 'Substitute Team'))}</em>).
    </div>
  </div>"""
    else:
        subject = f"[MATCH ALERT] Bayern Munich PPV Stream Detected ({channel_slot})"
        badge_text = "MATCH DETECTED"
        badge_bg = "#1e824c"  # Green for production match
        test_banner = ""

    reasons_li = "".join(f"<li>{html_lib.escape(r)}</li>" for r in reasons)

    html_body = f"""\
<!doctype html>
<html>
<body style="margin:0;padding:0;background:#f2f3f5;font-family:-apple-system,BlinkMacSystemFont,Segoe UI,Roboto,Arial,sans-serif;">
<div style="max-width:680px;margin:24px auto;background:#ffffff;border-radius:10px;overflow:hidden;box-shadow:0 1px 4px rgba(0,0,0,.12);">
  
  <!-- Top Header Banner -->
  <div style="background:#1a1d29;padding:20px 24px;">
    <div style="font-size:24px;">⚽</div>
    <div style="color:#ffffff;font-size:18px;font-weight:600;margin-top:4px;">Bayern Munich PPV Match Detector</div>
    <div style="color:#9aa0ae;font-size:13px;margin-top:2px;">Dynamic English-Language PPV Slot Notification</div>
  </div>
  
  {test_banner}

  <!-- Status & Match Summary Card -->
  <div style="padding:20px 24px 12px 24px;">
    <span style="display:inline-block;background:{badge_bg};color:#ffffff;font-size:12px;font-weight:700;letter-spacing:.04em;padding:4px 10px;border-radius:4px;">{badge_text}</span>
    <div style="font-size:18px;font-weight:700;color:#1a1d29;margin-top:10px;">{html_lib.escape(fixture_name)}</div>
  </div>

  <!-- Key Attributes Table Card -->
  <div style="margin:0 24px 16px 24px;background:#f8fafc;border:1px solid #e2e8f0;border-radius:8px;padding:16px;">
    <table style="width:100%;border-collapse:collapse;font-size:13.5px;color:#1e293b;">
      <tr>
        <td style="padding:6px 0;font-weight:600;width:140px;color:#64748b;">Exact Channel Slot:</td>
        <td style="padding:6px 0;font-weight:700;color:#0f172a;font-family:ui-monospace,monospace;">{html_lib.escape(channel_slot)}</td>
      </tr>
      <tr>
        <td style="padding:6px 0;font-weight:600;color:#64748b;">Kickoff Time:</td>
        <td style="padding:6px 0;font-weight:600;color:#0f172a;">{html_lib.escape(kickoff_str)}</td>
      </tr>
      <tr>
        <td style="padding:6px 0;font-weight:600;color:#64748b;">Provider Stream ID:</td>
        <td style="padding:6px 0;font-family:ui-monospace,monospace;">{stream_id}</td>
      </tr>
      <tr>
        <td style="padding:6px 0;font-weight:600;color:#64748b;">Category & Lang:</td>
        <td style="padding:6px 0;">{html_lib.escape(category)} ({lang})</td>
      </tr>
      <tr>
        <td style="padding:6px 0;font-weight:600;color:#64748b;">Confidence Score:</td>
        <td style="padding:6px 0;">
          <span style="display:inline-block;background:#dcfce7;color:#166534;font-weight:700;padding:2px 8px;border-radius:4px;">{score} pts</span>
          <span style="color:#64748b;font-size:12px;margin-left:6px;">(Threshold: 150 pts)</span>
        </td>
      </tr>
      <tr>
        <td style="padding:6px 0;font-weight:600;color:#64748b;vertical-align:top;">Raw Title:</td>
        <td style="padding:6px 0;font-size:12px;color:#475569;font-family:ui-monospace,monospace;word-break:break-all;">{html_lib.escape(raw_name)}</td>
      </tr>
    </table>
  </div>

  <!-- Detection Breakdown Section -->
  <div style="margin:0 24px 16px 24px;">
    <div style="font-size:12px;font-weight:700;letter-spacing:.03em;color:#475569;text-transform:uppercase;margin-bottom:6px;">Detection Scoring Breakdown</div>
    <div style="background:#f1f5f9;border-left:3px solid #0ea5e9;border-radius:4px;padding:12px 16px;">
      <ul style="margin:0;padding-left:18px;font-size:13px;color:#334155;line-height:1.6;">
        {reasons_li}
      </ul>
    </div>
  </div>

  <!-- Action Required / Next Steps Card -->
  <div style="margin:0 24px 20px 24px;background:#1a1d29;border-radius:8px;padding:16px 18px;">
    <div style="color:#ffc52f;font-size:12px;font-weight:700;letter-spacing:.04em;margin-bottom:8px;">
      ✎ ACTIONABLE NEXT STEPS FOR VIEWING & RECORDING
    </div>
    <div style="color:#d7dae0;font-size:12.5px;line-height:1.6;font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;">
1. Open Jellyfin / Threadfin Dashboard.<br>
2. Navigate to channel slot <strong>{html_lib.escape(channel_slot)}</strong> (Category: {html_lib.escape(category)}).<br>
3. Confirm stream video feed is live prior to kickoff.<br>
4. Schedule manual DVR timer in Jellyfin if auto-recording is not active.
    </div>
  </div>

  <!-- Footer -->
  <div style="padding:14px 24px;background:#f7f8fa;border-top:1px solid #e8e9ec;font-size:12px;color:#6b7280;">
    Sent by <code>bayern_ppv_alert.py</code> on pve-01 &middot; Standalone Bayern Munich PPV Match Detector (Phase 2).
  </div>

</div>
</body>
</html>
"""

    text_body = f"""================================================================
{subject}
================================================================

Match:           {fixture_name}
Channel Slot:    {channel_slot}
Kickoff Time:    {kickoff_str}
Stream ID:       {stream_id}
Category:        {category} ({lang})
Confidence Score:{score} pts (Threshold: 150 pts)
Raw Stream Name: {raw_name}

Scoring Rationale:
{chr(10).join(" - " + r for r in reasons)}

NEXT STEPS:
1. Open Jellyfin / Threadfin Dashboard.
2. Tune to channel slot '{channel_slot}'.
3. Confirm video feed prior to kickoff.
4. Schedule manual DVR timer if needed.

----------------------------------------------------------------
Sent by bayern_ppv_alert.py on pve-01
"""

    return subject, text_body, html_body


def send_email_alert(subject: str, text_body: str, html_body: str, recipient: str) -> bool:
    """Send MIME email via sendmail with fallback to mail CLI."""
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
            timeout=20,
        )
        print(f"Email alert successfully delivered via sendmail to {recipient}")
        return True
    except Exception as exc:
        print(f"sendmail delivery failed ({exc}). Trying /usr/bin/mail fallback...", file=sys.stderr)
        try:
            subprocess.run(
                ["/usr/bin/mail", "-s", subject, recipient],
                input=text_body,
                text=True,
                check=True,
                timeout=20,
            )
            print(f"Email delivered via mail fallback to {recipient}")
            return True
        except Exception as exc2:
            print(f"ERROR: Failed to send email via both sendmail and mail CLI: {exc2}", file=sys.stderr)
            return False


def run_test_validation(recipient: str, min_score: int, dry_run: bool) -> bool:
    """
    Executes live-test validation using a substitute currently-airing or scheduled PPV event.
    Target substitute event: Stream 1899866 in Category 573 (US| DAZN PPV):
    'Next | Puerto Rico U-14 vs. Trinidad & Tobago U-14 | CFU Challenge Series Boys U14 | 2026-08-17 | 13:30 (GMT) | 8K EXCLUSIVE | US: DAZN PPV 36'
    """
    print("================================================================")
    print(" BAYERN PPV ALERT SYSTEM - LIVE SYSTEM VALIDATION TEST")
    print("================================================================")
    print("Scanning live provider PPV categories for substitute test event...\n")

    substitute_team = "Puerto Rico"
    substitute_opponent = "Trinidad & Tobago"
    test_patterns = [re.compile(r"\b(Puerto\s+Rico)\b", re.IGNORECASE)]
    false_positives = re.compile(r"\b(women|frauen|basketball|bbl|highlights|replay|magazine)\b", re.IGNORECASE)

    test_fixture = {
        "match_name": "Puerto Rico U-14 vs. Trinidad & Tobago U-14",
        "opponent": substitute_opponent,
        "kickoff": datetime.fromisoformat("2026-08-17 13:30:00+00:00"),
    }

    # Fetch live streams from target category 573 (US| DAZN PPV) and others
    all_matches = []
    for cat_id in [573, 575, 1441, 1811]:
        cat_info = detector.TARGET_CATEGORIES.get(cat_id)
        if not cat_info:
            continue
        streams = detector.fetch_provider_ppv_streams(cat_id)
        for s in streams:
            match_res = detector.match_stream_generic(
                stream=s,
                cat_info=cat_info,
                team_patterns=test_patterns,
                false_positives_pattern=false_positives,
                espn_fixture=test_fixture,
            )
            if match_res:
                all_matches.append(match_res)

    if not all_matches:
        print("Error: Could not locate substitute test stream in live provider categories.", file=sys.stderr)
        return False

    # Pick highest-scoring match
    best_match = sorted(all_matches, key=lambda x: x["score"], reverse=True)[0]
    print(f"Substitute Match Detected!")
    print(f"  Stream ID:    {best_match['stream_id']}")
    print(f"  Raw Title:    {best_match['raw_name']}")
    print(f"  Channel Slot: {best_match['channel_slot']}")
    print(f"  Category:     {best_match['category']}")
    print(f"  Score:        {best_match['score']} (Min Threshold: {min_score})")
    print(f"  Reasons:      {', '.join(best_match['reasons'])}\n")

    test_context = {
        "substitute_team": substitute_team,
        "match_name": test_fixture["match_name"],
        "kickoff": test_fixture["kickoff"],
    }

    subject, text_body, html_body = render_email_content(best_match, is_test=True, test_context=test_context)

    if dry_run:
        print("--- [DRY-RUN MODE] Plain Text Email Output ---")
        print(text_body)
        print("--- [DRY-RUN MODE] End Email Output ---")
        return True

    print(f"Sending test validation email to {recipient}...")
    success = send_email_alert(subject, text_body, html_body, recipient)
    return success


def run_production_check(recipient: str, min_score: int, dry_run: bool, force: bool) -> bool:
    """
    Executes standard production check for upcoming Bayern Munich matches.
    """
    print("[1] Querying ESPN Schedule API for Bayern Munich...")
    fixtures = detector.fetch_espn_bayern_schedule()
    next_fixture = fixtures[0] if fixtures else None

    if next_fixture:
        print(f"  Next Fixture: {next_fixture['match_name']} ({next_fixture['kickoff'].strftime('%Y-%m-%d %H:%M UTC')})")
    else:
        print("  No upcoming Bayern Munich fixture returned by ESPN API.")

    print("[2] Scanning Live Provider Streams across PPV Categories...")
    all_matches = []
    for cat_id, cat_info in detector.TARGET_CATEGORIES.items():
        streams = detector.fetch_provider_ppv_streams(cat_id)
        for s in streams:
            m = detector.match_stream_for_bayern(s, cat_info, next_fixture)
            if m:
                all_matches.append(m)

    if not all_matches:
        print("No live Bayern Munich PPV streams detected at this time.")
        return True

    best_match = sorted(all_matches, key=lambda x: x["score"], reverse=True)[0]
    print(f"Match candidate found: '{best_match['raw_name']}' (Score: {best_match['score']})")

    if best_match["score"] < min_score and not force:
        print(f"Score {best_match['score']} is below minimum threshold {min_score}. Suppressing alert.")
        return True

    best_match["espn_fixture"] = next_fixture
    match_key = f"{best_match['channel_slot']}_{best_match['stream_id']}_{next_fixture['id'] if next_fixture else 'noespn'}"

    state = load_state()
    if is_already_notified(state, match_key) and not force:
        print(f"Notification already sent for match key '{match_key}' within 24 hours. Skipping.")
        return True

    subject, text_body, html_body = render_email_content(best_match, is_test=False)

    if dry_run:
        print("--- [DRY-RUN MODE] Plain Text Email Output ---")
        print(text_body)
        print("--- [DRY-RUN MODE] End Email Output ---")
        return True

    success = send_email_alert(subject, text_body, html_body, recipient)
    if success:
        record_notification(state, match_key, best_match)
    return success


def main():
    parser = argparse.ArgumentParser(description="Bayern Munich PPV Match Detector & Alerting System")
    parser.add_argument("--test", action="store_true", help="Run live system validation test using substitute event")
    parser.add_argument("--dry-run", action="store_true", help="Format and print email body without sending")
    parser.add_argument("--force-email", action="store_true", help="Force sending email ignoring score threshold and state")
    parser.add_argument("--recipient", default=DEFAULT_RECIPIENT, help="Email recipient address")
    parser.add_argument("--min-score", type=int, default=DEFAULT_MIN_SCORE, help="Minimum confidence score threshold")

    args = parser.parse_args()

    if args.test:
        success = run_test_validation(args.recipient, args.min_score, args.dry_run)
    else:
        success = run_production_check(args.recipient, args.min_score, args.dry_run, args.force_email)

    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
