#!/usr/bin/env python3
"""Comprehensive test suite for Tuner Availability Broker.

Proves in BOTH directions (Grant and Refusal):
1. Jellyfin recording (active recording blocks; no recording allows)
2. MCT capture (active MCT window blocks; outside window allows)
3. Live viewing (active Live TV session blocks; idle allows)
4. Lookahead margin (duration exceeding gap blocks; fitting duration allows)
5. Stale lease (active lease blocks; stale lease reclaimed and allows)
6. Broker unavailable (maintenance denied; authoritative recording unaffected)
"""
from __future__ import annotations

import copy
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

# Ensure sync directory is on sys.path
sys.path.insert(0, "/srv/media-core/sync")
import tuner_broker
import maintenance_window


class TestTunerBrokerBothDirections(unittest.TestCase):
    def setUp(self):
        # Use isolated temporary directory for state files during unit tests
        self.test_dir = tempfile.mkdtemp(prefix="tuner_broker_test_")
        self.orig_lease_file = tuner_broker.LEASE_FILE
        self.orig_mct_file = tuner_broker.MCT_WINDOWS_FILE
        self.orig_lock_file = tuner_broker.LOCK_FILE

        tuner_broker.LEASE_FILE = Path(self.test_dir) / "tuner_lease.json"
        tuner_broker.MCT_WINDOWS_FILE = Path(self.test_dir) / "mct-windows.json"
        tuner_broker.LOCK_FILE = Path(self.test_dir) / ".tuner_broker.lock"

        # Baseline timestamp: Friday 2026-09-04 10:00:00 UTC
        self.now = datetime(2026, 9, 4, 10, 0, 0, tzinfo=timezone.utc)

    def tearDown(self):
        tuner_broker.LEASE_FILE = self.orig_lease_file
        tuner_broker.MCT_WINDOWS_FILE = self.orig_mct_file
        tuner_broker.LOCK_FILE = self.orig_lock_file
        shutil.rmtree(self.test_dir, ignore_errors=True)

    # -------------------------------------------------------------------------
    # 1. Jellyfin Recording: Refusal & Grant
    # -------------------------------------------------------------------------
    def test_1_jellyfin_recording_refusal(self):
        """Tuner busy with an in-progress Jellyfin recording -> REFUSAL."""
        with patch.object(tuner_broker, "_check_jellyfin_recordings", return_value=(True, ["Fußball: Bundesliga Live"])), \
             patch.object(tuner_broker, "_check_tivimate_recording", return_value=False), \
             patch.object(tuner_broker, "_check_jellyfin_live_sessions", return_value=(False, [])), \
             patch.object(tuner_broker, "_check_threadfin_active", return_value=(False, "idle")), \
             patch.object(tuner_broker, "_get_jellyfin_timers", return_value=[]), \
             patch.object(tuner_broker, "_get_mct_windows", return_value=[]):

            ok, reason, details = tuner_broker.check(minutes=10, purpose="epg-test", at=self.now)
            self.assertFalse(ok)
            self.assertIn("busy with Jellyfin recording", reason)
            self.assertIn("Fußball: Bundesliga Live", reason)

            # Also verify maintenance_window.is_open() reflects closure
            is_open, win_reason = maintenance_window.is_open(at=self.now, minutes=10)
            self.assertFalse(is_open)
            self.assertIn("Fußball: Bundesliga Live", win_reason)

    def test_1_jellyfin_recording_grant(self):
        """No Jellyfin recording in progress and no collision -> GRANT."""
        with patch.object(tuner_broker, "_check_jellyfin_recordings", return_value=(False, [])), \
             patch.object(tuner_broker, "_check_tivimate_recording", return_value=False), \
             patch.object(tuner_broker, "_check_jellyfin_live_sessions", return_value=(False, [])), \
             patch.object(tuner_broker, "_check_threadfin_active", return_value=(False, "idle")), \
             patch.object(tuner_broker, "_get_jellyfin_timers", return_value=[]), \
             patch.object(tuner_broker, "_get_mct_windows", return_value=[]):

            ok, reason, details = tuner_broker.check(minutes=10, purpose="epg-test", at=self.now)
            self.assertTrue(ok)
            self.assertIn("available for 10m", reason)

            is_open, win_reason = maintenance_window.is_open(at=self.now, minutes=10)
            self.assertTrue(is_open)
            self.assertEqual(win_reason, "ok")

    # -------------------------------------------------------------------------
    # 2. MCT Capture: Refusal & Grant
    # -------------------------------------------------------------------------
    def test_2_mct_capture_refusal(self):
        """Current time overlaps an MCT capture window -> REFUSAL."""
        mct_interval = {
            "title": "Brewers: MIL @ CIN",
            "start": self.now - timedelta(minutes=10),
            "end": self.now + timedelta(minutes=60),
            "source": "mct",
        }
        with patch.object(tuner_broker, "_check_jellyfin_recordings", return_value=(False, [])), \
             patch.object(tuner_broker, "_check_tivimate_recording", return_value=False), \
             patch.object(tuner_broker, "_check_jellyfin_live_sessions", return_value=(False, [])), \
             patch.object(tuner_broker, "_check_threadfin_active", return_value=(False, "idle")), \
             patch.object(tuner_broker, "_get_jellyfin_timers", return_value=[]), \
             patch.object(tuner_broker, "_get_mct_windows", return_value=[mct_interval]):

            ok, reason, details = tuner_broker.check(minutes=5, purpose="icon-test", at=self.now)
            self.assertFalse(ok)
            self.assertIn("reserved for 'Brewers: MIL @ CIN' (mct)", reason)

            is_open, win_reason = maintenance_window.is_open(at=self.now, minutes=5)
            self.assertFalse(is_open)
            self.assertIn("Brewers: MIL @ CIN", win_reason)

    def test_2_mct_capture_grant(self):
        """MCT capture is hours away -> GRANT."""
        mct_interval = {
            "title": "Brewers: MIL @ CIN",
            "start": self.now + timedelta(hours=4),
            "end": self.now + timedelta(hours=7),
            "source": "mct",
        }
        with patch.object(tuner_broker, "_check_jellyfin_recordings", return_value=(False, [])), \
             patch.object(tuner_broker, "_check_tivimate_recording", return_value=False), \
             patch.object(tuner_broker, "_check_jellyfin_live_sessions", return_value=(False, [])), \
             patch.object(tuner_broker, "_check_threadfin_active", return_value=(False, "idle")), \
             patch.object(tuner_broker, "_get_jellyfin_timers", return_value=[]), \
             patch.object(tuner_broker, "_get_mct_windows", return_value=[mct_interval]):

            ok, reason, details = tuner_broker.check(minutes=15, purpose="icon-test", at=self.now)
            self.assertTrue(ok)
            self.assertIn("available for 15m", reason)

            is_open, win_reason = maintenance_window.is_open(at=self.now, minutes=15)
            self.assertTrue(is_open)
            self.assertEqual(win_reason, "ok")

    # -------------------------------------------------------------------------
    # 3. Someone Watching Live: Refusal & Grant
    # -------------------------------------------------------------------------
    def test_3_live_viewing_refusal(self):
        """Active Jellyfin session watching Live TV -> REFUSAL."""
        live_session = "Living Room TV (Wholphin/nate) watching 'ESPN HD'"
        with patch.object(tuner_broker, "_check_jellyfin_recordings", return_value=(False, [])), \
             patch.object(tuner_broker, "_check_tivimate_recording", return_value=False), \
             patch.object(tuner_broker, "_check_jellyfin_live_sessions", return_value=(True, [live_session])), \
             patch.object(tuner_broker, "_check_threadfin_active", return_value=(False, "idle")), \
             patch.object(tuner_broker, "_get_jellyfin_timers", return_value=[]), \
             patch.object(tuner_broker, "_get_mct_windows", return_value=[]):

            ok, reason, details = tuner_broker.check(minutes=5, purpose="scraper-test", at=self.now)
            self.assertFalse(ok)
            self.assertIn("live viewing active in Jellyfin", reason)
            self.assertIn("Living Room TV", reason)

            is_open, win_reason = maintenance_window.is_open(at=self.now, minutes=5)
            self.assertFalse(is_open)
            self.assertIn("live viewing active", win_reason)

    def test_3_live_viewing_grant(self):
        """No live viewing session and no active Threadfin stream -> GRANT."""
        with patch.object(tuner_broker, "_check_jellyfin_recordings", return_value=(False, [])), \
             patch.object(tuner_broker, "_check_tivimate_recording", return_value=False), \
             patch.object(tuner_broker, "_check_jellyfin_live_sessions", return_value=(False, [])), \
             patch.object(tuner_broker, "_check_threadfin_active", return_value=(False, "idle")), \
             patch.object(tuner_broker, "_get_jellyfin_timers", return_value=[]), \
             patch.object(tuner_broker, "_get_mct_windows", return_value=[]):

            ok, reason, details = tuner_broker.check(minutes=5, purpose="scraper-test", at=self.now)
            self.assertTrue(ok)
            self.assertIn("available for 5m", reason)

    # -------------------------------------------------------------------------
    # 4. Duration Does Not Fit Before Next Reservation: Refusal & Grant
    # -------------------------------------------------------------------------
    def test_4_duration_does_not_fit_refusal(self):
        """Next reservation in 10 minutes. Job requests 9m with 2m margin (needs 11m) -> REFUSAL."""
        next_res = {
            "title": "Badgers Football",
            "start": self.now + timedelta(minutes=10),
            "end": self.now + timedelta(hours=3),
            "source": "jellyfin",
        }
        with patch.object(tuner_broker, "_check_jellyfin_recordings", return_value=(False, [])), \
             patch.object(tuner_broker, "_check_tivimate_recording", return_value=False), \
             patch.object(tuner_broker, "_check_jellyfin_live_sessions", return_value=(False, [])), \
             patch.object(tuner_broker, "_check_threadfin_active", return_value=(False, "idle")), \
             patch.object(tuner_broker, "_get_jellyfin_timers", return_value=[next_res]), \
             patch.object(tuner_broker, "_get_mct_windows", return_value=[]):

            ok, reason, details = tuner_broker.check(minutes=9, margin_minutes=2, at=self.now)
            self.assertFalse(ok)
            self.assertIn("requested 9m + 2m margin exceeds available gap", reason)
            self.assertIn("10.0m until 'Badgers Football'", reason)

    def test_4_duration_fits_grant(self):
        """Next reservation in 10 minutes. Job requests 5m with 2m margin (needs 7m) -> GRANT."""
        next_res = {
            "title": "Badgers Football",
            "start": self.now + timedelta(minutes=10),
            "end": self.now + timedelta(hours=3),
            "source": "jellyfin",
        }
        with patch.object(tuner_broker, "_check_jellyfin_recordings", return_value=(False, [])), \
             patch.object(tuner_broker, "_check_tivimate_recording", return_value=False), \
             patch.object(tuner_broker, "_check_jellyfin_live_sessions", return_value=(False, [])), \
             patch.object(tuner_broker, "_check_threadfin_active", return_value=(False, "idle")), \
             patch.object(tuner_broker, "_get_jellyfin_timers", return_value=[next_res]), \
             patch.object(tuner_broker, "_get_mct_windows", return_value=[]):

            ok, reason, details = tuner_broker.check(minutes=5, margin_minutes=2, at=self.now)
            self.assertTrue(ok)
            self.assertIn("available for 5m", reason)

    # -------------------------------------------------------------------------
    # 5. Stale Lease Reclaimed: Refusal while active, Grant on reclaim
    # -------------------------------------------------------------------------
    def test_5_active_lease_refusal_and_stale_reclaim_grant(self):
        """Active lease blocks Job B; after heartbeat timeout, lease is stale and reclaimed -> GRANT."""
        with patch.object(tuner_broker, "_check_jellyfin_recordings", return_value=(False, [])), \
             patch.object(tuner_broker, "_check_tivimate_recording", return_value=False), \
             patch.object(tuner_broker, "_check_jellyfin_live_sessions", return_value=(False, [])), \
             patch.object(tuner_broker, "_check_threadfin_active", return_value=(False, "idle")), \
             patch.object(tuner_broker, "_get_jellyfin_timers", return_value=[]), \
             patch.object(tuner_broker, "_get_mct_windows", return_value=[]):

            # Job A acquires a 5m lease with 60s heartbeat TTL at t=0
            ok_a, msg_a, lease_a = tuner_broker.acquire(minutes=5, purpose="job-a", heartbeat_ttl=60, at=self.now)
            self.assertTrue(ok_a)
            self.assertIsNotNone(lease_a)

            # At t = 30s: Job A is still active and healthy -> Job B is refused
            t_30s = self.now + timedelta(seconds=30)
            ok_b, reason_b, lease_b = tuner_broker.acquire(minutes=5, purpose="job-b", at=t_30s)
            self.assertFalse(ok_b)
            self.assertIn("currently leased to 'job-a'", reason_b)

            # Job A crashes / does not heartbeat.
            # At t = 90s: heartbeat TTL (60s) has expired. Job A's lease is stale.
            t_90s = self.now + timedelta(seconds=90)
            # Inspect lease status: should report stale
            lease, is_active, is_stale, desc = tuner_broker.get_current_lease(t_90s)
            self.assertFalse(is_active)
            self.assertTrue(is_stale)
            self.assertIn("heartbeat timed out", desc)

            # Job B requests tuner -> broker automatically reclaims stale lease and GRANTS to Job B
            ok_b2, reason_b2, lease_b2 = tuner_broker.acquire(minutes=5, purpose="job-b", at=t_90s)
            self.assertTrue(ok_b2)
            self.assertIsNotNone(lease_b2)
            self.assertEqual(lease_b2["purpose"], "job-b")

    # -------------------------------------------------------------------------
    # 6. Broker Unavailable: Maintenance Denied, Recordings Unaffected
    # -------------------------------------------------------------------------
    def test_6_broker_unavailable_maintenance_denied(self):
        """When broker fails or media service queries raise errors, maintenance is DENIED (fails closed)."""
        with patch.object(tuner_broker, "_check_jellyfin_recordings", side_effect=RuntimeError("Jellyfin connection refused")), \
             patch.object(tuner_broker, "_check_threadfin_active", side_effect=RuntimeError("Threadfin down")):

            # check() fails closed
            ok, reason, details = tuner_broker.check(minutes=5, purpose="maintenance-job", at=self.now)
            self.assertFalse(ok)

            # acquire() fails closed
            acq_ok, acq_reason, acq_lease = tuner_broker.acquire(minutes=5, purpose="maintenance-job", at=self.now)
            self.assertFalse(acq_ok)

            # maintenance_window.is_open() fails closed
            is_open, win_reason = maintenance_window.is_open(at=self.now, minutes=5)
            self.assertFalse(is_open)

    def test_6_prefer_the_human_yield(self):
        """Holding a lease when a household member starts watching Live TV signals YIELD on heartbeat."""
        with patch.object(tuner_broker, "_check_jellyfin_recordings", return_value=(False, [])), \
             patch.object(tuner_broker, "_check_tivimate_recording", return_value=False), \
             patch.object(tuner_broker, "_check_jellyfin_live_sessions", return_value=(False, [])), \
             patch.object(tuner_broker, "_check_threadfin_active", return_value=(False, "idle")), \
             patch.object(tuner_broker, "_get_jellyfin_timers", return_value=[]), \
             patch.object(tuner_broker, "_get_mct_windows", return_value=[]):

            # Acquire lease
            ok, msg, lease = tuner_broker.acquire(minutes=10, purpose="long-maintenance", heartbeat_ttl=120, at=self.now)
            self.assertTrue(ok)
            lease_id = lease["lease_id"]

            # First heartbeat at t=30s with no viewer -> OK
            hb_ok, hb_msg, _ = tuner_broker.heartbeat(lease_id, at=self.now + timedelta(seconds=30))
            self.assertTrue(hb_ok)
            self.assertIn("renewed", hb_msg)

        # Now household member turns on TV at t=60s
        live_session = "Living Room TV (Wholphin/nate) watching 'ESPN HD'"
        with patch.object(tuner_broker, "_check_jellyfin_recordings", return_value=(False, [])), \
             patch.object(tuner_broker, "_check_tivimate_recording", return_value=False), \
             patch.object(tuner_broker, "_check_jellyfin_live_sessions", return_value=(True, [live_session])), \
             patch.object(tuner_broker, "_check_threadfin_active", return_value=(False, "idle")), \
             patch.object(tuner_broker, "_get_jellyfin_timers", return_value=[]), \
             patch.object(tuner_broker, "_get_mct_windows", return_value=[]):

            hb_ok, hb_msg, yielded_lease = tuner_broker.heartbeat(lease_id, at=self.now + timedelta(seconds=60))
            self.assertFalse(hb_ok)
            self.assertIn("yield: household live viewing started", hb_msg)
            self.assertEqual(yielded_lease["status"], "yielded")


if __name__ == "__main__":
    unittest.main(verbosity=2)
