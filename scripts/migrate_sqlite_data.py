"""One-off: copy the existing SQLite library into Postgres.

Each row in the old ``movies`` table becomes a watch_entry (manual, since the
titles catalog is empty until Phase 3) plus an entry_rating carrying the score.

Idempotent: an entry whose manual_title and manual_year already exist for the
household is skipped, so re-running won't duplicate. The SQLite file is only
read, never modified.

    python scripts/migrate_sqlite_data.py [--dry-run]
"""

import argparse
import sqlite3
import sys
from pathlib import Path

import psycopg

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.config import BASE_DIR, get_settings  # noqa: E402
from app.db import DEFAULT_HOUSEHOLD_ID, DEFAULT_PROFILE_ID  # noqa: E402

SQLITE_PATH = BASE_DIR / "app" / "movies.db"


def read_sqlite() -> list[dict]:
    if not SQLITE_PATH.is_file():
        print(f"no SQLite database at {SQLITE_PATH} - nothing to migrate")
        return []
    conn = sqlite3.connect(SQLITE_PATH)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """select id, title, year, genre_one, genre_two, genre_three, rating
                 from movies order by id"""
        ).fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    movies = read_sqlite()
    if not movies:
        return 0
    print(f"found {len(movies)} movie(s) in SQLite\n")

    inserted = skipped = 0
    with psycopg.connect(get_settings().require_database_url(), connect_timeout=30) as conn:
        for m in movies:
            genres = [g for g in (m["genre_one"], m["genre_two"], m["genre_three"]) if g]
            exists = conn.execute(
                """select id from watch_entries
                    where household_id = %s and manual_title = %s and manual_year = %s""",
                (DEFAULT_HOUSEHOLD_ID, m["title"], m["year"]),
            ).fetchone()

            if exists:
                print(f"  skip    {m['title'][:52]:<52} (already present)")
                skipped += 1
                continue

            if args.dry_run:
                print(f"  would   {m['title'][:52]:<52} {m['year']}  {m['rating']}  {genres}")
                inserted += 1
                continue

            # Offset created_at by the original SQLite id so the default
            # "newest first" ordering reproduces the old app's "order by id desc".
            row = conn.execute(
                """insert into watch_entries
                       (household_id, manual_title, manual_year, manual_genres,
                        added_by, created_at)
                   values (%s, %s, %s, %s, %s,
                           now() - make_interval(secs => %s))
                   returning id""",
                (DEFAULT_HOUSEHOLD_ID, m["title"], m["year"], genres,
                 DEFAULT_PROFILE_ID, 1000 - m["id"]),
            ).fetchone()
            conn.execute(
                """insert into entry_ratings (entry_id, profile_id, rating)
                   values (%s, %s, %s)""",
                (row[0], DEFAULT_PROFILE_ID, m["rating"]),
            )
            print(f"  insert  {m['title'][:52]:<52} {m['year']}  {m['rating']}")
            inserted += 1

        if args.dry_run:
            conn.rollback()
            print(f"\ndry run - would insert {inserted}, skip {skipped}")
        else:
            conn.commit()
            print(f"\nmigrated {inserted}, skipped {skipped}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
