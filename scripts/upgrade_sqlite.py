"""Apply the explicitly authorized schema v2 migration after safe shutdown.

Do not run manually on an active collector. The Windows release wrapper backs up
the database, stops the task, then calls this script in its rollback boundary.
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

APP = Path(__file__).resolve().parents[1] / "App"
sys.path.insert(0, str(APP))
from pilot_core import open_db  # noqa: E402

STABLE_STATS = (
    "ais_position_messages", "ais_static_messages",
    "ais_crossings_all", "ais_crossings_tanker", "ais_connected_seconds",
    "ais_disconnects", "adsb_ok", "adsb_errors", "opensky_ok", "opensky_errors",
)


def stats(conn: sqlite3.Connection):
    return conn.execute(
        "SELECT day," + ",".join(STABLE_STATS) + " FROM day_stats ORDER BY day"
    ).fetchall()


def upgrade(path: Path) -> tuple[int, int]:
    if not path.is_file():
        raise ValueError("missing source database")
    current = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
    try:
        old = current.execute("SELECT v FROM meta WHERE k='schema_version'").fetchone()
        if old not in (("1",), ("2",)):
            raise ValueError("unexpected starting schema version")
        before = stats(current)
    finally:
        current.close()

    migrated = open_db(path)
    try:
        if migrated.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise ValueError("database integrity check failed")
        if stats(migrated) != before:
            raise ValueError("migration changed original aggregate counters")
        version = migrated.execute(
            "SELECT v FROM meta WHERE k='schema_version'"
        ).fetchone()
        if version != ("2",):
            raise ValueError("schema version was not upgraded")
        needed = {"ais_zone_w", "ais_zone_m", "ais_zone_e",
                  "ais_vessels_w", "ais_vessels_m", "ais_vessels_e"}
        actual = {x[1] for x in migrated.execute("PRAGMA table_info(day_stats)")}
        if not needed <= actual:
            raise ValueError("zone counters unavailable")
    finally:
        migrated.close()
    return len(before), len(STABLE_STATS)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", required=True, type=Path)
    args = parser.parse_args()
    try:
        days, counters = upgrade(args.db)
    except (OSError, sqlite3.Error, ValueError):
        print("FAIL: SQLite schema migration verification failed.", file=sys.stderr)
        raise SystemExit(1) from None
    print(f"PASS: schema v2 migration; {days} day(s), {counters} original daily counters preserved.")
