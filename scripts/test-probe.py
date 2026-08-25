#!/usr/bin/env python3
"""Simulate the full pregame probe/commit sequence against the real module.

This logic cancels the guaranteed German fallback, so the paths that matter
most are the ones where something goes wrong: the English timer failing to
create, and the probe producing no video.
"""
import importlib.util, importlib.machinery, sys, logging, tempfile, os
from datetime import datetime, timedelta, timezone
from pathlib import Path

spec = importlib.util.spec_from_loader(
    "sda", importlib.machinery.SourceFileLoader("sda", "sports-dvr-auto"))
sda = importlib.util.module_from_spec(spec); sys.modules["sda"] = sda
spec.loader.exec_module(sda)
logging.basicConfig(level=logging.INFO, format="      %(message)s")

NOW = datetime.now(timezone.utc)
GAME = {"id": "G1", "team": "Bayern Munich", "name": "VfB Stuttgart at Bayern Munich",
        "kind": "soccer", "start": NOW + timedelta(minutes=30),
        "end": NOW + timedelta(minutes=180), "broadcasts": [],
        "state": "pre", "completed": False, "status_detail": ""}
BASE = "Bayern Munich: VfB Stuttgart at Bayern Munich"
FB = f"{BASE} {sda.PPV_FALLBACK_MARKER}"


CHANS = {"DAZN PPV 07": "ppvcid", "Sky Sport Bundesliga 1 HD (720P)": "linear"}


def scenario(name, *, resolve_ok=True, create_ok=True, probe_bytes=None,
             elapsed_min=5.0, start_phase=None):
    print(f"\n=== {name} ===")
    tf = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
    tf.close(); os.unlink(tf.name)
    sda.PPV_PROBE_STATE_FILE = Path(tf.name)
    timers = [{"Id": "fb1", "Name": FB, "Status": "New", "ChannelId": "linear",
               "StartDate": (NOW + timedelta(minutes=25)).strftime("%Y-%m-%dT%H:%M:%SZ"),
               "EndDate": (NOW + timedelta(minutes=180)).strftime("%Y-%m-%dT%H:%M:%SZ")}]
    events, cancels, created = [], [], []

    if start_phase == "probing":
        timers.append({"Id": "en1", "Name": BASE, "Status": "InProgress",
                       "ChannelId": "ppvcid", "RecordingPath": "/media/recordings/e.ts",
                       "StartDate": NOW.strftime("%Y-%m-%dT%H:%M:%SZ"),
                       "EndDate": (NOW + timedelta(minutes=180)).strftime("%Y-%m-%dT%H:%M:%SZ")})
        timers = [t for t in timers if t["Id"] != "fb1"]   # already released
        sda._save_probe_state({"G1": {"phase": "probing", "english_timer": "en1",
                                      "channel": "ppvcid",
                                      "started_at": (NOW - timedelta(minutes=elapsed_min)).isoformat()}})

    def sched(g, cid, cname, dry_run=True, name_suffix=""):
        if not create_ok:
            return False
        nm = f"{BASE} {name_suffix}".strip() if name_suffix else BASE
        created.append(nm)
        timers.append({"Id": "en1" if not name_suffix else "fb2", "Name": nm,
                       "Status": "InProgress", "ChannelId": cid,
                       "RecordingPath": "/media/recordings/e.ts",
                       "StartDate": NOW.strftime("%Y-%m-%dT%H:%M:%SZ"),
                       "EndDate": (NOW + timedelta(minutes=180)).strftime("%Y-%m-%dT%H:%M:%SZ")})
        return True

    sda.get_existing_timers = lambda: list(timers)
    sda.get_jellyfin_channels = lambda: dict(CHANS)
    sda.resolve_dynamic_ppv_channel = lambda t, g, j: (
        ("ppvcid", "DAZN PPV 07") if resolve_ok else (None, "nothing labeled yet"))
    sda.schedule_game_timer = sched
    sda.recording_file_size = lambda p: probe_bytes
    sda.resolve_active_recording_path = lambda n: "/media/recordings/e.ts"
    sda.cancel_live_timer = lambda tid, dry_run=True: (
        cancels.append(tid), timers.__setitem__(slice(None),
            [t for t in timers if t["Id"] != tid]), (True, "ok"))[2]
    sda.log_event = lambda ev, *a, **k: events.append(ev)
    sda.notify = lambda *a, **k: None

    sda.run_ppv_probe_commit(dry_run=False, games=[GAME])
    st = sda._load_probe_state().get("G1", {})
    names = sorted(t["Name"] for t in timers)
    print(f"  phase={st.get('phase')} cancels={cancels} events={events}")
    print(f"  timers left: {names}")
    os.path.exists(tf.name) and os.unlink(tf.name)
    return st.get("phase"), cancels, [t["Name"] for t in timers]


# 1. No slot labeled yet -> must change nothing at all
ph, c, t = scenario("No English slot labeled yet", resolve_ok=False)
assert ph is None and c == [] and FB in t, "must not touch the fallback"
print("  PASS: fallback untouched")

# 2. Slot found but the English timer fails to create -> fallback MUST survive
ph, c, t = scenario("English timer creation FAILS", create_ok=False)
assert c == [] and FB in t, "fallback must survive a failed create"
print("  PASS: create-before-cancel held the line")

# 3. Happy path: probe starts, fallback released
ph, c, t = scenario("Probe starts cleanly")
assert ph == "probing" and c == ["fb1"] and BASE in t
print("  PASS: English recording live, tuner released")

# 4. Probe proves the channel -> commit, fallback stays gone
ph, c, t = scenario("Probe PROVES the channel", start_phase="probing",
                    probe_bytes=500_000_000)
assert ph == "committed" and FB not in t
print("  PASS: committed to English")

# 5. Probe produces nothing -> revert to German before kickoff
ph, c, t = scenario("Probe produces NO video", start_phase="probing",
                    probe_bytes=0, elapsed_min=5.0)
assert ph == "reverted" and "en1" in c and any(sda.PPV_FALLBACK_MARKER in x for x in t), t
print("  PASS: reverted to German feed with time to spare")

# 6. Probe quiet but still inside its grace period -> keep waiting, no change
ph, c, t = scenario("Probe quiet but still within grace", start_phase="probing",
                    probe_bytes=0, elapsed_min=1.0)
assert ph == "probing" and c == []
print("  PASS: waited instead of thrashing")

# 7. Probe fails AND the German channel is gone -> must KEEP English, not strand us
CHANS.pop("Sky Sport Bundesliga 1 HD (720P)")
ph, c, t = scenario("Probe fails AND fallback channel missing", start_phase="probing",
                    probe_bytes=0, elapsed_min=5.0)
assert ph == "probing" and c == [] and BASE in t, (ph, c, t)
print("  PASS: kept the English timer rather than recording nothing")
CHANS["Sky Sport Bundesliga 1 HD (720P)"] = "linear"

print("\nALL SCENARIOS PASS")
