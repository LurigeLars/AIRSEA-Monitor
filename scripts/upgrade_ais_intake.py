"""Add AIS intake diagnostics to a stopped, backed-up schema-v2 database.

This does not change the existing SQLite schema version or historical totals.
Never call directly while the collector is running; use the release wrapper.
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "App"))
from pilot_core import open_db  # noqa: E402
from ais_diagnostics import COLUMNS, daily_report  # noqa: E402

STABLE = (
    "ais_position_messages", "ais_static_messages", "ais_crossings_all",
    "ais_crossings_tanker", "ais_connected_seconds", "ais_disconnects",
    "adsb_ok", "adsb_errors", "opensky_ok", "opensky_errors",
    "ais_zone_w", "ais_zone_m", "ais_zone_e",
    "ais_vessels_w", "ais_vessels_m", "ais_vessels_e",
)


def totals(conn: sqlite3.Connection):
    return conn.execute("SELECT day," + ",".join(STABLE) +
                        " FROM day_stats ORDER BY day").fetchall()


def upgrade(path: Path) -> tuple[int, int]:
    if not path.is_file():
        raise ValueError("database missing")
    ro = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
    try:
        old_version = ro.execute(
            "SELECT v FROM meta WHERE k='schema_version'"
        ).fetchone()
        if old_version != ("2",):
            raise ValueError("only schema v2 is supported")
        before = totals(ro)
        table = ro.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='ais_intake_day'"
        ).fetchone()
        if table:
            raise ValueError("AIS intake instrumentation already exists")
    finally:
        ro.close()

    conn = open_db(path)
    try:
        if conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise ValueError("SQLite integrity failed")
        if totals(conn) != before:
            raise ValueError("existing daily statistics changed")
        if conn.execute(
            "SELECT v FROM meta WHERE k='schema_version'"
        ).fetchone() != ("2",):
            raise ValueError("schema version changed unexpectedly")
        columns = {r[1] for r in conn.execute("PRAGMA table_info(ais_intake_day)")}
        if set(COLUMNS) - columns:
            raise ValueError("missing AIS diagnostics columns")
        start = conn.execute(
            "SELECT v FROM meta WHERE k='ais_intake_tracking_since'"
        ).fetchone()
        if not start:
            raise ValueError("instrumentation start marker missing")
        if daily_report(conn):
            raise ValueError("old AIS data were incorrectly backfilled")
    finally:
        conn.close()
    return len(before), len(STABLE)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", type=Path, required=True)
    args = ap.parse_args()
    try:
        days, columns = upgrade(args.db)
    except (OSError, sqlite3.Error, ValueError):
        print("FAIL: AIS intake extension validation failed.", file=sys.stderr)
        raise SystemExit(1) from None
    print(f"PASS: AIS intake counters installed; {days} historical day(s), "
          f"{columns} existing daily counters preserved.")
    print("Historical frame rejection counts remain unknown (no fabricated backfill).")
