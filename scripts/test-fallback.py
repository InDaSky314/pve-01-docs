#!/usr/bin/env python3
"""Decision-matrix test for check_ppv_fallback_cancellation.

Loads the real deployed module, stubs only its I/O boundary, and asserts
the cancel decision for every combination that matters. The fallback is the
last line of defence for a match the owner explicitly asked to be
guaranteed -- a false cancel loses the game, so each guard is tested alone.
"""
import importlib.util, importlib.machinery, sys, types
from datetime import datetime, timedelta, timezone

spec = importlib.util.spec_from_loader(
    "sda", importlib.machinery.SourceFileLoader("sda", "sports-dvr-auto"))
sda = importlib.util.module_from_spec(spec)
sys.modules["sda"] = sda
spec.loader.exec_module(sda)

NOW = datetime.now(timezone.utc)
FB_START = (NOW - timedelta(minutes=10)).strftime("%Y-%m-%dT%H:%M:%SZ")
FB_END = (NOW + timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
PPV_CID, LINEAR_CID, OTHER_CID = "ppv-cid-1", "linear-cid-9", "unrelated-cid-3"

FALLBACK = {"Id": "fb1", "Name": f"Bayern Munich: VfB Stuttgart at Bayern Munich {sda.PPV_FALLBACK_MARKER}",
            "Status": "New", "ChannelId": LINEAR_CID, "StartDate": FB_START, "EndDate": FB_END}


def english(status="InProgress", cid=PPV_CID, start=FB_START, end=FB_END):
    return {"Id": "en1", "Name": "Bayern Munich: VfB Stuttgart at Bayern Munich",
            "Status": status, "ChannelId": cid, "StartDate": start, "EndDate": end,
            "RecordingPath": "/media/recordings/x.ts"}


def run(timers, size):
    cancelled = []
    sda.get_existing_timers = lambda: timers
    sda.get_jellyfin_channels = lambda: {"DAZN PPV 07": PPV_CID,
                                         "Sky Sport Bundesliga 1 HD (720P)": LINEAR_CID}
    sda.recording_file_size = lambda p: size
    sda.resolve_active_recording_path = lambda n: "/media/recordings/x.ts"
    sda.log_event = lambda *a, **k: None
    sda.notify = lambda *a, **k: None
    sda.cancel_live_timer = lambda tid, dry_run=True: (cancelled.append(tid), (True, "ok"))[1]
    sda.check_ppv_fallback_cancellation(dry_run=True)
    return cancelled

BIG, SMALL = 500_000_000, 1_000_000
cases = [
    ("English recording, plenty of bytes -> CANCEL", [FALLBACK, english()], BIG, True),
    ("English InProgress but barely any bytes -> keep", [FALLBACK, english()], SMALL, False),
    ("English file missing entirely -> keep", [FALLBACK, english()], None, False),
    ("English only Scheduled, not started -> keep", [FALLBACK, english(status="New")], BIG, False),
    ("Sibling on a NON-PPV channel -> keep", [FALLBACK, english(cid=OTHER_CID)], BIG, False),
    ("Sibling window does not overlap -> keep", [FALLBACK, english(
        start=(NOW + timedelta(days=2)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        end=(NOW + timedelta(days=2, hours=2)).strftime("%Y-%m-%dT%H:%M:%SZ"))], BIG, False),
    ("No English timer at all -> keep", [FALLBACK], BIG, False),
    ("Two fallbacks, one match -> cancel only the matching one",
     [FALLBACK, {**FALLBACK, "Id": "fb2", "Name": f"Bayern Munich: Other Match {sda.PPV_FALLBACK_MARKER}",
                 "StartDate": (NOW + timedelta(days=3)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                 "EndDate": (NOW + timedelta(days=3, hours=2)).strftime("%Y-%m-%dT%H:%M:%SZ")},
      english()], BIG, True),
]

import logging
logging.disable(logging.CRITICAL)
ok = True
for desc, timers, size, want_cancel in cases:
    got = run(timers, size)
    passed = (got == ["fb1"]) if want_cancel else (got == [])
    ok &= passed
    print(f"{'PASS' if passed else 'FAIL'}  {desc:52s} cancelled={got}")
print("\nALL PASS" if ok else "\nFAILURES PRESENT")
sys.exit(0 if ok else 1)
