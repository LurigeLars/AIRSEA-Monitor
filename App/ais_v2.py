"""Privacy-preserving zone statistics and crossing state, schema v2.

No raw MMSIs or AIS coordinates are stored. Geographic plausibility uses only
the minimum distance between the two disjoint longitude zones.
"""
from __future__ import annotations

import hashlib
import math
import sqlite3
from datetime import datetime, timedelta, timezone

MAX_GAP_SECONDS = 6 * 3600
MIN_GAP_SECONDS = 4 * 60
MAX_SPEED_KNOTS = 40
ZONE_STAT_COLUMNS = {"W": ("ais_zone_w", "ais_vessels_w"),
                     "M": ("ais_zone_m", "ais_vessels_m"),
                     "E": ("ais_zone_e", "ais_vessels_e")}
SCHEMA_COLUMNS = (
    "ais_zone_w", "ais_zone_m", "ais_zone_e",
    "ais_vessels_w", "ais_vessels_m", "ais_vessels_e",
)


def _inc(conn: sqlite3.Connection, column: str, day: str, value: int = 1) -> None:
    allowed = {"ais_position_messages", "ais_static_messages",
               "ais_crossings_all", "ais_crossings_tanker", *SCHEMA_COLUMNS}
    if column not in allowed:
        raise ValueError("unsupported counter")
    conn.execute("INSERT OR IGNORE INTO day_stats(day) VALUES(?)", (day,))
    conn.execute(f"UPDATE day_stats SET {column} = {column} + ? WHERE day = ?",
                 (value, day))


def migrate(conn: sqlite3.Connection, now: datetime) -> None:
    """Idempotent additive migration from v1; NEVER retroactively invent zone counts."""
    version_row = conn.execute("SELECT v FROM meta WHERE k='schema_version'").fetchone()
    old_version = int(version_row[0]) if version_row else 1
    if old_version > 2:
        raise ValueError("database schema is newer than this application")

    # Transactional DDL. Any failure rolls back the additions and version marker.
    try:
        conn.execute("BEGIN IMMEDIATE")
        columns = {r[1] for r in conn.execute("PRAGMA table_info(day_stats)")}
        for name in SCHEMA_COLUMNS:
            if name not in columns:
                conn.execute(f"ALTER TABLE day_stats ADD COLUMN {name} INTEGER NOT NULL DEFAULT 0")
        crossing_cols = {r[1] for r in conn.execute("PRAGMA table_info(counted_crossing)")}
        if "tanker_counted" not in crossing_cols:
            conn.execute("ALTER TABLE counted_crossing ADD COLUMN tanker_counted INTEGER NOT NULL DEFAULT 0")
            conn.execute("""
                UPDATE counted_crossing SET tanker_counted = 1 WHERE EXISTS (
                    SELECT 1 FROM vessel_day v
                    WHERE v.day = counted_crossing.day
                      AND v.vessel_hash = counted_crossing.vessel_hash
                      AND v.ship_type BETWEEN 80 AND 89
                )
            """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS vessel_state (
                vessel_hash TEXT PRIMARY KEY,
                ship_type INTEGER, last_zone TEXT,
                side_seen TEXT, last_seen TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS zone_vessel_day (
                day TEXT NOT NULL, vessel_hash TEXT NOT NULL, zone TEXT NOT NULL,
                PRIMARY KEY(day, vessel_hash, zone)
            )
        """)
        if old_version < 2:
            # Legacy day-keyed states can be carried forward without historical
            # positions; last_seen provides an approximate latest-side anchor.
            cutoff = (now - timedelta(days=15)).date().isoformat()
            legacy = conn.execute("""
                SELECT day, vessel_hash, ship_type, last_zone, last_seen
                FROM vessel_day WHERE day >= ? ORDER BY day, last_seen
            """, (cutoff,)).fetchall()
            for day, vessel, kind, side, last_seen in legacy:
                seen = last_seen or day + "T00:00:00+00:00"
                conn.execute("""
                    INSERT INTO vessel_state(vessel_hash,ship_type,last_zone,side_seen,last_seen)
                    VALUES(?,?,?,?,?)
                    ON CONFLICT(vessel_hash) DO UPDATE SET
                        ship_type=excluded.ship_type,
                        last_zone=excluded.last_zone,
                        side_seen=excluded.side_seen,
                        last_seen=excluded.last_seen
                    WHERE excluded.last_seen > vessel_state.last_seen
                """, (vessel, kind, side, seen if side in ("W", "E") else None, seen))
            conn.execute("INSERT OR IGNORE INTO meta(k,v) VALUES('ais_zone_tracking_since',?)",
                         (now.astimezone(timezone.utc).isoformat(timespec="seconds"),))
        conn.execute("""
            INSERT INTO meta(k,v) VALUES('schema_version','2')
            ON CONFLICT(k) DO UPDATE SET v='2'
        """)
        conn.commit()
    except BaseException:
        conn.rollback()
        raise


def process_observation(conn: sqlite3.Connection, parsed: dict, salt: bytes,
                        now: datetime, zone: str | None, min_crossing_nm: float) -> bool:
    if now.tzinfo is None:
        raise ValueError("AIS observations require timezone-aware timestamps")
    now = now.astimezone(timezone.utc)
    stamp = now.isoformat(timespec="seconds")
    day = now.date().isoformat()
    vessel = hashlib.sha256(salt + parsed["mmsi"].encode("ascii")).hexdigest()[:32]
    coords = parsed["coords"]
    old = conn.execute("""
        SELECT ship_type,last_zone,side_seen,last_seen
        FROM vessel_state WHERE vessel_hash=?
    """, (vessel,)).fetchone()
    old_type, last_side, side_seen, previous_stamp = old if old else (None, None, None, None)

    # Arrival order is used as a proxy for report order; do not roll state back.
    if previous_stamp is not None:
        prev_time = datetime.fromisoformat(previous_stamp.replace("Z", "+00:00"))
        if now <= prev_time:
            return False

    kind = parsed["ship_type"] if parsed["ship_type"] is not None else old_type
    is_tanker = kind is not None and 80 <= int(kind) <= 89
    had_tanker = old_type is not None and 80 <= int(old_type) <= 89

    if coords is not None:
        if zone is None:
            return False
        _, _, sog = coords
        _inc(conn, "ais_position_messages", day)
        messages, vessels = ZONE_STAT_COLUMNS[zone]
        _inc(conn, messages, day)
        added = conn.execute("""
            INSERT OR IGNORE INTO zone_vessel_day(day,vessel_hash,zone)
            VALUES(?,?,?)
        """, (day, vessel, zone)).rowcount
        if added:
            _inc(conn, vessels, day)
    else:
        _inc(conn, "ais_static_messages", day)

    # Preserve legacy per-day classification/reporting state.
    daily_row = conn.execute(
        "SELECT ship_type FROM vessel_day WHERE day=? AND vessel_hash=?",
        (day, vessel)).fetchone()
    if daily_row is None:
        conn.execute("""
            INSERT INTO vessel_day(day,vessel_hash,ship_type,last_zone,zone_since,last_seen)
            VALUES(?,?,?,?,?,?)
        """, (day, vessel, kind, last_side, side_seen, stamp))
    else:
        conn.execute("""
            UPDATE vessel_day SET ship_type=?,last_seen=?
            WHERE day=? AND vessel_hash=?
        """, (kind, stamp, day, vessel))

    if is_tanker and not had_tanker:
        # A late type message may confirm older, already-counted crossings.
        missing = conn.execute("""
            SELECT day, direction FROM counted_crossing
            WHERE vessel_hash=? AND tanker_counted=0
        """, (vessel,)).fetchall()
        for crossing_day, direction in missing:
            conn.execute("""
                UPDATE counted_crossing SET tanker_counted=1
                WHERE day=? AND vessel_hash=? AND direction=? AND tanker_counted=0
            """, (crossing_day, vessel, direction))
            _inc(conn, "ais_crossings_tanker", crossing_day)

    if coords is not None:
        _, _, sog = coords
        if zone in ("W", "E"):
            if last_side is None or side_seen is None:
                last_side, side_seen = zone, stamp
            elif last_side == zone:
                # Most recent observation on that side, NOT original arrival time.
                side_seen = stamp
            else:
                delta = (now - datetime.fromisoformat(
                    side_seen.replace("Z", "+00:00"))).total_seconds()
                implied_min_speed = min_crossing_nm * 3600 / delta if delta > 0 else float("inf")
                if delta > MAX_GAP_SECONDS:
                    # Unknown journey during AIS outage; resync without a count.
                    last_side, side_seen = zone, stamp
                elif (MIN_GAP_SECONDS <= delta <= MAX_GAP_SECONDS
                      and 1 <= sog <= MAX_SPEED_KNOTS
                      and implied_min_speed <= MAX_SPEED_KNOTS):
                    direction = last_side + zone
                    prior = conn.execute("""
                        SELECT 1 FROM counted_crossing
                        WHERE day=? AND vessel_hash=? AND direction=?
                    """, (day, vessel, direction)).fetchone()
                    if prior is None:
                        conn.execute("""
                            INSERT INTO counted_crossing(day,vessel_hash,direction,tanker_counted)
                            VALUES(?,?,?,?)
                        """, (day, vessel, direction, int(is_tanker)))
                        _inc(conn, "ais_crossings_all", day)
                        if is_tanker:
                            _inc(conn, "ais_crossings_tanker", day)
                    last_side, side_seen = zone, stamp
                # Reject low-SOG/too-fast observations without replacing confirmed side.
        conn.execute("""
            UPDATE vessel_day SET last_zone=?,zone_since=?,
                ship_type=?,last_seen=? WHERE day=? AND vessel_hash=?
        """, (last_side, side_seen, kind, stamp, day, vessel))

    conn.execute("""
        INSERT INTO vessel_state(vessel_hash,ship_type,last_zone,side_seen,last_seen)
        VALUES(?,?,?,?,?)
        ON CONFLICT(vessel_hash) DO UPDATE SET
            ship_type=excluded.ship_type,
            last_zone=excluded.last_zone,
            side_seen=excluded.side_seen,
            last_seen=excluded.last_seen
    """, (vessel, kind, last_side, side_seen, stamp))
    return True


def prune(conn: sqlite3.Connection, current: datetime) -> None:
    cutoff_day = (current - timedelta(days=15)).date().isoformat()
    cutoff_stamp = (current - timedelta(days=15)).astimezone(timezone.utc).isoformat()
    conn.execute("DELETE FROM vessel_state WHERE last_seen < ?", (cutoff_stamp,))
    conn.execute("DELETE FROM zone_vessel_day WHERE day < ?", (cutoff_day,))
