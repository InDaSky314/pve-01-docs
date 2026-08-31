#!/usr/bin/env python3
"""Stack monitoring script for pve-01 homelab.

Monitors:
1. Recording sanity (detects fragmented/multi-file recordings & undersized recordings in /srv/shared-recordings)
2. Stack health (probes jellyfin-live, jellyfin-vod, jellyfin-npvr, nextpvr-live HTTP endpoints)
3. EPG freshness per stack (measures EPG file modification age in hours)

Exposes Prometheus metrics on port 9105 and sends alert events to Loki on CT 107 (192.168.9.164:3100).
"""
import os
import re
import glob
import time
import json
import subprocess
import urllib.request
import urllib.error
import urllib.parse
from http.server import HTTPServer, BaseHTTPRequestHandler
import threading

LOKI_URL = "http://192.168.9.164:3100/loki/api/v1/push"

# CT 110 (jellyfin-live) was stopped 2026-08-02 -- its Threadfin backend
# duplicates production, and the fragmentation mystery it existed to control
# for turned out to be the pre-recording-guard bug, not Threadfin. It is
# retired from monitoring rather than left to alarm: check_epg_freshness()
# reports 999.0 when it cannot stat the file, and check_stack_health() reports
# 0 when the endpoint is down, so leaving it in would fire epg-freshness-stale
# and stack-health-down every cycle for an outage we chose. To revive it:
# `pct set 110 --onboot 1 && pct start 110`, then restore the three
# "jellyfin-live" entries below.
#
# CT 111 (jellyfin-vod) was stopped 2026-08-31 -- verified zero user activity,
# duplicate of CT105's catalogue, disk preserved. It is retired from
# monitoring rather than left to alarm: check_stack_health() reports 0 when
# the endpoint is down, so leaving it in would fire stack-health-down every
# cycle for an outage we chose. To revive it:
# `pct set 111 --onboot 1 && pct start 111`, then restore the entries below.
metrics_data = {
    "stack_up": {
        "jellyfin-npvr": 1,
        "nextpvr-live": 1,
    },
    "epg_age_hours": {
        "jellyfin-npvr": 0.0,
        "media-core": 0.0,
    },
    "epg_real_channels": None,
    "epg_total_channels": None,
    "epg_coverage_ratio": None,
    "recording_sanity_ok": 1,
    "recording_fragment_count": 0,
    "recording_undersized_count": 0,
}

def push_loki_log(job, level, msg, extra_labels=None):
    labels = {"job": job, "host": "pve-01", "level": level}
    if extra_labels:
        labels.update(extra_labels)
    timestamp_ns = str(int(time.time() * 1e9))
    payload = {
        "streams": [
            {
                "stream": labels,
                "values": [[timestamp_ns, msg]]
            }
        ]
    }
    try:
        req = urllib.request.Request(
            LOKI_URL,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"}
        )
        urllib.request.urlopen(req, timeout=5)
    except Exception:
        pass

def check_recording_sanity():
    base_dir = "/srv/shared-recordings"
    if not os.path.exists(base_dir):
        return

    fragment_anomalies = []
    undersized_anomalies = []
    now = time.time()

    for root, dirs, files in os.walk(base_dir):
        video_files = [f for f in files if f.endswith(('.ts', '.mp4', '.mkv', '.avi'))]
        if not video_files:
            continue

        if len(video_files) > 1:
            episodes = {}
            for vf in video_files:
                base = vf
                for ext in ['.ts', '.mp4', '.mkv', '.avi']:
                    if base.endswith(ext):
                        base = base[:-len(ext)]
                clean_base = re.sub(r'[_\-]\d+$', '', base)
                episodes.setdefault(clean_base, []).append(vf)

            for clean_base, ep_files in episodes.items():
                if len(ep_files) > 1:
                    fragment_anomalies.append(f"Directory {root}: episode '{clean_base}' has {len(ep_files)} fragment files ({ep_files})")

        for vf in video_files:
            filepath = os.path.join(root, vf)
            try:
                st = os.stat(filepath)
                age_sec = now - st.st_mtime
                if age_sec > 300 and st.st_size < 150000000:
                    undersized_anomalies.append(f"File {filepath}: size is {st.st_size} bytes ({st.st_size/1e6:.1f}MB), far under scheduled duration")
            except Exception:
                pass

    metrics_data["recording_fragment_count"] = len(fragment_anomalies)
    metrics_data["recording_undersized_count"] = len(undersized_anomalies)

    if fragment_anomalies or undersized_anomalies:
        metrics_data["recording_sanity_ok"] = 0
        msg_parts = []
        if fragment_anomalies:
            msg_parts.append("event=recording_anomaly type=fragmented " + " | ".join(fragment_anomalies))
        if undersized_anomalies:
            msg_parts.append("event=recording_anomaly type=undersized " + " | ".join(undersized_anomalies))
        full_msg = " ; ".join(msg_parts)
        push_loki_log("recording-sanity", "error", full_msg, {"event": "recording_anomaly"})
    else:
        metrics_data["recording_sanity_ok"] = 1

def check_stack_health():
    endpoints = {
        "jellyfin-npvr": "http://192.168.9.219:8096/health",
        "nextpvr-live": "http://192.168.9.219:8866/",
    }
    for stack, url in endpoints.items():
        up = 0
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "StackMonitor/1.0"})
            with urllib.request.urlopen(req, timeout=5) as resp:
                if resp.status in [200, 301, 302]:
                    up = 1
        except urllib.error.HTTPError as e:
            if e.code in [200, 301, 302]:
                up = 1
        except Exception:
            up = 0
        metrics_data["stack_up"][stack] = up

def check_epg_freshness():
    epg_targets = {
        "jellyfin-npvr": (112, "/srv/jellyfin-npvr/nextpvr/config/epg.xml"),
        "media-core": (105, "/srv/media-core/epg/epg.xml"),
    }
    now = time.time()
    for stack, (vmid, path) in epg_targets.items():
        age_hours = 999.0
        try:
            cmd = ["pct", "exec", str(vmid), "--", "stat", "-c", "%Y", path]
            out = subprocess.check_output(cmd, timeout=5).decode().strip()
            mtime = float(out)
            age_hours = (now - mtime) / 3600.0
        except Exception:
            pass
        metrics_data["epg_age_hours"][stack] = round(age_hours, 2)
        if age_hours > 26.0:
            push_loki_log("epg-freshness", "warn", f"event=epg_stale stack={stack} age_hours={age_hours:.1f}", {"event": "epg_stale", "stack": stack})
        else:
            push_loki_log("epg-sync", "info", f"event=epg_sync_complete stack={stack} age_hours={age_hours:.1f}", {"event": "epg_sync_complete", "stack": stack})

def check_epg_coverage():
    now_ns = int(time.time() * 1e9)
    start_ns = now_ns - (7 * 86400 * 1e9)
    params = {
        "query": '{job="epg-sync"} |= "real="',
        "start": str(int(start_ns)),
        "end": str(int(now_ns)),
        "limit": "1",
        "direction": "BACKWARD",
    }
    url = f"http://192.168.9.164:3100/loki/api/v1/query_range?{urllib.parse.urlencode(params)}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "StackMonitor/1.0"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.load(resp)
        results = data.get("data", {}).get("result", [])
        if results and results[0].get("values"):
            _ts, line = results[0]["values"][0]
            m_real = re.search(r"\breal=(\d+)\b", line)
            m_total = re.search(r"\btotal_channels=(\d+)\b", line)
            if m_real and m_total:
                real = int(m_real.group(1))
                total = int(m_total.group(1))
                if total > 0:
                    metrics_data["epg_real_channels"] = real
                    metrics_data["epg_total_channels"] = total
                    metrics_data["epg_coverage_ratio"] = round(real / total, 4)
    except Exception:
        pass

class MetricsHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path in ["/metrics", "/"]:
            lines = []
            lines.append("# HELP stack_up Service status (1 = up, 0 = down)")
            lines.append("# TYPE stack_up gauge")
            for stack, val in metrics_data["stack_up"].items():
                lines.append(f'stack_up{{stack="{stack}"}} {val}')

            lines.append("# HELP epg_age_hours EPG guide data age in hours")
            lines.append("# TYPE epg_age_hours gauge")
            for stack, val in metrics_data["epg_age_hours"].items():
                lines.append(f'epg_age_hours{{stack="{stack}"}} {val}')

            if metrics_data["epg_real_channels"] is not None:
                lines.append("# HELP epg_real_channels Number of channels with real guide data")
                lines.append("# TYPE epg_real_channels gauge")
                lines.append(f'epg_real_channels {metrics_data["epg_real_channels"]}')

            if metrics_data["epg_total_channels"] is not None:
                lines.append("# HELP epg_total_channels Total number of channels in EPG lineup")
                lines.append("# TYPE epg_total_channels gauge")
                lines.append(f'epg_total_channels {metrics_data["epg_total_channels"]}')

            if metrics_data["epg_coverage_ratio"] is not None:
                lines.append("# HELP epg_coverage_ratio Ratio of real guide channels to total channels (real / total)")
                lines.append("# TYPE epg_coverage_ratio gauge")
                lines.append(f'epg_coverage_ratio {metrics_data["epg_coverage_ratio"]}')

            lines.append("# HELP recording_sanity_ok Recording sanity status (1 = ok, 0 = anomaly)")
            lines.append("# TYPE recording_sanity_ok gauge")
            lines.append(f'recording_sanity_ok {metrics_data["recording_sanity_ok"]}')
            lines.append(f'recording_fragment_count {metrics_data["recording_fragment_count"]}')
            lines.append(f'recording_undersized_count {metrics_data["recording_undersized_count"]}')

            content = "\n".join(lines) + "\n"
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; version=0.0.4")
            self.end_headers()
            self.wfile.write(content.encode("utf-8"))
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        pass

def run_loop():
    while True:
        try:
            check_stack_health()
            check_recording_sanity()
            check_epg_freshness()
            check_epg_coverage()
        except Exception:
            pass
        time.sleep(30)

if __name__ == "__main__":
    try:
        check_stack_health()
        check_recording_sanity()
        check_epg_freshness()
        check_epg_coverage()
    except Exception:
        pass
    t = threading.Thread(target=run_loop, daemon=True)
    t.start()
    server = HTTPServer(("0.0.0.0", 9105), MetricsHandler)
    server.serve_forever()
