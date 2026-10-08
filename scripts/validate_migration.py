"""Rehearse schema migration on a TEMPORARY SQLite backup; never mutate source.

No raw AIS rows, vessel identifiers, local paths or credentials are printed.
The source database is opened with SQLite URI mode=ro; online-backup captures
a consistent snapshot even when the collector is writing in WAL mode.
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "App"))
from pilot_core import open_db  # noqa: E402

STABLE_STATS = (
    "ais_position_messages", "ais_static_messages",
    "ais_crossings_all", "ais_crossings_tanker", "ais_connected_seconds",
    "ais_disconnects", "adsb_ok", "adsb_errors", "opensky_ok", "opensky_errors",
)


def captured_stats(conn: sqlite3.Connection) -> list[tuple]:
    return conn.execute(
        "SELECT day," + ",".join(STABLE_STATS) + " FROM day_stats ORDER BY day"
    ).fetchall()


def verify_live_db_backup(source_path: Path) -> tuple[int, int]:
    if not source_path.is_file():
        raise ValueError("source SQLite file is missing")
    with tempfile.TemporaryDirectory(prefix="airsea-migration-check-") as directory:
        copied = Path(directory) / "snapshot.sqlite"
        source = sqlite3.connect(source_path.resolve().as_uri() + "?mode=ro",
                                 uri=True, timeout=10)
        try:
            if source.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise ValueError("source SQLite integrity check failed")
            legacy = captured_stats(source)
            backup = sqlite3.connect(copied)
            try:
                source.backup(backup, pages=128, sleep=0.1)
            finally:
                backup.close()
        finally:
            source.close()

        upgraded = open_db(copied)
        try:
            assert upgraded.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
            if captured_stats(upgraded) != legacy:
                raise ValueError("existing daily totals changed during migration")
            schema = upgraded.execute(
                "SELECT v FROM meta WHERE k='schema_version'"
            ).fetchone()
            if schema != ("2",):
                raise ValueError("schema v2 migration did not complete")
            expected_columns = {
                "ais_zone_w", "ais_zone_m", "ais_zone_e",
                "ais_vessels_w", "ais_vessels_m", "ais_vessels_e",
            }
            columns = {row[1] for row in upgraded.execute("PRAGMA table_info(day_stats)")}
            if not expected_columns <= columns:
                raise ValueError("zone columns not available after migration")
            markers = upgraded.execute(
                "SELECT v FROM meta WHERE k='ais_zone_tracking_since'"
            ).fetchone()
            if markers is None:
                raise ValueError("zone metrics tracking start missing")
            vessel_state = upgraded.execute(
                "SELECT COUNT(*) FROM vessel_state"
            ).fetchone()[0]
            day_count = len(legacy)
        finally:
            upgraded.close()

        # Opening again verifies the migration is idempotent on the COPY.
        again = open_db(copied)
        try:
            if captured_stats(again) != legacy:
                raise ValueError("second open changed daily totals")
            if again.execute(
                "SELECT COUNT(*) FROM vessel_state"
            ).fetchone()[0] != vessel_state:
                raise ValueError("second open changed temporary vessel state")
        finally:
            again.close()
        return day_count, len(STABLE_STATS)


if __name__ == "__main__":
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--db", type=Path, required=True,
                     help="Existing SQLite database to READ and back up for an isolated v2 rehearsal.")
    args = cli.parse_args()
    try:
        days, checked_columns = verify_live_db_backup(args.db)
    except (OSError, sqlite3.Error, ValueError, AssertionError) as exc:
        # No exception text, as filesystem errors may reveal private paths.
        print("FAIL: offline migration validation did not pass.", file=sys.stderr)
        raise SystemExit(1) from None
    print(f"PASS: isolated schema v2 rehearsal; {days} days preserved, "
          f"{checked_columns} legacy counters per day unchanged.")
    print("The source database and live collector were not modified.")
