"""Conservative publication check for tracked runtime data and host-specific text."""
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[1]
DISALLOWED_DIRS = {"data", "logs", "secrets", "runtime", "backups", "snapshots"}
DISALLOWED_SUFFIXES = {".db", ".sqlite", ".sqlite3", ".log", ".jsonl", ".csv", ".pem", ".p12", ".pfx"}
EMAIL = re.compile(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}")
HOST_PATH = re.compile(r"(?:^|[\s='\"(])[A-Za-z]:[\\/]", re.MULTILINE)


def main():
    raw = subprocess.check_output(["git", "ls-files", "-z"], cwd=ROOT)
    violations = []
    for rel in raw.decode("utf-8").split("\0"):
        if not rel:
            continue
        path = Path(rel)
        if any(part.lower() in DISALLOWED_DIRS for part in path.parts[:-1]):
            violations.append(rel + ": runtime directory")
        if path.suffix.lower() in DISALLOWED_SUFFIXES:
            violations.append(rel + ": runtime data file")
        if path.parts[0] == "App" and path.suffix in {".py", ".ps1"}:
            content = (ROOT / path).read_text(encoding="utf-8-sig")
            if EMAIL.search(content) or HOST_PATH.search(content):
                violations.append(rel + ": private content candidate")
    if violations:
        raise SystemExit("\n".join(violations))
    print("Tracked file policy passed; human source review is still required.")


if __name__ == "__main__":
    main()
