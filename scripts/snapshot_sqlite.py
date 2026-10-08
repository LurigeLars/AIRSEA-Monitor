"""Create and verify a consistent SQLite backup without writing to the source."""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path


def snapshot(source_path: Path, target_path: Path) -> tuple[str, int]:
    if not source_path.is_file() or target_path.exists():
        raise ValueError("invalid source or existing backup destination")
    target_path.parent.mkdir(parents=True, exist_ok=True)
    source = sqlite3.connect(source_path.resolve().as_uri() + "?mode=ro", uri=True, timeout=20)
    try:
        source.execute("PRAGMA busy_timeout=20000")
        if source.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise ValueError("source database integrity check failed")
        version = source.execute(
            "SELECT v FROM meta WHERE k='schema_version'"
        ).fetchone()
        version_text = version[0] if version else "1"
        target = sqlite3.connect(target_path)
        try:
            source.backup(target, pages=128, sleep=0.1)
            if target.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise ValueError("backup database integrity check failed")
            backup_version = target.execute(
                "SELECT v FROM meta WHERE k='schema_version'"
            ).fetchone()
            if backup_version != version:
                raise ValueError("backup schema version differs")
            days = target.execute("SELECT COUNT(*) FROM day_stats").fetchone()[0]
        finally:
            target.close()
        return version_text, days
    finally:
        source.close()


if __name__ == "__main__":
    args = argparse.ArgumentParser()
    args.add_argument("--db", type=Path, required=True)
    args.add_argument("--dest", type=Path, required=True)
    opt = args.parse_args()
    try:
        version, days = snapshot(opt.db, opt.dest)
    except (OSError, sqlite3.Error, ValueError):
        print("FAIL: SQLite backup could not be verified.", file=sys.stderr)
        raise SystemExit(1) from None
    print(f"PASS: verified SQLite snapshot; schema {version}; {days} daily aggregate row(s).")
