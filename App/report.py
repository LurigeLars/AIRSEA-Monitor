"""Read-only, compact coverage report, never a trade recommendation."""
import argparse
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from pilot_core import started_at, summary
from ais_diagnostics import daily_report, REJECTION_COLUMNS

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
    has_intake = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='ais_intake_day'"
    ).fetchone() is not None
    intake_by_day = daily_report(conn) if has_intake else {}
    intake_start = conn.execute(
        "SELECT v FROM meta WHERE k='ais_intake_tracking_since'"
    ).fetchone() if has_intake else None
    start = started_at(conn)
    rows = summary(conn)
    print("SHADOW PILOT (no signals, orders, or notifications)")
    print("Started UTC:", start.isoformat())
    print("Scheduled end UTC:", (start + timedelta(days=14)).isoformat())
    print("Observed days:", len(rows))
    print("Zone counters start:", inception[0] if inception else "unknown")
    print("Zone counters are partial on the migration day; pre-upgrade positions cannot be reconstructed.")
    print("AIS intake instrumentation since:",
          intake_start[0] if intake_start else "not installed")
    print("Intake counts exclude subscription handshake; earlier days are UNKNOWN, not zero.")
    print("Technical coverage only; no confirmed oil cargo volumes or military-intent signals.")
    for row in rows:
        day_start = datetime.fromisoformat(row['day']+'T00:00:00+00:00')
        obs_begin = max(day_start, start)
        obs_end = min(day_start+timedelta(days=1),datetime.now(timezone.utc), start+timedelta(days=14))
        possible = max(1, (obs_end-obs_begin).total_seconds())
        coverage = min(100.0, 100.0 * row['connected_seconds'] / possible)
        print(f"{row['day']}: AIS stream connected {coverage:.1f}% of day; "
              f"accepted {row['ais_positions']} positional messages (legacy total); "
              f"zone W/M/E positions {row['zone_w_positions']}/{row['zone_m_positions']}/{row['zone_e_positions']}; "
              f"zone W/M/E unique vessels {row['zone_w_vessels']}/{row['zone_m_vessels']}/{row['zone_e_vessels']}; "
              f"coarse all-vessel transitions {row['crossings_all']}; "
              f"tanker-confirmed transitions {row['crossings_tanker_typed']} (lower bound); "
              f"ADSB snapshots {row['adsb_snapshots']} (mean {row['adsb_mean_airborne']}); "
              f"OpenSky {row['opensky_snapshots']} (mean {row['opensky_mean_airborne']}); "
              f"source errors {row['disconnects']+row['adsb_errors']+row['opensky_errors']}")
        diag = intake_by_day.get(row["day"])
        if diag is None:
            print("  AIS intake: not instrumented for this day or no post-installation frames.")
            continue
        accepted = diag["accepted_positions"] + diag["accepted_static"]
        rejected = sum(diag[key] for key in REJECTION_COLUMNS)
        errors = diag["processing_errors"]
        received = diag["frames_received"]
        reasons = ", ".join(
            key.removeprefix("rejected_") + "=" + str(diag[key])
            for key in REJECTION_COLUMNS if diag[key]
        ) or "none"
        balanced = received == accepted + rejected + errors
        print(f"  AIS intake: frames={received}; accepted={accepted} "
              f"(positions={diag['accepted_positions']}, static={diag['accepted_static']}); "
              f"rejected={rejected}; processing_errors={errors}; reasons: {reasons}; "
              f"accounting={'OK' if balanced else 'INCONSISTENT'}")
    conn.close()
