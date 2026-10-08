"""Synthetic, isolated regression tests for AIS schema v2 and passage accounting."""
from __future__ import annotations

import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "App"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from validate_migration import verify_live_db_backup  # noqa: E402
from pilot_core import EAST_LIMIT, WEST_LIMIT, open_db, process_ais, prune_temp, summary  # noqa: E402

UTC = timezone.utc
BASE = datetime(2026, 10, 8, 10, tzinfo=UTC)
SALT = b"unit-test-key-never-use-for-live-traffic"
WEST = WEST_LIMIT - .01
EAST = EAST_LIMIT + .01
MIDDLE = (WEST_LIMIT + EAST_LIMIT) / 2


def ais(lon: float, mmsi: int = 111111111, sog: float = 12) -> dict:
    return {"MessageType": "PositionReport",
            "Message": {"PositionReport": {
                "UserID": mmsi, "Latitude": 26.0, "Longitude": lon, "Sog": sog}}}


def ship_type(kind: int = 82, mmsi: int = 111111111) -> dict:
    return {"MessageType": "ShipStaticData",
            "Message": {"ShipStaticData": {"UserID": mmsi, "Type": kind}}}


class V2ScenarioTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = open_db(Path(self.tmp.name) / "synthetic.sqlite")

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def send(self, msg, minutes=0, when=None):
        when = when or BASE + timedelta(minutes=minutes)
        accepted = process_ais(self.conn, msg, SALT, when)
        self.conn.commit()
        return accepted

    def rows(self):
        return {row["day"]: row for row in summary(self.conn)}

    def test_zone_aggregate_and_unique_vessel_count(self):
        self.send(ais(WEST, 111111111))
        self.send(ais(WEST, 111111111), 10)
        self.send(ais(MIDDLE, 111111111), 20)
        self.send(ais(EAST, 222222222), 30)
        self.send(ais(WEST, 333333333), 40)
        row = self.rows()[BASE.date().isoformat()]
        self.assertEqual((row["zone_w_positions"], row["zone_m_positions"],
                          row["zone_e_positions"]), (3, 1, 1))
        self.assertEqual((row["zone_w_vessels"], row["zone_m_vessels"],
                          row["zone_e_vessels"]), (2, 1, 1))
        self.assertEqual(row["ais_positions"], 5)
        self.assertEqual(row["crossings_all"], 0)
        # Hashed identifiers only; no raw coordinates are stored in SQLite schema.
        tables = {x[0] for x in self.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertIn("zone_vessel_day", tables)
        self.assertIn("vessel_state", tables)

    def test_recent_same_side_anchor_enables_crossing(self):
        self.send(ais(WEST), 0)
        self.send(ais(WEST), 420)
        self.send(ais(EAST), 600)
        self.assertEqual(self.rows()[BASE.date().isoformat()]["crossings_all"], 1)

    def test_low_speed_opposite_report_does_not_eat_crossing(self):
        self.send(ais(WEST))
        self.send(ais(EAST, sog=0), 120)
        self.assertEqual(self.rows()[BASE.date().isoformat()]["crossings_all"], 0)
        self.send(ais(EAST), 180)
        self.assertEqual(self.rows()[BASE.date().isoformat()]["crossings_all"], 1)

    def test_impossible_jump_cannot_eat_later_valid_crossing(self):
        self.send(ais(WEST))
        self.send(ais(EAST), 2)
        self.assertEqual(self.rows()[BASE.date().isoformat()]["crossings_all"], 0)
        self.send(ais(EAST), 120)
        self.assertEqual(self.rows()[BASE.date().isoformat()]["crossings_all"], 1)

    def test_minimum_geographic_speed_rejects_too_fast_jump(self):
        self.send(ais(WEST))
        self.send(ais(EAST), 20)  # At least ~32 nautical miles between disjoint zones.
        self.assertEqual(self.rows()[BASE.date().isoformat()]["crossings_all"], 0)
        self.send(ais(EAST), 130)
        self.assertEqual(self.rows()[BASE.date().isoformat()]["crossings_all"], 1)

    def test_utc_midnight_carry_over_counts_on_arrival_day(self):
        previous = datetime(2026, 10, 8, 23, tzinfo=UTC)
        self.send(ais(WEST), when=previous)
        self.send(ais(EAST), when=previous + timedelta(hours=3))
        rows = self.rows()
        self.assertEqual(rows["2026-10-08"]["crossings_all"], 0)
        self.assertEqual(rows["2026-10-09"]["crossings_all"], 1)
        self.assertEqual(rows["2026-10-09"]["zone_e_positions"], 1)

    def test_late_tanker_type_reconciles_across_utc_day(self):
        previous = datetime(2026, 10, 8, 23, tzinfo=UTC)
        self.send(ais(WEST), when=previous)
        self.send(ais(EAST), when=previous + timedelta(hours=3))
        self.send(ship_type(), when=previous + timedelta(hours=3, minutes=1))
        self.send(ship_type(), when=previous + timedelta(hours=3, minutes=2))
        self.assertEqual(self.rows()["2026-10-09"]["crossings_tanker_typed"], 1)

    def test_subsecond_order_protects_static_ship_type_and_side(self):
        t0 = BASE + timedelta(microseconds=100)
        self.assertTrue(self.send(ais(WEST), when=t0))
        self.assertTrue(self.send(ship_type(), when=t0 + timedelta(microseconds=400)))
        # Delayed position must not override the more recent static message.
        self.assertFalse(self.send(ais(EAST), when=t0 + timedelta(microseconds=200)))
        self.send(ais(EAST), when=t0 + timedelta(hours=2))
        row = self.rows()[BASE.date().isoformat()]
        self.assertEqual(row["ais_positions"], 2)
        self.assertEqual(row["crossings_tanker_typed"], 1)

    def test_duplicate_and_time_reversal_do_not_change_counters(self):
        self.assertTrue(self.send(ais(WEST), 0))
        self.assertFalse(self.send(ais(EAST), 0))
        self.assertFalse(self.send(ais(EAST), -1))
        self.assertEqual(self.rows()[BASE.date().isoformat()]["ais_positions"], 1)
        self.assertEqual(self.rows()[BASE.date().isoformat()]["zone_e_positions"], 0)

    def test_after_six_hours_first_opposite_is_baseline_not_crossing(self):
        self.send(ais(WEST))
        self.send(ais(EAST), 500)  # Observation gap; physical passage unverified.
        self.send(ais(EAST), 600)
        self.assertEqual(self.rows()[BASE.date().isoformat()]["crossings_all"], 0)
        self.send(ais(WEST), 780)
        self.assertEqual(self.rows()[BASE.date().isoformat()]["crossings_all"], 1)

    def test_each_direction_deduplicated_per_day(self):
        self.send(ais(WEST))
        self.send(ais(EAST), 120)
        self.send(ais(WEST), 240)
        self.send(ais(EAST), 360)
        self.assertEqual(self.rows()[BASE.date().isoformat()]["crossings_all"], 2)

    def test_retention_discards_old_pseudonymous_zone_records(self):
        self.send(ais(WEST))
        prune_temp(self.conn, BASE + timedelta(days=16))
        self.assertEqual(self.conn.execute(
            "SELECT COUNT(*) FROM zone_vessel_day").fetchone()[0], 0)
        self.assertEqual(self.conn.execute(
            "SELECT COUNT(*) FROM vessel_state").fetchone()[0], 0)
        self.assertEqual(self.rows()[BASE.date().isoformat()]["zone_w_positions"], 1)

    def test_migration_is_repeatable(self):
        self.send(ais(WEST))
        self.conn.close()
        self.conn = open_db(Path(self.tmp.name) / "synthetic.sqlite")
        self.assertEqual(self.conn.execute(
            "SELECT v FROM meta WHERE k='schema_version'").fetchone()[0], "2")
        self.assertEqual(self.rows()[BASE.date().isoformat()]["zone_w_positions"], 1)


class LegacyV1MigrationTests(unittest.TestCase):
    def test_additive_migration_preserves_preexisting_totals(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "legacy_synthetic.sqlite"
            old = sqlite3.connect(target)
            old.executescript("""
                CREATE TABLE meta(k TEXT PRIMARY KEY, v TEXT NOT NULL);
                INSERT INTO meta VALUES('schema_version','1');
                INSERT INTO meta VALUES('pilot_start_utc','2026-10-08T10:00:00+00:00');
                CREATE TABLE day_stats(
                  day TEXT PRIMARY KEY, ais_position_messages INTEGER NOT NULL DEFAULT 0,
                  ais_static_messages INTEGER NOT NULL DEFAULT 0,
                  ais_crossings_all INTEGER NOT NULL DEFAULT 0,
                  ais_crossings_tanker INTEGER NOT NULL DEFAULT 0,
                  ais_connected_seconds REAL NOT NULL DEFAULT 0,
                  ais_disconnects INTEGER NOT NULL DEFAULT 0,
                  adsb_ok INTEGER NOT NULL DEFAULT 0, adsb_errors INTEGER NOT NULL DEFAULT 0,
                  opensky_ok INTEGER NOT NULL DEFAULT 0, opensky_errors INTEGER NOT NULL DEFAULT 0);
                INSERT INTO day_stats(day,ais_position_messages,ais_connected_seconds)
                VALUES('2026-10-08',9,3600);
                CREATE TABLE vessel_day(
                  day TEXT NOT NULL, vessel_hash TEXT NOT NULL, ship_type INTEGER,
                  last_zone TEXT,zone_since TEXT,last_seen TEXT,
                  PRIMARY KEY(day,vessel_hash));
                CREATE TABLE counted_crossing(
                  day TEXT NOT NULL, vessel_hash TEXT NOT NULL, direction TEXT NOT NULL,
                  PRIMARY KEY(day,vessel_hash,direction));
                CREATE TABLE flight_snapshot(
                  utc TEXT NOT NULL,provider TEXT NOT NULL,observed_airborne INTEGER NOT NULL,
                  PRIMARY KEY(utc,provider));
                CREATE TABLE source_error(utc TEXT NOT NULL,provider TEXT NOT NULL,kind TEXT NOT NULL);
            """)
            old.commit()
            old.close()
            # Rehearse v1 -> v2 on an isolated backup, not the original file.
            days, stable_columns = verify_live_db_backup(target)
            self.assertEqual((days, stable_columns), (1, 10))
            still_old = sqlite3.connect(target)
            self.assertEqual(still_old.execute(
                "SELECT v FROM meta WHERE k='schema_version'").fetchone()[0], "1")
            still_old.close()
            conn = open_db(target)
            try:
                row = summary(conn)[0]
                self.assertEqual(row["ais_positions"], 9)
                self.assertEqual(row["connected_seconds"], 3600)
                self.assertEqual(row["zone_w_positions"], 0)  # UNKNOWN, not true zero.
                self.assertEqual(row["zone_e_positions"], 0)
                self.assertEqual(conn.execute(
                    "SELECT v FROM meta WHERE k='schema_version'").fetchone()[0], "2")
                tracking = conn.execute(
                    "SELECT v FROM meta WHERE k='ais_zone_tracking_since'").fetchone()
                self.assertIsNotNone(tracking)
            finally:
                conn.close()
            conn = open_db(target)
            try:
                self.assertEqual(summary(conn)[0]["ais_positions"], 9)
                self.assertEqual(conn.execute(
                    "SELECT COUNT(*) FROM zone_vessel_day").fetchone()[0], 0)
            finally:
                conn.close()


if __name__ == "__main__":
    unittest.main()
