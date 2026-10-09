"""Aggregate-only AIS intake diagnostics.

Count *post-handshake* websocket frames and explain why a frame did not
contribute an accepted observation. Never store source payloads, vessel IDs,
coordinates, raw errors, endpoint responses or credentials.

This is an additive schema-v2 extension, deliberately compatible with v2
collectors for straightforward rollback.
"""
from __future__ import annotations

import json
import math
import sqlite3
from datetime import datetime, timezone
from typing import Any

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
REJECTION_COLUMNS = tuple(x for x in COLUMNS if x.startswith("rejected_"))
POSITION_TYPES = frozenset({
    "PositionReport", "StandardClassBPositionReport",
    "ExtendedClassBPositionReport",
})
STATIC_TYPES = frozenset({"ShipStaticData", "StaticDataReport"})
SUPPORTED_TYPES = POSITION_TYPES | STATIC_TYPES


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


def increment(conn: sqlite3.Connection, day: str, field: str) -> None:
    if field not in COLUMNS:
        raise ValueError("unsupported AIS intake diagnostic")
    conn.execute("INSERT OR IGNORE INTO ais_intake_day(day) VALUES(?)", (day,))
    conn.execute(
        f"UPDATE ais_intake_day SET {field}={field}+1 WHERE day=?",
        (day,),
    )


def rejected_reason(obj: Any) -> str:
    """Finite, data-free reason; only called when process_ais rejected a dict."""
    if not isinstance(obj, dict):
        return "rejected_non_object"
    kind = obj.get("MessageType")
    if not isinstance(kind, str) or kind not in SUPPORTED_TYPES:
        return "rejected_unsupported"
    message = obj.get("Message")
    if not isinstance(message, dict):
        return "rejected_structure"
    row = message.get(kind)
    if not isinstance(row, dict):
        return "rejected_structure"
    meta = obj.get("MetaData")
    if meta is not None and not isinstance(meta, dict):
        return "rejected_structure"
    mmsi = row.get("UserID") or (meta or {}).get("MMSI")
    if not isinstance(mmsi, (str, int)) or not str(mmsi).isdigit():
        return "rejected_identifier"
    if len(str(mmsi)) != 9 or not (100000000 <= int(mmsi) <= 999999999):
        return "rejected_identifier"
    if kind in POSITION_TYPES:
        try:
            lat, lon = float(row["Latitude"]), float(row["Longitude"])
            sog = float(row.get("Sog", 0))
        except (KeyError, ValueError, TypeError, OverflowError):
            return "rejected_position"
        if not all(math.isfinite(x) for x in (lat, lon, sog)):
            return "rejected_position"
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            return "rejected_position"
        # Use the same bounds as the core parser, without persisting coordinates.
        from pilot_core import zone_for
        if zone_for(lat, lon) is None:
            return "rejected_outside_box"
    return "rejected_duplicate_stale"


def ingest_frame(conn: sqlite3.Connection, frame: str | bytes, salt: bytes,
                 now: datetime | None = None) -> str:
    """Record one post-handshake frame and its outcome atomically.

    Return only a fixed diagnostic category. Processing exceptions propagate
    so the connection can be re-established, but a counted processing error
    remains. Never include frame content in exceptions or logs.
    """
    from pilot_core import process_ais, utcnow

    now = now or utcnow()
    if now.tzinfo is None:
        raise ValueError("timezone-aware timestamp required")
    day = now.astimezone(timezone.utc).date().isoformat()
    increment(conn, day, "frames_received")
    try:
        payload = json.loads(frame)
    except (json.JSONDecodeError, UnicodeDecodeError, TypeError, ValueError):
        increment(conn, day, "rejected_bad_json")
        return "rejected_bad_json"
    if not isinstance(payload, dict):
        increment(conn, day, "rejected_non_object")
        return "rejected_non_object"

    # Discard known invalid data before invoking the state machine. This also
    # protects it from provider JSON structures it does not support.
    preliminary = rejected_reason(payload)
    if preliminary != "rejected_duplicate_stale":
        increment(conn, day, preliminary)
        return preliminary

    # Protect existing aggregate, per-vessel and crossing tables against
    # partial writes on an unexpected processing failure.
    conn.execute("SAVEPOINT ais_frame_processing")
    try:
        ok = process_ais(conn, payload, salt, now)
    except Exception:
        conn.execute("ROLLBACK TO SAVEPOINT ais_frame_processing")
        conn.execute("RELEASE SAVEPOINT ais_frame_processing")
        increment(conn, day, "processing_errors")
        raise
    else:
        conn.execute("RELEASE SAVEPOINT ais_frame_processing")
    if ok:
        category = ("accepted_positions"
                    if payload.get("MessageType") in POSITION_TYPES
                    else "accepted_static")
    else:
        category = rejected_reason(payload)
    increment(conn, day, category)
    return category


def daily_report(conn: sqlite3.Connection) -> dict[str, dict[str, int]]:
    rows = conn.execute(
        "SELECT day," + ",".join(COLUMNS) +
        " FROM ais_intake_day ORDER BY day"
    ).fetchall()
    return {
        row[0]: dict(zip(COLUMNS, (int(x) for x in row[1:])))
        for row in rows
    }
