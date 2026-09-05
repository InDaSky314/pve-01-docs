#!/usr/bin/env python3
"""Prove early-complete recovery for Jellyfin DVR recordings in sports-dvr-auto.

Tests both directions:
1. Timer completing far short of window WITH fixture live -> triggers continuation.
2. Timer completing short because fixture is FINAL (ESPN / OpenLigaDB) -> does NOT restart.
3. Normal full-length recording -> completely unaffected.
4. Single-tuner protection:
   4a. Overlapping another Jellyfin timer -> continuation refused.
   4b. Overlapping an MCT booking -> continuation refused.
   4c. Free tuner -> continuation allowed.
5. Multi-segment stitching and comskip queuing:
   Segments stitch via perform_ffmpeg_concat -> archived -> stitched file enqueued to comskip.
6. Bounded retries and backoff floor:
   Floor prevents thrashing; max attempts ceiling prevents looping.
"""
from __future__ import annotations

import importlib.machinery
import importlib.util
import json
import logging
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Load sports-dvr-auto module
spec = importlib.util.spec_from_loader(
    "sda", importlib.machinery.SourceFileLoader("sda", "/root/pve-01-docs/scripts/sports-dvr-auto")
)
sda = importlib.util.module_from_spec(spec)
sys.modules["sda"] = sda
spec.loader.exec_module(sda)

logging.disable(logging.CRITICAL)


def test_fixture_live_triggers_continuation():
    print("\n--- Test 1A: Early-complete with LIVE fixture triggers continuation ---")
    now = datetime.now(timezone.utc)
    t1_end = now + timedelta(hours=2)
    post_padding = 3600
    scheduled_end = t1_end + timedelta(seconds=post_padding)

    # Simulated timer: completed 2 hours early
    timer_live = {
        "Id": "timer-live-1",
        "Name": "Fußball: Bundesliga",
        "EpisodeTitle": "FC Schalke 04 – FC Bayern München, 2. Spieltag",
        "ChannelId": "hdhr_1011",
        "ChannelName": "Sky Sport Bundesliga 1 HD",
        "StartDate": (now - timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "EndDate": t1_end.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "PostPaddingSeconds": post_padding,
        "Status": "Completed",
        "RecordingPath": "/media/recordings/Sports/Bundesliga/seg1.ts",
    }

    # OpenLigaDB match is NOT finished
    mock_openliga = [{
        "matchID": 83172,
        "matchDateTimeUTC": (now - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "team1": {"teamName": "FC Schalke 04"},
        "team2": {"teamName": "FC Bayern München"},
        "matchIsFinished": False,
    }]

    restore_state_store = {}
    created_timers = []

    sda.load_restore_state = lambda: dict(restore_state_store)
    sda.save_restore_state = lambda s: restore_state_store.update(s)
    sda.fetch_openligadb_matches = lambda now=None: mock_openliga
    sda.fetch_espn_schedule = lambda: []
    sda.file_exists_on_ct105 = lambda p: True
    sda.notify = lambda *a, **k: None
    sda.log_event = lambda *a, **k: None

    def mock_restore(timer, dry_run=True):
        created_timers.append(timer)
        return True, "continuation created", "restore-timer-2", True

    sda.trigger_auto_restore = mock_restore

    sda.check_early_completed_recordings(
        dry_run=False,
        api_timers=[],
        disk_timers=[timer_live],
    )

    assert len(created_timers) == 1, f"Expected 1 restore triggered, got {len(created_timers)}"
    assert "timer-live-1" in restore_state_store, "Timer not recorded in restore_state"
    entry = restore_state_store["timer-live-1"]
    assert entry["restore_timer_id"] == "restore-timer-2", f"Wrong restore_timer_id: {entry}"
    assert entry["status"] == "restoring", f"Wrong status: {entry}"
    assert entry["original_end_date"] == scheduled_end.strftime("%Y-%m-%dT%H:%M:%SZ"), (
        f"Continuation must preserve full scheduled end + post padding (expected {scheduled_end.isoformat()}, got {entry['original_end_date']})"
    )
    print("PASS: Live Bundesliga fixture triggered continuation for full remaining window (including 3600s padding)")


def test_fixture_final_does_not_restart():
    print("\n--- Test 1B: Early-complete with FINAL fixture does NOT restart ---")
    now = datetime.now(timezone.utc)
    t1_end = now + timedelta(hours=2)

    # Same timer, but OpenLigaDB reports matchIsFinished: True
    timer_final = {
        "Id": "timer-final-1",
        "Name": "Fußball: Bundesliga",
        "EpisodeTitle": "FC Schalke 04 – FC Bayern München, 2. Spieltag",
        "ChannelId": "hdhr_1011",
        "ChannelName": "Sky Sport Bundesliga 1 HD",
        "StartDate": (now - timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "EndDate": t1_end.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "PostPaddingSeconds": 3600,
        "Status": "Completed",
        "RecordingPath": "/media/recordings/Sports/Bundesliga/seg1.ts",
    }

    mock_openliga = [{
        "matchID": 83172,
        "matchDateTimeUTC": (now - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "team1": {"teamName": "FC Schalke 04"},
        "team2": {"teamName": "FC Bayern München"},
        "matchIsFinished": True,  # FINAL!
    }]

    restore_state_store = {}
    created_timers = []

    sda.load_restore_state = lambda: dict(restore_state_store)
    sda.save_restore_state = lambda s: restore_state_store.update(s)
    sda.fetch_openligadb_matches = lambda now=None: mock_openliga
    sda.fetch_espn_schedule = lambda: []
    sda.trigger_auto_restore = lambda timer, dry_run=True: created_timers.append(timer)

    sda.check_early_completed_recordings(
        dry_run=False,
        api_timers=[],
        disk_timers=[timer_final],
    )

    assert len(created_timers) == 0, f"Expected 0 restores for final match, got {len(created_timers)}"
    assert "timer-final-1" not in restore_state_store
    print("PASS: Final Bundesliga match correctly suppressed continuation (feed died vs game over distinction)")

    # Also prove for ESPN game
    timer_espn = {
        "Id": "timer-espn-1",
        "Name": "Brewers: MIL @ CHC",
        "ChannelId": "hdhr_121",
        "StartDate": (now - timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "EndDate": t1_end.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "PostPaddingSeconds": 1800,
        "Status": "Completed",
    }
    mock_espn = [{
        "id": "espn-1",
        "team": "Brewers",
        "name": "Milwaukee Brewers at Chicago Cubs",
        "start": now - timedelta(hours=2),
        "end": now - timedelta(minutes=10),
        "state": "post",
        "completed": True,
        "broadcasts": [],
    }]
    sda.fetch_espn_schedule = lambda: mock_espn
    sda.check_early_completed_recordings(
        dry_run=False,
        api_timers=[],
        disk_timers=[timer_espn],
    )
    assert len(created_timers) == 0, "Expected 0 restores for completed ESPN game"
    print("PASS: Final ESPN game correctly suppressed continuation")


def test_normal_full_length_unaffected():
    print("\n--- Test 2: Normal full-length recording completely unaffected ---")
    now = datetime.now(timezone.utc)

    # Timer that ran to its full scheduled end + post padding
    t_normal = {
        "Id": "timer-normal-1",
        "Name": "Packers vs Bears",
        "ChannelId": "hdhr_106",
        "StartDate": (now - timedelta(hours=4)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "EndDate": (now - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "PostPaddingSeconds": 3600,  # Scheduled end was now
        "Status": "Completed",
        "RecordingPath": "/media/recordings/Sports/Packers.ts",
    }

    created_timers = []
    sda.trigger_auto_restore = lambda timer, dry_run=True: created_timers.append(timer)
    sda.load_restore_state = lambda: {}

    sda.check_early_completed_recordings(
        dry_run=False,
        api_timers=[],
        disk_timers=[t_normal],
    )
    assert len(created_timers) == 0, "Normal full-length recording must never be restored"
    print("PASS: Normal full-length recording was completely unaffected")


def test_single_tuner_conflict_protection():
    print("\n--- Test 3: Single tuner conflict protection (Jellyfin timers & MCT bookings) ---")
    now = datetime.now(timezone.utc)
    t1_end = now + timedelta(hours=2)

    timer_candidate = {
        "Id": "timer-cand-1",
        "Name": "Brewers: MIL @ CHC",
        "ChannelId": "hdhr_121",
        "StartDate": (now - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "EndDate": t1_end.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "PostPaddingSeconds": 1800,
        "Status": "Completed",
        "RecordingPath": "/media/recordings/Sports/Brewers.ts",
    }

    sda.fetch_openligadb_matches = lambda now=None: []
    sda.fetch_espn_schedule = lambda: [{
        "id": "espn-1",
        "team": "Brewers",
        "name": "Milwaukee Brewers at Chicago Cubs",
        "start": now - timedelta(hours=1),
        "end": now + timedelta(hours=2),
        "state": "in",
        "completed": False,
        "broadcasts": [],
    }]

    created_timers = []
    sda.trigger_auto_restore = lambda timer, dry_run=True: (created_timers.append(timer), (True, "ok", "r-id", True))[1]

    # 3A: Conflict with another Jellyfin timer
    other_jf_timer = {
        "Id": "timer-other-2",
        "Name": "Tonight Movie",
        "ChannelId": "hdhr_104",
        "StartDate": (now + timedelta(minutes=30)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "EndDate": (now + timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "Status": "New",
        "PrePaddingSeconds": 0,
        "PostPaddingSeconds": 0,
    }

    sda.check_early_completed_recordings(
        dry_run=False,
        api_timers=[other_jf_timer],
        disk_timers=[timer_candidate, other_jf_timer],
    )
    assert len(created_timers) == 0, f"Continuation must be refused when overlapping another Jellyfin timer! Got: {created_timers}"
    print("PASS: 3A - Overlapping Jellyfin timer correctly blocked continuation")

    # 3B: Conflict with an MCT booking
    mct_booking = [{
        "id": "mct-test-1",
        "title": "MCT Direct Capture",
        "status": "booked",
        "start": (now + timedelta(minutes=20)).isoformat(),
        "duration_sec": 3600,
    }]
    mct_file = Path("/var/lib/dvr-dashboard/mct-bookings.json")
    mct_orig = mct_file.read_text() if mct_file.exists() else "[]"
    try:
        mct_file.write_text(json.dumps(mct_booking))
        sda.check_early_completed_recordings(
            dry_run=False,
            api_timers=[],
            disk_timers=[timer_candidate],
        )
        assert len(created_timers) == 0, "Continuation must be refused when overlapping an MCT booking!"
        print("PASS: 3B - Overlapping MCT booking correctly blocked continuation")

        # 3C: No conflict -> Continuation is created!
        mct_file.write_text(json.dumps([]))
        sda.check_early_completed_recordings(
            dry_run=False,
            api_timers=[],
            disk_timers=[timer_candidate],
        )
        assert len(created_timers) == 1, "Continuation must be created when tuner is free"
        print("PASS: 3C - Free tuner allows continuation creation")
    finally:
        mct_file.write_text(mct_orig)


def test_stitching_and_comskip():
    print("\n--- Test 4: Segments stitch and STITCHED file goes to comskip ---")
    now = datetime.now(timezone.utc)
    root_tid = "t-root-1"
    restore_tid = "t-restore-2"

    seg1_path = "/media/recordings/Sports/Bayern Munich/segment1.ts"
    seg2_path = "/media/recordings/Sports/Bayern Munich/segment2.ts"
    stitched_target = "/media/recordings/Sports/Bayern Munich/segment1 (stitched).mkv"

    restore_state = {
        root_tid: {
            "original_timer_id": root_tid,
            "original_file_path": seg1_path,
            "original_name": "Fußball: Bundesliga",
            "original_end_date": (now - timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "channel_id": "hdhr_1011",
            "restore_timer_id": restore_tid,
            "status": "restoring",
            "restored_at": (now - timedelta(hours=1)).isoformat(),
        }
    }

    timers_completed = [
        {"Id": root_tid, "Name": "Fußball: Bundesliga", "Status": "Completed", "RecordingPath": seg1_path, "StartDate": (now - timedelta(hours=2)).isoformat()},
        {"Id": restore_tid, "Name": "Fußball: Bundesliga (restored)", "Status": "Completed", "RecordingPath": seg2_path, "StartDate": (now - timedelta(hours=1)).isoformat()},
    ]

    concat_calls = []
    archive_calls = []
    comskip_calls = []

    sda.load_restore_state = lambda: dict(restore_state)
    def save_rs(s): restore_state.clear(); restore_state.update(s)
    sda.save_restore_state = save_rs
    sda.read_live_timers = lambda: timers_completed
    sda.file_exists_on_ct105 = lambda p: True
    sda.recording_file_mtime = lambda p: int(now.timestamp()) - (3600 if "segment1" in p else 0)

    sda.perform_ffmpeg_concat = lambda paths, out: (concat_calls.append((paths, out)), (True, ""))[1]
    sda.archive_segments = lambda paths, orig: (archive_calls.append(paths), (paths, []))[1]
    sda.enqueue_for_comskip = lambda out: (comskip_calls.append(out), True)[1]
    sda.jf_request = lambda path, method="GET", data=None: {"status": "ok"}
    sda.notify = lambda *a, **k: None
    sda.log_event = lambda *a, **k: None

    sda.check_restore_stitching(dry_run=False)

    assert len(concat_calls) == 1, f"Expected 1 concat call, got {len(concat_calls)}"
    segments_concatenated, out_file = concat_calls[0]
    assert segments_concatenated == [seg1_path, seg2_path], f"Wrong segments concatenated: {segments_concatenated}"
    assert out_file == stitched_target, f"Wrong stitched output: {out_file}"

    assert len(archive_calls) == 1, f"Expected 1 archive call, got {len(archive_calls)}"
    assert archive_calls[0] == [seg1_path, seg2_path]

    assert len(comskip_calls) == 1, f"Expected 1 comskip enqueue, got {len(comskip_calls)}"
    assert comskip_calls[0] == stitched_target, f"Comskip must receive STITCHED file, got: {comskip_calls[0]}"

    assert restore_state[root_tid]["status"] == "stitched"
    assert restore_state[root_tid]["stitched_file_path"] == stitched_target
    print("PASS: Restored segments stitched into MKV, raw segments archived, and STITCHED file enqueued to comskip")


def test_retry_limits_and_backoff():
    print("\n--- Test 5: Bounded retries and backoff floor ---")
    now = datetime.now(timezone.utc)
    t1_end = now + timedelta(hours=2)

    timer = {
        "Id": "dead-ch-timer",
        "Name": "Brewers: Test Game",
        "ChannelId": "hdhr_121",
        "StartDate": (now - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "EndDate": t1_end.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "PostPaddingSeconds": 1800,
        "Status": "Completed",
        "RecordingPath": "/media/recordings/t.ts",
    }

    sda.fetch_openligadb_matches = lambda now=None: []
    sda.fetch_espn_schedule = lambda: [{
        "id": "e1", "team": "Brewers", "name": "Brewers: Test Game",
        "start": now - timedelta(hours=1), "end": now + timedelta(hours=2),
        "state": "in", "completed": False, "broadcasts": []
    }]
    sda.check_single_tuner_conflict = lambda *a, **k: (False, "")

    created_timers = []
    sda.trigger_auto_restore = lambda t, dry_run=True: (created_timers.append(t), (True, "ok", "r-id", True))[1]

    # 5A: Backoff floor: last attempt was 30 seconds ago
    restore_state = {
        "dead-ch-timer": {
            "original_timer_id": "dead-ch-timer",
            "status": "failed_restore_creation",
            "restore_timer_id": None,
            "restored_at": (now - timedelta(seconds=30)).isoformat(),
            "retry_count": 1,
        }
    }
    sda.load_restore_state = lambda: dict(restore_state)
    sda.check_early_completed_recordings(dry_run=False, api_timers=[], disk_timers=[timer])
    assert len(created_timers) == 0, "Backoff floor (< 2 min) must suppress retry"
    print("PASS: 5A - Pacing floor prevents rapid thrashing")

    # 5B: Max retries ceiling: retry_count already 4
    restore_state["dead-ch-timer"]["restored_at"] = (now - timedelta(minutes=10)).isoformat()
    restore_state["dead-ch-timer"]["retry_count"] = 4
    sda.check_early_completed_recordings(dry_run=False, api_timers=[], disk_timers=[timer])
    assert len(created_timers) == 0, "Max attempts ceiling (4) must halt retries"
    print("PASS: 5B - Max retries ceiling halts infinite retry loop on permanently dead channel")


def main():
    test_fixture_live_triggers_continuation()
    test_fixture_final_does_not_restart()
    test_normal_full_length_unaffected()
    test_single_tuner_conflict_protection()
    test_stitching_and_comskip()
    test_retry_limits_and_backoff()
    print("\n=======================================================")
    print("ALL TESTS PASSED: Early completion recovery verified!")
    print("=======================================================")


if __name__ == "__main__":
    main()
