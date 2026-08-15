# Grafana alert responder (`alert-responder.py`)

**Executable Location:** `/root/bin/alert-responder.py`  
**Systemd Unit:** `alert-responder.service` (runs continuously, listening on TCP port 9106)  
**State Directory:** `/var/lib/alert-responder/` (`cooldowns.json`, `<slug>.prompt.md`)  
**Orchestrator Wrapper:** `/root/bin/agy-task.sh`

```
┌─────────────────────────┐
│     Grafana Alerts      │
└────────────┬────────────┘
             │ HTTP POST Webhook
             ▼
┌─────────────────────────┐
│  /root/bin/             │
│  alert-responder.py     │◄───── 2-Hour Fingerprint Cooldown Check
│  (:9106)                │       (/var/lib/alert-responder/cooldowns.json)
└────────────┬────────────┘
             │ (Background Thread Dispatch)
             ▼
┌─────────────────────────┐
│   /root/bin/            │
│   agy-task.sh           │
└────────────┬────────────┘
             │ (Mode: diagnose ONLY)
             ▼
┌─────────────────────────┐
│   agy Subagent          │
│   (Diagnose Only)       │──────► MUST NOT restart services, edit configs,
└────────────┬────────────┘        or execute destructive commands.
             │ (Outputs Markdown Report)
             ▼
┌─────────────────────────┐
│  HTML Email Renderer    │
│  & Sendmail Dispatch    │──────► Owner Email (kopr.notify@gmail.com)
└─────────────────────────┘
```

#### Core Components & Safety Boundaries

1. **Webhook Ingestion & Fingerprint Cooldown:**
   - Listens for HTTP POST requests from Grafana's webhook contact point.
   - Extracts alert status, fingerprints, labels, and annotations.
   - Maintains a 2-hour cooldown per alert fingerprint in `/var/lib/alert-responder/cooldowns.json` (`COOLDOWN_SECONDS = 7200`) to prevent duplicate investigations during persistent firing alerts.

2. **Strict DIAGNOSE-ONLY Safety Boundary:**
   - For uncooled firing alerts, creates an investigation prompt file at `/var/lib/alert-responder/<slug>.prompt.md`.
   - Dispatches `agy` via `/root/bin/agy-task.sh run <slug> diagnose @prompt --bg --timeout 20m`.
   - **Enforced Constraint:** `agy` is invoked strictly in `diagnose` mode. The prompt explicitly prohibits applying fixes, restarting services, editing configuration files, or executing state-changing commands. `agy` investigates root cause and produces a detailed diagnostic report only.

3. **Background Dispatch & Timeout Handling:**
   - Dispatch runs asynchronously in a daemon thread so the HTTP webhook responds immediately (`200 OK`).
   - Subprocess timeout for launching `agy-task.sh` is set to 300 seconds to accommodate non-interactive CLI startup latency.

4. **HTML Email Report Delivery:**
   - `wait_and_notify()` monitors for `agy`'s report output under `/root/agy-reports/<slug>.md`.
   - Formats the report using `render_html_email()` into a responsive HTML email card featuring status badges (`INVESTIGATION COMPLETE` vs `INVESTIGATION FAILED`), dark code blocks, and structured metadata.
   - Sends MIME multipart emails via `/usr/sbin/sendmail` from `kopr.notify@gmail.com` to `nathan.karras@gmail.com` (with fallback to `mail -s`).

---

