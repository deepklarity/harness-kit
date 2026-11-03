#!/usr/bin/env python3
"""
Check docs/skills/skills.csv against the skill folders on disk, grouped by tier.

Usage:
    python3 list_tiers.py [--root <repo_root>]

Exits non-zero if:
  - a skill folder on disk (docs/skills/<category>/<name>/SKILL.md) has no row in skills.csv
  - a skills.csv row names a folder that doesn't exist
  - a skills.csv row's tier is anything other than kit or workspace
"""

import argparse
import csv
import sys
from pathlib import Path

VALID_TIERS = {"kit", "workspace"}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=".")
    args = parser.parse_args()
    root = Path(args.root).resolve()

    csv_path = root / "docs" / "skills" / "skills.csv"
    rows = list(csv.DictReader(csv_path.open()))

    on_disk = {
        (f.parent.parent.name, f.parent.name)
        for f in (root / "docs" / "skills").glob("*/*/SKILL.md")
    }
    in_csv = {(r["category"], r["name"]) for r in rows}

    missing_from_csv = sorted(on_disk - in_csv)
    missing_folder = sorted(in_csv - on_disk)
    bad_tier = sorted(
        (r["category"], r["name"], r["tier"]) for r in rows if r["tier"] not in VALID_TIERS
    )

    groups = {}
    for r in rows:
        if (r["category"], r["name"]) in on_disk:
            groups.setdefault(r["tier"], []).append(f'{r["category"]}/{r["name"]}')

    for tier in sorted(groups):
        print(f"# {tier}")
        for rel in sorted(groups[tier]):
            print(f"  {rel}")
        print()

    ok = True
    if missing_from_csv:
        ok = False
        print("# ON DISK, NO CSV ROW")
        for cat, name in missing_from_csv:
            print(f"  {cat}/{name}")
        print()
    if missing_folder:
        ok = False
        print("# IN CSV, NO FOLDER")
        for cat, name in missing_folder:
            print(f"  {cat}/{name}")
        print()
    if bad_tier:
        ok = False
        print("# INVALID TIER")
        for cat, name, tier in bad_tier:
            print(f"  {cat}/{name}: {tier!r}")
        print()

    if not ok:
        print("Error: skills.csv is out of sync with docs/skills, or has an invalid tier.")
        sys.exit(1)

    sys.exit(0)


if __name__ == "__main__":
    main()
