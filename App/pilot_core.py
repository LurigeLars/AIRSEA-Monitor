"""Aggregation primitives for a non-alerting, civilian-traffic data quality pilot.

No raw vessel identifiers, flight identifiers or positional histories are persisted.
"""
from __future__ import annotations

import hashlib
import math
import sqlite3
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1
# Broad chokepoint sampling region. Not a navigation or operational predictor.
AIS_BOX = [[[27.25, 55.05], [25.15, 58.15]]]
# Coarse two-zone observation; no vessel paths are stored.
WEST_LIMIT, EAST_LIMIT = 56.35, 56.95
AIS_LAT_MIN, AIS_LAT_MAX = 25.15, 27.25
AIS_LON_MIN, AIS_LON_MAX = 55.05, 58.15
FLIGHT_CENTER = (26.0, 56.5)
FLIGHT_RADIUS_NM = 125
OPENSKY_BOUNDS = (24.5, 54.2, 27.4, 58.8)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso_now() -> str:
    return utcnow().isoformat(timespec="seconds")


def parse_dt(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def day_key(now: datetime | None = None) -> str:
    return (now or utcnow()).strftime("%Y-%m-%d")


def fingerprint(mmsi: int | str, salt: bytes) -> str:
    """Salted, irreversible pseudonym; no original MMSI or ship name stored."""
    return hashlib.sha256(salt + str(mmsi).encode("ascii")).hexdigest()[:32]


def tanker_code(code: int | str | None) -> bool:
    try:
        return 80 <= int(code) <= 89
    except (TypeError, ValueError):
        return False


def zone_for(lat: float, lon: float) -> str | None:
    if not (AIS_LAT_MIN <= lat <= AIS_LAT_MAX and AIS_LON_MIN <= lon <= AIS_LON_MAX):
        return None
    if lon <= WEST_LIMIT:
        return "W"
    if lon >= EAST_LIMIT:
        return "E"
    return "M"


def extract_ais(message: dict[str, Any]) -> dict[str, Any] | None:
    kind = message.get("MessageType")
    if kind not in {"PositionReport", "ShipStaticData", "StandardClassBPositionReport", "ExtendedClassBPositionReport", "StaticDataReport"}:
        return None
    payload = message.get("Message")
    if not isinstance(payload, dict):
        return None
    row = payload.get(kind)
    if not isinstance(row, dict):
        return None
    mmsi = row.get("UserID") or (message.get("MetaData") or {}).get("MMSI")
    if not isinstance(mmsi, (str, int)) or not str(mmsi).isdigit() or not 100000000 <= int(mmsi) <= 999999999:
        return None
    if kind == "ShipStaticData":
        ship_type = row.get("Type")
    elif kind == "StaticDataReport":
        ship_type = (row.get("ReportB") or {}).get("ShipType")
    elif kind == "ExtendedClassBPositionReport":
        ship_type = row.get("Type")
    else:
        ship_type = None
    if ship_type is not None:
        try:
            ship_type = int(ship_type)
        except (ValueError, TypeError):
            ship_type = None
    coords = None
    if kind in {"PositionReport", "StandardClassBPositionReport", "ExtendedClassBPositionReport"}:
        try:
            lat, lon, sog = float(row["Latitude"]), float(row["Longitude"]), float(row.get("Sog", 0))
            if not all(map(math.isfinite, (lat, lon, sog))) or not (-90 <= lat <= 90 and -180 <= lon <= 180):
                return None
            coords = (lat, lon, sog)
        except (KeyError, TypeError, ValueError):
            return None
    return {"mmsi": str(mmsi), "ship_type": ship_type, "coords": coords}


def adsb_count(payload: dict[str, Any]) -> int:
    """Count valid, airborne observed positions only. Not guaranteed commercial/civilian."""
    aircraft = payload.get("ac")
    if not isinstance(aircraft, list):
        raise ValueError("ADSB response missing ac list")
    valid = 0
    for ac in aircraft:
        if not isinstance(ac, dict):
            continue
        try:
            lat = float(ac["lat"])
            lon = float(ac["lon"])
            if not all(map(math.isfinite, (lat, lon))) or not (-90 <= lat <= 90 and -180 <= lon <= 180):
                continue
        except (KeyError, TypeError, ValueError):
            continue
        if ac.get("alt_baro") == "ground":
            continue
        valid += 1
    return valid


def opensky_count(payload: dict[str, Any]) -> int:
    states = payload.get("states")
    if states is None:
        return 0
    if not isinstance(states, list):
        raise ValueError("OpenSky response missing states array")
    return sum(1 for state in states if isinstance(state, list) and len(state) > 8 and state[5] is not None and state[6] is not None and state[8] is False)


def open_db(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=15)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=15000")
    conn.executescript("""
      CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT NOT NULL);
      CREATE TABLE IF NOT EXISTS day_stats (
        day TEXT PRIMARY KEY, ais_position_messages INTEGER NOT NULL DEFAULT 0,
        ais_static_messages INTEGER NOT NULL DEFAULT 0,
        ais_crossings_all INTEGER NOT NULL DEFAULT 0,
        ais_crossings_tanker INTEGER NOT NULL DEFAULT 0,
        ais_connected_seconds REAL NOT NULL DEFAULT 0,
        ais_disconnects INTEGER NOT NULL DEFAULT 0,
        adsb_ok INTEGER NOT NULL DEFAULT 0, adsb_errors INTEGER NOT NULL DEFAULT 0,
        opensky_ok INTEGER NOT NULL DEFAULT 0, opensky_errors INTEGER NOT NULL DEFAULT 0
      );
      CREATE TABLE IF NOT EXISTS vessel_day (
        day TEXT NOT NULL, vessel_hash TEXT NOT NULL, ship_type INTEGER,
        last_zone TEXT, zone_since TEXT, last_seen TEXT,
        PRIMARY KEY (day, vessel_hash)
      );
      CREATE TABLE IF NOT EXISTS counted_crossing (
        day TEXT NOT NULL, vessel_hash TEXT NOT NULL, direction TEXT NOT NULL,
        PRIMARY KEY (day, vessel_hash, direction)
      );
      CREATE TABLE IF NOT EXISTS flight_snapshot (
        utc TEXT NOT NULL, provider TEXT NOT NULL, observed_airborne INTEGER NOT NULL,
        PRIMARY KEY (utc, provider)
      );
      CREATE TABLE IF NOT EXISTS source_error (
        utc TEXT NOT NULL, provider TEXT NOT NULL, kind TEXT NOT NULL
      );
    """)
    conn.execute("INSERT OR IGNORE INTO meta(k,v) VALUES('schema_version',?)", (str(SCHEMA_VERSION),))
    conn.execute("INSERT OR IGNORE INTO meta(k,v) VALUES('pilot_start_utc',?)", (iso_now(),))
    conn.commit()
    return conn


def inc(conn: sqlite3.Connection, col: str, value: float = 1, day: str | None = None):
    if col not in {"ais_position_messages", "ais_static_messages", "ais_crossings_all", "ais_crossings_tanker", "ais_connected_seconds", "ais_disconnects", "adsb_ok", "adsb_errors", "opensky_ok", "opensky_errors"}:
        raise ValueError("unsupported stat")
    d = day or day_key()
    conn.execute("INSERT OR IGNORE INTO day_stats(day) VALUES(?)", (d,))
    conn.execute(f"UPDATE day_stats SET {col} = {col} + ? WHERE day = ?", (value, d))


def log_error(conn: sqlite3.Connection, source: str, exc: Exception):
    kind = type(exc).__name__ # Never persist exception text (may include secrets/URLs).
    inc(conn, f"{source}_errors") if source in {"adsb", "opensky"} else inc(conn, "ais_disconnects")
    conn.execute("INSERT INTO source_error(utc,provider,kind) VALUES(?,?,?)", (iso_now(), source, kind))
    conn.commit()


def process_ais(conn: sqlite3.Connection, raw: dict[str, Any], salt: bytes, now: datetime | None = None) -> bool:
    parsed = extract_ais(raw)
    if parsed is None:
        return False
    now = now or utcnow()
    today, nowstr = day_key(now), now.isoformat(timespec="seconds")
    pseudonym = fingerprint(parsed["mmsi"], salt)
    coords = parsed["coords"]
    if coords is not None:
        lat, lon, sog = coords
        zone = zone_for(lat, lon)
        if zone is None:
            return False
        inc(conn, "ais_position_messages", day=today)
    else:
        inc(conn, "ais_static_messages", day=today)
        zone, sog = None, None
    row = conn.execute("SELECT ship_type,last_zone,zone_since,last_seen FROM vessel_day WHERE day=? AND vessel_hash=?", (today, pseudonym)).fetchone()
    prior_type, prev_zone, zone_since, last_seen = row if row else (None, None, None, None)
    kind = parsed["ship_type"] if parsed["ship_type"] is not None else prior_type
    if coords is None:
        if tanker_code(kind) and not tanker_code(prior_type):
            # Static type can arrive AFTER a coarse crossing; repair lower-bound classification.
            existing = conn.execute("SELECT COUNT(*) FROM counted_crossing WHERE day=? AND vessel_hash=?", (today,pseudonym)).fetchone()[0]
            if existing:
                inc(conn, "ais_crossings_tanker", value=existing, day=today)
        if row is None:
            conn.execute("INSERT INTO vessel_day(day,vessel_hash,ship_type) VALUES(?,?,?)", (today,pseudonym,kind))
        elif parsed["ship_type"] is not None:
            conn.execute("UPDATE vessel_day SET ship_type=? WHERE day=? AND vessel_hash=?", (kind,today,pseudonym))
        return True
    # Report crossing only when moving between coarse disjoint sides, 4min..6h apart.
    crossed = False
    if (prev_zone in {"W", "E"} and zone in {"W", "E"} and prev_zone != zone
            and zone_since and 1 <= sog <= 40):
        previous = parse_dt(zone_since)
        delta = (now-previous).total_seconds() if previous else 0
        crossed = 240 <= delta <= 21600
    if crossed:
        direction = prev_zone + zone
        already = conn.execute("SELECT 1 FROM counted_crossing WHERE day=? AND vessel_hash=? AND direction=?", (today,pseudonym,direction)).fetchone()
        if not already:
            conn.execute("INSERT INTO counted_crossing VALUES(?,?,?)", (today,pseudonym,direction))
            inc(conn,"ais_crossings_all",day=today)
            if tanker_code(kind):
                inc(conn,"ais_crossings_tanker",day=today)
    if zone not in {"W", "E"}:
        new_zone, new_zone_since = prev_zone, zone_since
    elif zone != prev_zone:
        new_zone, new_zone_since = zone, nowstr
    else:
        new_zone, new_zone_since = prev_zone, zone_since
    conn.execute("""INSERT INTO vessel_day(day,vessel_hash,ship_type,last_zone,zone_since,last_seen)
      VALUES(?,?,?,?,?,?) ON CONFLICT(day,vessel_hash) DO UPDATE SET
      ship_type=excluded.ship_type,last_zone=excluded.last_zone,zone_since=excluded.zone_since,last_seen=excluded.last_seen""",
      (today,pseudonym,kind,new_zone,new_zone_since,nowstr))
    return True


def prune_temp(conn: sqlite3.Connection, current: datetime | None = None):
    """Discard hashed per-vessel state after 15 days, retaining aggregate day stats."""
    current = current or utcnow()
    cutoff = (current-timedelta(days=15)).strftime("%Y-%m-%d")
    conn.execute("DELETE FROM vessel_day WHERE day < ?",(cutoff,))
    conn.execute("DELETE FROM counted_crossing WHERE day < ?",(cutoff,))
    conn.commit()


def record_flight_count(conn: sqlite3.Connection, provider: str, count: int, now: datetime | None = None):
    if provider not in {"adsb", "opensky"}:
        raise ValueError("unknown provider")
    now = now or utcnow()
    stamp = now.isoformat(timespec="seconds")
    conn.execute("INSERT OR REPLACE INTO flight_snapshot(utc,provider,observed_airborne) VALUES(?,?,?)",(stamp,provider,count))
    inc(conn,f"{provider}_ok",day=day_key(now))
    conn.commit()


def started_at(conn: sqlite3.Connection) -> datetime:
    row = conn.execute("SELECT v FROM meta WHERE k='pilot_start_utc'").fetchone()
    start = parse_dt(row[0]) if row else None
    if start is None:
        raise ValueError("start timestamp missing")
    return start


def summary(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    # Type coverage includes static-only rows; interpret tanker data as LOWER BOUND.
    rows = conn.execute("""SELECT s.day,s.ais_position_messages,s.ais_static_messages,
      s.ais_crossings_all,s.ais_crossings_tanker,s.ais_connected_seconds,s.ais_disconnects,
      s.adsb_ok,s.adsb_errors,s.opensky_ok,s.opensky_errors,
      (SELECT COUNT(*) FROM vessel_day v WHERE v.day=s.day) AS distinct_known,
      (SELECT COUNT(*) FROM vessel_day v WHERE v.day=s.day AND v.ship_type IS NOT NULL) AS typed,
      (SELECT COUNT(*) FROM vessel_day v WHERE v.day=s.day AND v.ship_type BETWEEN 80 AND 89) AS tankers,
      (SELECT ROUND(AVG(f.observed_airborne),1) FROM flight_snapshot f WHERE substr(f.utc,1,10)=s.day AND f.provider='adsb') AS adsb_mean,
      (SELECT ROUND(AVG(f.observed_airborne),1) FROM flight_snapshot f WHERE substr(f.utc,1,10)=s.day AND f.provider='opensky') AS opensky_mean
      FROM day_stats s ORDER BY s.day""").fetchall()
    names=("day","ais_positions","ais_static","crossings_all","crossings_tanker_typed","connected_seconds","disconnects","adsb_snapshots","adsb_errors","opensky_snapshots","opensky_errors","distinct_vessels_in_retention","typed_vessels_in_retention","tanker_vessels_in_retention","adsb_mean_airborne","opensky_mean_airborne")
    return [dict(zip(names,row)) for row in rows]
