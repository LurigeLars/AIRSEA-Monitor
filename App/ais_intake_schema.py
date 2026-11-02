"""Storage schema for aggregate-only AIS intake diagnostics.

Kept separate from pilot_core to avoid a cyclic dependency.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

COLUMNS = (
    "frames_received",
    "accepted_positions",
    "accepted_static",
    "rejected_bad_json",
    "rejected_non_object",
    "rejected_unsupported",
    "rejected_structure",
    "rejected_identifier",
    "rejected_position",
    "rejected_outside_box",
    "rejected_duplicate_stale",
    "processing_errors",
)

def ensure_schema(conn: sqlite3.Connection, now: datetime | None = None) -> None:
    """Idempotent, no backfill and no changes to existing AIS counters."""
    now = now or datetime.now(timezone.utc)
    cols = ",\n".join(f"{name} INTEGER NOT NULL DEFAULT 0 CHECK({name}>=0)" for name in COLUMNS)
    conn.execute(f"""
        CREATE TABLE IF NOT EXISTS ais_intake_day (
            day TEXT PRIMARY KEY,
            {cols}
        )
    """)
    conn.execute(
        "INSERT OR IGNORE INTO meta(k,v) VALUES('ais_intake_tracking_since',?)",
        (now.astimezone(timezone.utc).isoformat(timespec="seconds"),),
    )
    conn.commit()


