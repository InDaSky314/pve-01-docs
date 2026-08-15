#!/usr/bin/env python3
"""Webhook receiver for Grafana alerts.

Built 2026-08-12 per owner request. Grafana POSTs here (a new webhook
contact point, alongside the existing owner-email one) whenever an alert
fires. On receipt, dispatches Agy in strict diagnose-only mode (same safety
framing proven live the same night on the real Aug-9-recording
investigation) to independently investigate root cause, then emails the
owner Agy's findings once it finishes.

Deliberately conservative (owner's explicit choice, asked directly rather
than assumed): this NEVER applies a fix automatically. It investigates and
proposes only. The owner, or Claude Code when asked, applies anything that
needs applying, after reviewing what Agy actually found -- not before.

Cooldown: Grafana re-sends "firing" notifications periodically for as long
as an alert stays active (confirmed same night: an alert can stay firing
for days with periodic re-sends). Without a cooldown this would re-dispatch
Agy every time Grafana re-notifies, not just on a genuinely new occurrence.
"""
from __future__ import annotations

import html as html_lib
import json
import subprocess
import threading
import time
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

PORT = 9106
STATE_DIR = Path("/var/lib/alert-responder")
COOLDOWN_SECONDS = 2 * 3600
MAILTO = "nathan.karras@gmail.com"
AGY_TASK = "/root/bin/agy-task.sh"
AGY_TIMEOUT_MIN = 20


def load_cooldowns() -> dict:
    f = STATE_DIR / "cooldowns.json"
    try:
        return json.loads(f.read_text())
    except Exception:
        return {}


def save_cooldowns(d: dict) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    f = STATE_DIR / "cooldowns.json"
    tmp = f.with_suffix(".tmp")
    tmp.write_text(json.dumps(d))
    tmp.replace(f)


def slugify(name: str) -> str:
    return "".join(c if c.isalnum() else "-" for c in name.lower())[:40].strip("-") or "alert"


FROM_ADDR = "kopr.notify@gmail.com"  # same authenticated account Grafana's own alert emails already send from
FROM_NAME = "Media-Core Investigator"


def render_html_email(subject: str, body_lines: list[str]) -> str:
    """Dress up the plain-text report into something readable at a glance,
    matching the visual language of the Grafana alert emails this follows up
    on (colored status badge, card layout) rather than a wall of monospace
    text. Added 2026-08-12 per owner request."""
    is_failure = "FAILED" in subject
    badge_color = "#c0392b" if is_failure else "#2f6fed"
    badge_text = "INVESTIGATION FAILED" if is_failure else "INVESTIGATION COMPLETE"
    body_text = "\n".join(body_lines)
    body_escaped = html_lib.escape(body_text)
    return f"""\
<!doctype html>
<html><body style="margin:0;padding:0;background:#f2f3f5;font-family:-apple-system,Segoe UI,Roboto,Arial,sans-serif;">
<div style="max-width:640px;margin:24px auto;background:#ffffff;border-radius:10px;overflow:hidden;
            box-shadow:0 1px 4px rgba(0,0,0,.12);">
  <div style="background:#1a1d29;padding:20px 24px;">
    <div style="font-size:20px;">🔎</div>
    <div style="color:#ffffff;font-size:17px;font-weight:600;margin-top:4px;">Media-Core Automated Investigation</div>
    <div style="color:#9aa0ae;font-size:13px;margin-top:2px;">Triggered by a Grafana alert &middot; diagnose-only, nothing changed automatically</div>
  </div>
  <div style="padding:20px 24px 4px 24px;">
    <span style="display:inline-block;background:{badge_color};color:#fff;font-size:12px;font-weight:700;
                 letter-spacing:.03em;padding:4px 10px;border-radius:4px;">{badge_text}</span>
    <div style="font-size:16px;font-weight:600;color:#1a1d29;margin-top:12px;">{html_lib.escape(subject)}</div>
  </div>
  <div style="padding:8px 24px 20px 24px;">
    <pre style="background:#0f1117;color:#d7dae0;font-size:12.5px;line-height:1.5;padding:16px;
                border-radius:8px;overflow-x:auto;white-space:pre-wrap;word-break:break-word;
                font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;">{body_escaped}</pre>
  </div>
  <div style="padding:14px 24px;background:#f7f8fa;border-top:1px solid #e8e9ec;">
    <span style="font-size:12px;color:#6b7280;">Sent by the alert-responder framework on pve-01 &middot;
    Agy investigated, nothing was applied automatically &middot; review and apply the fix yourself, or ask Claude Code.</span>
  </div>
</div>
</body></html>"""


def notify_owner(subject: str, body_lines: list[str]) -> None:
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = f'"{FROM_NAME}" <{FROM_ADDR}>'
    msg["To"] = MAILTO
    msg.attach(MIMEText("\n".join(body_lines), "plain"))
    msg.attach(MIMEText(render_html_email(subject, body_lines), "html"))
    try:
        subprocess.run(["/usr/sbin/sendmail", "-t", "-oi"], input=msg.as_string(), text=True, check=True, timeout=20)
    except Exception:
        # Fall back to the plain path rather than silently losing the notification.
        subprocess.run(["mail", "-s", subject, MAILTO], input="\n".join(body_lines), text=True, check=False)


def wait_and_notify(alertname: str, summary: str, report_path: str) -> None:
    deadline = time.time() + (AGY_TIMEOUT_MIN + 5) * 60
    p = Path(report_path)
    while time.time() < deadline:
        if p.exists() and p.stat().st_size > 0:
            break
        time.sleep(15)
    body = p.read_text() if p.exists() else "(no report produced -- Agy may have timed out or crashed before writing one)"
    notify_owner(f"DVR Alert Investigation: {alertname}",
                 [f"Alert summary: {summary}", "", "This is a DIAGNOSIS ONLY -- nothing was changed automatically.",
                  "Review below, then apply the fix yourself or ask Claude Code to.", "",
                  "--- Agy's findings ---", "", body])


def dispatch_and_notify(alertname: str, summary: str, labels: dict) -> None:
    slug = f"alert-{slugify(alertname)}-{int(time.time())}"
    prompt = (
        f"A monitoring alert just fired: '{alertname}'.\n\n"
        f"Summary: {summary}\n\n"
        f"Labels: {json.dumps(labels)}\n\n"
        "Investigate the real root cause -- don't assume anything is already "
        "known or already fixed, treat this as a first, independent look. "
        "Check the actual current state of whatever this alert is about, not "
        "just whether the alert condition has since cleared on its own.\n\n"
        "IMPORTANT: this is diagnostic only. Do NOT restart any services, do "
        "NOT modify any files or configuration, do NOT apply any fix, do NOT "
        "run any destructive or state-changing command. If you find a real "
        "problem and know the fix, describe it precisely in your report "
        "(what's wrong, why, and the exact fix) instead of applying it -- the "
        "owner or Claude Code will apply it after reviewing your report."
    )
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    prompt_file = STATE_DIR / f"{slug}.prompt.md"
    prompt_file.write_text(prompt)

    try:
        # Bug found + fixed 2026-08-12 before this ever ran for a real alert:
        # agy-task.sh's own startup (before it even gets to backgrounding)
        # took ~70s when invoked this way (subprocess, no TTY) in direct
        # testing -- a 30s timeout here killed it every time even though
        # it wasn't actually hung, just slower to start than it looked when
        # run interactively. 300s gives generous margin -- observed startup latency varied
        # from ~70s to 120s+ across repeated tests, and since this whole call
        # runs inside a background thread, a longer wait here costs nothing.
        proc = subprocess.run(
            [AGY_TASK, "run", slug, "diagnose", f"@{prompt_file}", "--bg", "--timeout", f"{AGY_TIMEOUT_MIN}m"],
            capture_output=True, text=True, timeout=300,
        )
    except Exception as exc:
        notify_owner(f"DVR Alert Auto-Investigation FAILED to start -- {alertname}",
                     [f"Could not dispatch Agy: {exc}"])
        return

    report_path = None
    for line in proc.stdout.splitlines():
        line = line.strip()
        if line.startswith("/root/agy-reports/") and line.endswith(".md"):
            report_path = line
    if not report_path:
        notify_owner(f"DVR Alert Auto-Investigation FAILED to start -- {alertname}",
                     [f"Could not find Agy's report path in dispatch output.", "",
                      f"stdout: {proc.stdout}", f"stderr: {proc.stderr}"])
        return

    wait_and_notify(alertname, summary, report_path)


class Handler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length)
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"ok")
        try:
            payload = json.loads(raw)
        except Exception:
            return

        cooldowns = load_cooldowns()
        now = time.time()
        changed = False
        for alert in payload.get("alerts", []):
            if alert.get("status") != "firing":
                continue
            fp = alert.get("fingerprint", "")
            if now - cooldowns.get(fp, 0) < COOLDOWN_SECONDS:
                continue
            cooldowns[fp] = now
            changed = True
            labels = alert.get("labels", {})
            annotations = alert.get("annotations", {})
            alertname = labels.get("alertname", "unknown alert")
            summary = annotations.get("summary", "")
            threading.Thread(target=dispatch_and_notify, args=(alertname, summary, labels), daemon=True).start()
        if changed:
            save_cooldowns(cooldowns)

    def log_message(self, format: str, *args) -> None:  # noqa: A002
        pass  # systemd/journald captures real access via its own stdout wiring; keep this quiet


if __name__ == "__main__":
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    server = HTTPServer(("0.0.0.0", PORT), Handler)
    print(f"Alert responder listening on :{PORT}")
    server.serve_forever()
