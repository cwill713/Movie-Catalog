"""Apply SQL migrations in order, tracking which have run.

Each file in migrations/ runs once, inside a transaction, and is recorded in
schema_migrations. Re-running is safe: applied files are skipped.

    python scripts/migrate.py           # apply anything pending
    python scripts/migrate.py --status  # show what has and hasn't run
"""

import argparse
import sys
from pathlib import Path

import psycopg

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.config import BASE_DIR, get_settings  # noqa: E402

MIGRATIONS_DIR = BASE_DIR / "migrations"

TRACKING_TABLE = """
create table if not exists schema_migrations (
    filename    text primary key,
    applied_at  timestamptz not null default now()
)
"""


def migration_files() -> list[Path]:
    return sorted(MIGRATIONS_DIR.glob("*.sql"))


def applied(conn) -> set[str]:
    with conn.cursor() as cur:
        cur.execute(TRACKING_TABLE)
        cur.execute("select filename from schema_migrations")
        return {row[0] for row in cur.fetchall()}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--status", action="store_true", help="list without applying")
    args = parser.parse_args()

    settings = get_settings()
    files = migration_files()
    if not files:
        print(f"no .sql files in {MIGRATIONS_DIR}")
        return 0

    with psycopg.connect(settings.require_database_url(), connect_timeout=30) as conn:
        done = applied(conn)
        conn.commit()

        if args.status:
            for path in files:
                print(f"  {'applied' if path.name in done else 'PENDING':>8}  {path.name}")
            return 0

        pending = [p for p in files if p.name not in done]
        if not pending:
            print(f"up to date - {len(done)} migration(s) already applied")
            return 0

        for path in pending:
            print(f"applying {path.name} ...", end=" ", flush=True)
            try:
                with conn.cursor() as cur:
                    cur.execute(path.read_text(encoding="utf-8"))
                    cur.execute(
                        "insert into schema_migrations (filename) values (%s)",
                        (path.name,),
                    )
                conn.commit()
                print("ok")
            except Exception as exc:
                conn.rollback()
                print("FAILED")
                print(f"\n{type(exc).__name__}: {exc}")
                return 1

        print(f"\napplied {len(pending)} migration(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
