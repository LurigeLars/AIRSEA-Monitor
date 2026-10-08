"""Read-only, compact coverage report, never a trade recommendation."""
import argparse
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from pilot_core import open_db, started_at, summary

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", type=Path, required=True)
    args = ap.parse_args()
    if not args.db.exists():
        raise SystemExit("Pilot has not started: database absent")
    # Never mutate or migrate the live database while reporting.
    conn = sqlite3.connect(args.db.resolve().as_uri() + "?mode=ro", uri=True)
    version = conn.execute("SELECT v FROM meta WHERE k='schema_version'").fetchone()
    if not version or int(version[0]) < 2:
        conn.close()
        raise SystemExit("AIS zone counters unavailable in schema v1; migrate an offline copy first.")
    inception = conn.execute(
        "SELECT v FROM meta WHERE k='ais_zone_tracking_since'"
    ).fetchone()
    start = started_at(conn)
    rows = summary(conn)
    print("SHADOW PILOT (no signals, orders, or notifications)")
    print("Started UTC:", start.isoformat())
    print("Scheduled end UTC:", (start + timedelta(days=14)).isoformat())
    print("Observed days:", len(rows))
    print("Zone counters start:", inception[0] if inception else "unknown")
    print("Zone counters are partial on the migration day; pre-upgrade positions cannot be reconstructed.")
    print("Technical coverage only; no confirmed oil cargo volumes or military-intent signals.")
    for row in rows:
        day_start = datetime.fromisoformat(row['day']+'T00:00:00+00:00')
        obs_begin = max(day_start, start)
        obs_end = min(day_start+timedelta(days=1),datetime.now(timezone.utc), start+timedelta(days=14))
        possible = max(1, (obs_end-obs_begin).total_seconds())
        coverage = min(100.0, 100.0 * row['connected_seconds'] / possible)
        print(f"{row['day']}: AIS stream connected {coverage:.1f}% of day; "
              f"received {row['ais_positions']} positional messages; "
              f"zone W/M/E positions {row['zone_w_positions']}/{row['zone_m_positions']}/{row['zone_e_positions']}; "
              f"zone W/M/E unique vessels {row['zone_w_vessels']}/{row['zone_m_vessels']}/{row['zone_e_vessels']}; "
              f"coarse all-vessel transitions {row['crossings_all']}; "
              f"tanker-confirmed transitions {row['crossings_tanker_typed']} (lower bound); "
              f"ADSB snapshots {row['adsb_snapshots']} (mean {row['adsb_mean_airborne']}); "
              f"OpenSky {row['opensky_snapshots']} (mean {row['opensky_mean_airborne']}); "
              f"source errors {row['disconnects']+row['adsb_errors']+row['opensky_errors']}")
    conn.close()
