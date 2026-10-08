"""Synthetic-only regression tests for zone and crossing accounting."""
from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "App"))
from pilot_core import (  # noqa: E402
    WEST_LIMIT, EAST_LIMIT, extract_ais, open_db, process_ais, summary, zone_for,
)

BASE = datetime(2026, 10, 8, 10, 0, tzinfo=timezone.utc)
SALT = b"synthetic-test-only"
MMSI = 123456789  # Deliberately fictitious nine-digit identifier.


def position(lon: float, sog: float = 12, mmsi: int = MMSI) -> dict:
    return {
        "MessageType": "PositionReport",
        "Message": {"PositionReport": {
            "UserID": mmsi, "Latitude": 26.0, "Longitude": lon, "Sog": sog,
        }},
    }


def static(ship_type: int = 80) -> dict:
    return {
        "MessageType": "ShipStaticData",
        "Message": {"ShipStaticData": {"UserID": MMSI, "Type": ship_type}},
    }


class ZoneTests(unittest.TestCase):
    def test_three_zones_and_out_of_bounds(self):
        self.assertEqual(zone_for(26, WEST_LIMIT - .1), "W")
        self.assertEqual(zone_for(26, (WEST_LIMIT + EAST_LIMIT) / 2), "M")
        self.assertEqual(zone_for(26, EAST_LIMIT + .1), "E")
        self.assertIsNone(zone_for(20, 56))

    def test_static_type_extraction(self):
        parsed = extract_ais(static())
        self.assertEqual(parsed["ship_type"], 80)
        self.assertIsNone(parsed["coords"])


class CrossingTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.conn = open_db(Path(self.temp_dir.name) / "pilot.sqlite")

    def tearDown(self):
        self.conn.close()
        self.temp_dir.cleanup()

    def send(self, message, minutes=0):
        result = process_ais(self.conn, message, SALT, BASE + timedelta(minutes=minutes))
        self.conn.commit()
        return result

    def counts(self):
        latest = summary(self.conn)[0]
        return latest["crossings_all"], latest["crossings_tanker_typed"]

    def test_west_to_east_once(self):
        self.send(position(WEST_LIMIT - .1))
        self.send(static(80), 1)
        self.send(position(EAST_LIMIT + .1), 120)
        self.assertEqual(self.counts(), (1, 1))
        self.send(position(EAST_LIMIT + .2), 122)
        self.assertEqual(self.counts(), (1, 1))

    def test_middle_zone_preserves_last_side(self):
        self.send(position(WEST_LIMIT - .1))
        self.send(position((WEST_LIMIT + EAST_LIMIT)/2), 10)
        self.send(position(EAST_LIMIT + .1), 130)
        self.assertEqual(self.counts(), (1, 0))

    def test_late_static_classification(self):
        self.send(position(WEST_LIMIT - .1))
        self.send(position(EAST_LIMIT + .1), 120)
        self.assertEqual(self.counts(), (1, 0))
        self.send(static(83), 121)
        self.assertEqual(self.counts(), (1, 1))
        self.send(static(83), 122)
        self.assertEqual(self.counts(), (1, 1))

    def test_invalid_time_and_speed_do_not_count(self):
        self.send(position(WEST_LIMIT - .1, mmsi=111111111))
        self.send(position(EAST_LIMIT + .1, mmsi=111111111), 2)
        self.send(position(WEST_LIMIT - .1, mmsi=222222222), 10)
        self.send(position(EAST_LIMIT + .1, sog=0, mmsi=222222222), 20)
        self.send(position(WEST_LIMIT - .1, mmsi=333333333), 30)
        self.send(position(EAST_LIMIT + .1, mmsi=333333333), 500)
        self.assertEqual(self.counts(), (0, 0))

    def test_only_west_positions_do_not_count(self):
        for minute in range(0, 60, 10):
            self.send(position(WEST_LIMIT - .02), minute)
        self.assertEqual(self.counts(), (0, 0))


if __name__ == "__main__":
    unittest.main()
