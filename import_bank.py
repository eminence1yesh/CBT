#!/usr/bin/env python3
"""
import_bank.py
--------------
Add a question bank to the database from the command line.

    python import_bank.py data/questions.csv
    python import_bank.py more_questions.xlsx another.json notes.txt
    python import_bank.py --rebuild        # regenerate all cached options

Duplicates are detected by normalised question text and skipped.
"""

from __future__ import annotations

import sys
import time

import builder
import database as db
import parser


def import_file(path: str) -> None:
    t0 = time.time()
    rows = parser.load_bank(path)
    if not rows:
        print(f"{path}: nothing usable found.")
        return
    inserted, dupes = db.insert_questions(rows)
    print(f"{path}: {len(rows)} parsed, {inserted} added, {dupes} duplicates skipped "
          f"({time.time() - t0:.1f}s)")


def main(argv: list[str]) -> int:
    db.init_db()

    if not argv:
        print(__doc__)
        return 1

    if argv[0] in ("--rebuild", "-r"):
        t0 = time.time()
        n = builder.rebuild_all()
        print(f"Rebuilt options for {n} questions in {time.time() - t0:.1f}s")
        return 0

    for path in argv:
        try:
            import_file(path)
        except Exception as exc:
            print(f"{path}: import failed - {exc}", file=sys.stderr)

    t0 = time.time()
    built = builder.build()
    if built:
        print(f"Generated options for {built} new questions in {time.time() - t0:.1f}s")
    print(f"Bank now holds {db.count_questions()} questions.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
