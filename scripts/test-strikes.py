#!/usr/bin/env python3
"""Prove the strike counter: a single stalled sample must NOT restore, two
consecutive must, and a healthy sample in between must reset."""
import importlib.util, importlib.machinery, sys, logging, json
from datetime import datetime, timedelta, timezone

spec = importlib.util.spec_from_loader(
    "sda", importlib.machinery.SourceFileLoader("sda", "/usr/local/bin/sports-dvr-auto"))
sda = importlib.util.module_from_spec(spec); sys.modules["sda"] = sda
spec.loader.exec_module(sda)
logging.disable(logging.CRITICAL)

NOW = datetime.now(timezone.utc)
TIMER = {"Id": "t1", "Name": "Packers: Test Game", "Status": "InProgress",
         "ChannelId": "c1", "RecordingPath": "/media/recordings/t.ts",
         "StartDate": (NOW - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ"),
         "EndDate": (NOW + timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%SZ")}

state_store = {}
restores = []

def setup(size_seq):
    """size_seq: list of sizes returned on successive recording_file_size calls."""
    seq = list(size_seq)
    sda.get_existing_timers = lambda: [TIMER]
    sda.read_live_timers = lambda: [TIMER]
    sda.recording_file_size = lambda p: seq.pop(0) if seq else seq
    sda.resolve_active_recording_path = lambda n: "/media/recordings/t.ts"
    sda.load_stall_state = lambda: json.loads(json.dumps(state_store))
    def save(s): state_store.clear(); state_store.update(s)
    sda.save_stall_state = save
    sda.load_restore_state = lambda: {}
    sda.save_restore_state = lambda s: None
    sda.log_event = lambda *a, **k: None
    sda.notify = lambda *a, **k: None
    sda.check_concurrent_stream = lambda cid: "ok"
    sda.trigger_auto_restore = lambda t, dry_run=True: (
        restores.append(t["Id"]), (True, "restored", "r1", True))[1]

BIG = 5_000_000_000
# Sample 1 establishes the baseline (no prior state -> never acts)
setup([1_000_000_000]); sda.check_stalled_recordings(dry_run=False)
print(f"after baseline sample: strikes={state_store.get('t1',{}).get('strikes')} restores={restores}")

# Sample 2: no growth -> strike 1, must NOT restore
state_store["t1"]["checked_at"] = (NOW - timedelta(seconds=200)).isoformat()
setup([1_000_000_000]); sda.check_stalled_recordings(dry_run=False)
s1 = state_store.get("t1", {}).get("strikes")
print(f"after 1st stalled sample: strikes={s1} restores={restores}  -> {'PASS' if s1==1 and not restores else 'FAIL'}")

# Sample 3: healthy growth -> strikes reset to 0
state_store["t1"]["checked_at"] = (NOW - timedelta(seconds=200)).isoformat()
setup([1_100_000_000]); sda.check_stalled_recordings(dry_run=False)
s2 = state_store.get("t1", {}).get("strikes")
print(f"after healthy sample:     strikes={s2} restores={restores}  -> {'PASS' if s2==0 and not restores else 'FAIL'}")

# Samples 4+5: two consecutive stalls -> restore fires
ok = True
for i in (1, 2):
    state_store["t1"]["checked_at"] = (NOW - timedelta(seconds=200)).isoformat()
    # second element is the confirm re-probe right before restoring
    setup([1_100_000_000, 1_100_000_000]); sda.check_stalled_recordings(dry_run=False)
    print(f"  stalled sample {i}: strikes={state_store.get('t1',{}).get('strikes')} restores={restores}")
    if i == 1 and restores: ok = False
print(f"two consecutive stalls -> restore: {'PASS' if restores == ['t1'] and ok else 'FAIL'} ({restores})")
