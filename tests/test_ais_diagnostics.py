"""Synthetic-only AIS websocket intake and data-quality accounting tests."""
from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "App"))
from ais_diagnostics import (COLUMNS, REJECTION_COLUMNS, daily_report,
                             ingest_frame, increment)
from pilot_core import (EAST_LIMIT, WEST_LIMIT, open_db, summary)

BASE = datetime(2026, 10, 9, 3, 0, tzinfo=timezone.utc)
SALT = b"synthetic-test-not-live"


def position(lon=WEST_LIMIT-.1, mmsi=111111111, sog=12.):
    return {"MessageType":"PositionReport",
            "Message":{"PositionReport":{"UserID":mmsi, "Latitude":26.,
                                         "Longitude":lon, "Sog":sog}}}


def static():
    return {"MessageType":"ShipStaticData",
            "Message":{"ShipStaticData":{"UserID":111111111,"Type":82}}}


class IntakeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "synthetic.sqlite"
        self.conn = open_db(self.db)
        self.tick = 0

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def send(self, msg):
        now = BASE + timedelta(seconds=self.tick)
        self.tick += 1
        data = msg if isinstance(msg, (str, bytes)) else json.dumps(msg)
        cat = ingest_frame(self.conn, data, SALT, now)
        self.conn.commit()
        return cat

    def info(self):
        return daily_report(self.conn)[BASE.date().isoformat()]

    def check_balanced(self):
        info = self.info()
        self.assertEqual(
            info["frames_received"],
            info["accepted_positions"] + info["accepted_static"] +
            info["processing_errors"] + sum(info[name] for name in REJECTION_COLUMNS),
        )

    def test_accept_position_and_static_without_identifier_in_diagnostics(self):
        self.assertEqual(self.send(position()), "accepted_positions")
        self.assertEqual(self.send(static()), "accepted_static")
        self.assertEqual((self.info()["frames_received"],
                          self.info()["accepted_positions"],
                          self.info()["accepted_static"]), (2, 1, 1))
        self.assertEqual(summary(self.conn)[0]["zone_w_positions"], 1)
        schema = self.conn.execute(
            "SELECT sql FROM sqlite_master WHERE name='ais_intake_day'"
        ).fetchone()[0]
        self.assertNotIn("mmsi", schema.lower())
        self.assertNotIn("latitude", schema.lower())
        self.assertNotIn("longitude", schema.lower())
        self.check_balanced()

    def test_bad_json_does_not_require_reconnect(self):
        self.assertEqual(self.send("{"), "rejected_bad_json")
        self.assertEqual(self.send(position()), "accepted_positions")
        self.check_balanced()

    def test_non_object_json(self):
        self.assertEqual(self.send("[1,2,3]"), "rejected_non_object")
        self.check_balanced()

    def test_unsupported_type(self):
        self.assertEqual(self.send({"MessageType":"SubscriptionConfirmation"}), "rejected_unsupported")
        self.assertEqual(self.send({"MessageType":["unhashable"]}), "rejected_unsupported")
        self.check_balanced()

    def test_structural_error(self):
        self.assertEqual(self.send({"MessageType":"PositionReport","Message":{}}),
                         "rejected_structure")
        bad = position()
        bad["MetaData"] = "malformed"
        self.assertEqual(self.send(bad), "rejected_structure")
        self.check_balanced()

    def test_identifier_error(self):
        self.assertEqual(self.send(position(mmsi=42)), "rejected_identifier")
        self.check_balanced()

    def test_bad_latitude_and_speed(self):
        no_lat = position()
        del no_lat["Message"]["PositionReport"]["Latitude"]
        self.assertEqual(self.send(no_lat), "rejected_position")
        self.assertEqual(self.send(position(sog=float("nan"))), "rejected_position")
        self.check_balanced()

    def test_outside_box(self):
        self.assertEqual(self.send(position(lon=60)), "rejected_outside_box")
        self.check_balanced()

    def test_duplicate_timestamp(self):
        message = json.dumps(position())
        now = BASE
        self.assertEqual(ingest_frame(self.conn, message, SALT, now), "accepted_positions")
        self.assertEqual(ingest_frame(self.conn, message, SALT, now), "rejected_duplicate_stale")
        self.conn.commit()
        self.check_balanced()

    def test_unexpected_processing_failure_is_separate_from_rejection(self):
        with patch("pilot_core.process_ais", side_effect=RuntimeError("synthetic")):
            with self.assertRaises(RuntimeError):
                self.send(position())
        self.assertEqual(self.info()["processing_errors"], 1)
        self.assertEqual(self.info()["accepted_positions"], 0)
        self.assertEqual(summary(self.conn), [])
        self.check_balanced()

    def test_counter_whitelist_prevents_freeform_fields(self):
        with self.assertRaises(ValueError):
            increment(self.conn, BASE.date().isoformat(), "raw_mmsi")
        self.assertEqual(set(self.info().keys()) if daily_report(self.conn) else set(), set())

    def test_tracking_start_marker_and_no_backfill(self):
        start = self.conn.execute(
            "SELECT v FROM meta WHERE k='ais_intake_tracking_since'"
        ).fetchone()
        self.assertIsNotNone(start)
        self.assertEqual(daily_report(self.conn), {})
        self.assertEqual(
            self.conn.execute("SELECT v FROM meta WHERE k='schema_version'").fetchone(),
            ("2",),
        )

    def test_reopen_does_not_reset_counters(self):
        self.send(position())
        self.conn.close()
        self.conn = open_db(self.db)
        self.assertEqual(self.info()["frames_received"], 1)
        self.check_balanced()

    def test_additive_legacy_v2_database_preserves_existing_stats(self):
        self.send(position())
        before = self.conn.execute(
            "SELECT ais_position_messages,ais_zone_w FROM day_stats"
        ).fetchone()
        self.conn.execute("DROP TABLE ais_intake_day")
        self.conn.execute("DELETE FROM meta WHERE k='ais_intake_tracking_since'")
        self.conn.commit()
        self.conn.close()
        self.conn = open_db(self.db)
        after = self.conn.execute(
            "SELECT ais_position_messages,ais_zone_w FROM day_stats"
        ).fetchone()
        self.assertEqual(before, after)
        self.assertEqual(daily_report(self.conn), {})
        self.assertEqual(self.conn.execute(
            "SELECT v FROM meta WHERE k='schema_version'"
        ).fetchone(), ("2",))

    def test_reason_is_not_payload_text(self):
        self.assertEqual(self.send({"MessageType":"unknown",
                                   "Internal":"SYNTHETIC-SECRET-NOT-TO-LOG"}),
                         "rejected_unsupported")
        serialized = json.dumps(self.info())
        self.assertNotIn("SYNTHETIC-SECRET", serialized)
        self.check_balanced()


if __name__ == "__main__":
    unittest.main()
