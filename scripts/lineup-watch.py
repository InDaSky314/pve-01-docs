#!/usr/bin/env python3
"""Weekly report of channels the provider added or dropped.

The lineup in config.json is an explicit allowlist: each live_selection carries
an `ids` map of provider stream_id -> display name. Anything the provider
carries that is not in that map is a channel we are not showing -- which is
usually correct (the provider carries thousands) but is also where a genuinely
new channel first appears.

Reports three things:

  NEW      streams the provider now carries that are not in any selection, and
           that arrived since the last run
  DROPPED  stream ids we DO have in the lineup that the provider no longer
           carries -- these are dead channels in the guide right now
  RENAMED  stream ids in the lineup whose provider-side name changed since the
           previous run (a provider relabel, which often precedes a swap)

DROPPED matters more than NEW: a dead entry is a channel that fails when
selected, whereas an unlisted new channel is merely absent.

State lives in lineup-watch-state.json so each run reports the delta rather
than the same several-thousand-line list every week.
"""
import json
import os
import re
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

BASE_DIR = Path("/srv/media-core")
ENV_FILE = BASE_DIR / ".env"
CONFIG = BASE_DIR / "sync" / "config.json"
STATE = BASE_DIR / "sync" / "lineup-watch-state.json"
MAILTO = "nathan.karras@gmail.com"

# Numbered event slots and pool feeds churn constantly by design; reporting
# them every week would bury the real additions.
NOISE = re.compile(r"\b(PPV|EVENT|SLOT)\b|\b\d{2,3}\s*$", re.I)

# Pasted into every report so the next step is one copy-paste away rather than
# something to reconstruct from memory weeks later.
ACTION_PROMPT = """TO ACT ON THIS, paste the block below to Claude Code, filling in the channels
you want. Nothing in this mail changes anything on its own.

    Media-Core lineup change. Read /root/pve-01-docs/docs/lessons-learned.md
    and docs/channel-icons-runbook.md first.

    ADD these channels to the lineup (stream id -> where it belongs):
      [id] <name>  ->  group <group name>
      [id] <name>  ->  group <group name>

    REMOVE these dead entries:
      [id] <name>

    For each added channel:
      1. Put it in the right live_selections group in
         /srv/media-core/sync/config.json on CT 105, using the group's
         start_chno block. Check the block has room -- the sync warns on
         overflow and 'German Cable & Entertainment' already overflows.
      2. Let the 12:01 sync and 12:25 renumber run, or run them by hand.
      3. Source a logo: scripts/icon-match.py against tv-logos first.
         If there is no match, generate one via agy in batches of 6 --
         12 produced cropped text and invented words.
      4. Install per channel-icons-runbook.md: composite onto a
         luminance-matched opaque backdrop, write to NextPVR's channel dir
         with : / | stripped from the filename, then clear ALL channel rows
         in Jellyfin's BaseItemImageInfos and run ONE guide refresh to
         completion. Clearing a subset does not re-fetch.
      5. Verify at the layer I see: pull the rendered image from Jellyfin
         and compare byte counts to the source file. Do not stop at the
         NextPVR API -- it was green while the guide showed nothing.

    This touches production (CT 105 config.json drives the whole estate),
    so confirm the plan with me before applying."""


def env():
    out = {}
    for line in ENV_FILE.read_text().splitlines():
        if "=" in line and not line.strip().startswith("#"):
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip().strip('"').strip("'")
    return out


# The panel 403s a request without this, and xtream-sync.py already uses it.
UA = "MediaCoreSync/1.0"


def api(base, user, pw, action):
    """Mirrors xtream-sync.py's helper deliberately.

    The panel intermittently answers 200 with an empty list. Treating that as a
    real answer would report the entire lineup as DROPPED on a bad night, so it
    is retried like a transport error -- the same guard the sync already has.
    """
    url = f"{base}/player_api.php?username={user}&password={pw}&action={action}"
    for attempt in range(1, 4):
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=120) as r:
            data = json.load(r)
        if data != []:
            return data
        if attempt < 3:
            time.sleep(20 * attempt)
    return []


def main():
    e = env()
    base, user, pw = e["XTREAM_BASE"], e["XTREAM_USER"], e["XTREAM_PASS"]
    cfg = json.loads(CONFIG.read_text())

    # `ids` comes in three shapes: a dict of id->name, a bare list of ids, or
    # absent entirely for selections that take a whole provider category by
    # regex. The last kind matters: without handling it, every channel in
    # "Soccer PPV" or the German category selections would be reported NEW
    # every week, and a report that is mostly false positives gets ignored.
    in_lineup = {}
    cat_rules = []
    for sel in cfg["live_selections"]:
        ids = sel.get("ids")
        grp = sel.get("group", "?")
        if isinstance(ids, dict):
            for sid, name in ids.items():
                in_lineup[str(sid)] = (name, grp)
        elif isinstance(ids, list):
            # No stored display name for these, so rename detection has to skip
            # them -- None marks that, rather than a placeholder string that
            # would mismatch the provider name and report all 726 as renamed.
            for sid in ids:
                in_lineup[str(sid)] = (None, grp)
        elif sel.get("category"):
            cat_rules.append((re.compile(sel["category"]),
                              re.compile(sel["name"]) if sel.get("name") else None,
                              re.compile(sel["name_exclude"]) if sel.get("name_exclude") else None,
                              grp))

    try:
        streams = api(base, user, pw, "get_live_streams")
    except Exception as exc:                                  # noqa: BLE001
        print(f"provider query failed: {exc}", file=sys.stderr)
        return 1
    if not streams:
        print("provider returned zero streams -- treating as a failed query, "
              "not an empty lineup", file=sys.stderr)
        return 1

    provider = {str(s["stream_id"]): s.get("name", "").strip() for s in streams}
    cat_of = {str(s["stream_id"]): str(s.get("category_id", "")) for s in streams}
    cat_names = {str(c["category_id"]): c.get("category_name", "")
                 for c in api(base, user, pw, "get_live_categories")}

    def covered_by_category(sid):
        """True if a category-based selection would already pick this up."""
        cname = cat_names.get(cat_of.get(sid, ""), "")
        nm = provider[sid]
        for crx, nrx, nxrx, _grp in cat_rules:
            if not crx.search(cname):
                continue
            if nxrx and nxrx.search(nm):
                continue
            if nrx and not nrx.search(nm):
                continue
            return True
        return False
    print(f"provider carries {len(provider)} live streams; "
          f"lineup uses {len(in_lineup)}")

    prev = {}
    if STATE.exists():
        prev = json.loads(STATE.read_text()).get("provider", {})

    unlisted = {k: v for k, v in provider.items()
                if k not in in_lineup and not covered_by_category(k)}
    # only channels that appeared since last run, and not obvious event churn
    new = {k: v for k, v in unlisted.items()
           if k not in prev and not NOISE.search(v)}
    dropped = {k: in_lineup[k] for k in in_lineup if k not in provider}
    # Compare the provider's name now against the provider's name last week --
    # NOT against our display name. We deliberately rename channels ("Chicago:
    # ABC 7 (WLS)" vs the provider's "US: ABC 7 (WLS) CHICAGO HD"), so the
    # latter comparison flagged 39 channels as renamed every run when nothing
    # had changed.
    renamed = {k: (prev[k], provider[k]) for k in in_lineup
               if k in provider and k in prev and provider[k]
               and prev[k] != provider[k]}

    # VOD: informational only. New films flow into the library automatically
    # via the category rules, so there is no decision to make -- the owner just
    # wants to know what arrived. Reported as a count plus a sample, because
    # the full list runs to thousands and would bury the channel section.
    vod_new, vod_total = [], 0
    try:
        vod = api(base, user, pw, "get_vod_streams")
        vod_now = {str(v["stream_id"]): (v.get("name") or "").strip() for v in vod}
        vod_total = len(vod_now)
        prev_vod = json.loads(STATE.read_text()).get("vod", {}) if STATE.exists() else {}
        if prev_vod:
            vod_new = sorted(nm for sid, nm in vod_now.items() if sid not in prev_vod)
    except Exception as exc:                                  # noqa: BLE001
        print(f"VOD query failed: {exc}", file=sys.stderr)
        vod_now = json.loads(STATE.read_text()).get("vod", {}) if STATE.exists() else {}

    STATE.write_text(json.dumps({"provider": provider, "vod": vod_now}, indent=1))

    if not (new or dropped or renamed or vod_new):
        print("no lineup changes")
        return 0

    lines = [f"Provider carries {len(provider)} live streams; "
             f"{len(in_lineup)} are in the lineup.", ""]
    if dropped:
        lines += [f"DROPPED -- in our lineup, provider no longer carries "
                  f"({len(dropped)}). These are dead entries in the guide:", ""]
        lines += [f"  [{sid}] {nm or '(no stored name)'}   (group: {grp})"
                  for sid, (nm, grp) in sorted(dropped.items(),
                                               key=lambda x: (x[1][0] or ""))]
        lines.append("")
    if renamed:
        lines += [f"RENAMED provider-side ({len(renamed)}):", ""]
        lines += [f"  [{sid}] {ours}  ->  {theirs}"
                  for sid, (ours, theirs) in sorted(renamed.items(), key=lambda x: x[1][0])]
        lines.append("")
    if new:
        lines += [f"NEW since last week, not in the lineup ({len(new)}):", ""]
        lines += [f"  [{sid}] {nm}" for sid, nm in sorted(new.items(), key=lambda x: x[1])]
        lines.append("")

    if vod_new:
        lines += ["", f"NEW MOVIES ({len(vod_new)} of {vod_total} in the VOD "
                  f"catalogue). These flow into the library automatically -- "
                  f"nothing to decide:", ""]
        lines += [f"  {t}" for t in vod_new[:40]]
        if len(vod_new) > 40:
            lines.append(f"  ... and {len(vod_new) - 40} more")

    lines += ["", "-" * 68, "", ACTION_PROMPT]

    # Report on stdout only. `mail` is not installed in CT 105, and the
    # provider API is reachable from CT 105's Swiss egress but not necessarily
    # the host's US one -- so the query stays here and the host wrapper does
    # the sending. Exit 2 means "there is something to send".
    print(f"SUBJECT: IPTV lineup: {len(new)} new, {len(dropped)} dropped, "
          f"{len(renamed)} renamed, {len(vod_new)} new movies")
    print("\n".join(lines))
    return 2


if __name__ == "__main__":
    sys.exit(main())
