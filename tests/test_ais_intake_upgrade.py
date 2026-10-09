"""Verify additive AIS instrumentation on a copy of a synthetic legacy v2 DB."""
from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "App"))
from snapshot_sqlite import snapshot  # noqa: E402
from upgrade_ais_intake import upgrade  # noqa: E402
from ais_diagnostics import ingest_frame, daily_report  # noqa: E402
from pilot_core import open_db, summary  # noqa: E402


class OfflineIntakeUpgradeTests(unittest.TestCase):
    def test_upgrade_copy_preserves_v2_and_is_not_backfilled(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            original = root / "original.sqlite"
            copied = root / "consistent-backup.sqlite"
            conn = open_db(original)
            conn.execute("""
                INSERT INTO day_stats(day,ais_position_messages,
                    ais_zone_w,ais_vessels_w,ais_connected_seconds)
                VALUES('2026-10-08',177,177,11,3600)
            """)
            # Simulate a real v2 DB BEFORE diagnostics were introduced.
            conn.execute("DROP TABLE ais_intake_day")
            conn.execute("DELETE FROM meta WHERE k='ais_intake_tracking_since'")
            conn.commit()
            conn.close()

            version, days = snapshot(original, copied)
            self.assertEqual((version,days), ("2", 1))
            checked_days, counters = upgrade(copied)
            self.assertEqual((checked_days,counters), (1, 16))
            src = sqlite3.connect(original)
            try:
                self.assertIsNone(src.execute(
                    "SELECT name FROM sqlite_master WHERE name='ais_intake_day'"
                ).fetchone())
                self.assertEqual(src.execute(
                    "SELECT ais_position_messages FROM day_stats"
                ).fetchone()[0], 177)
            finally:
                src.close()

            after = sqlite3.connect(copied)
            try:
                self.assertEqual(after.execute(
                    "SELECT v FROM meta WHERE k='schema_version'"
                ).fetchone()[0], "2")
                self.assertEqual(summary(after)[0]["zone_w_positions"], 177)
                self.assertEqual(daily_report(after), {})  # not historical 0 frames
                self.assertIsNotNone(after.execute(
                    "SELECT v FROM meta WHERE k='ais_intake_tracking_since'"
                ).fetchone())
            finally:
                after.close()
            with self.assertRaises(ValueError):
                upgrade(copied)

    def test_report_shows_only_aggregate_diagnostics(self):
        with tempfile.TemporaryDirectory() as folder:
            database = Path(folder) / "synthetic.sqlite"
            conn = open_db(database)
            now = datetime.now(timezone.utc)
            raw = json.dumps({
                "MessageType": "PositionReport",
                "Message": {"PositionReport": {
                    "UserID": 987654321, "Latitude": 26, "Longitude": 56,
                    "Sog": 12
                }}
            })
            self.assertEqual(ingest_frame(conn, raw, b"test", now),
                             "accepted_positions")
            self.assertEqual(ingest_frame(conn, "{", b"test", now),
                             "rejected_bad_json")
            conn.commit()
            conn.close()
            proc = subprocess.run(
                [sys.executable, str(ROOT / "App" / "report.py"),
                 "--db", str(database)],
                capture_output=True, text=True, check=True,
            )
            self.assertIn("frames=2", proc.stdout)
            self.assertIn("accepted=1", proc.stdout)
            self.assertIn("rejected=1", proc.stdout)
            self.assertIn("bad_json=1", proc.stdout)
            self.assertIn("accounting=OK", proc.stdout)
            self.assertNotIn("987654321", proc.stdout)


if __name__ == "__main__":
    unittest.main()
