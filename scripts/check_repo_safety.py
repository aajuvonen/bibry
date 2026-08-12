#!/usr/bin/env python3
"""Fail if personal Bibry deployment data is tracked by Git."""
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
FORBIDDEN = (
    ".bib", ".pdf", ".sqlite", ".sqlite3", ".json",
)
ALLOWED = {"bib/.gitkeep", "bib/history/.gitkeep", "pdf/.gitkeep"}
PRIVATE_PREFIXES = ("bib/metadata/", "bib/history/", "bib/scan_jobs/", "bib/cache/")


def main():
    result = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
        cwd=ROOT, text=True, capture_output=True, check=True,
    )
    violations = []
    for raw in result.stdout.splitlines():
        path = raw.strip()
        lower = path.lower()
        if path in ALLOWED:
            continue
        if lower.startswith(PRIVATE_PREFIXES) or lower.startswith("pdf/"):
            violations.append(path)
            continue
        if lower.startswith("bib/") and any(lower.endswith(suffix) for suffix in FORBIDDEN):
            violations.append(path)
    if violations:
        print("Private Bibry data would be included in a commit:", file=sys.stderr)
        print("\n".join(f"  {path}" for path in violations), file=sys.stderr)
        return 1
    print("Repository safety check passed: no private Bibry data is tracked or unignored.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
