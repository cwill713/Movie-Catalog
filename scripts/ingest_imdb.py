"""Load the IMDb dumps into the titles catalog.

Streams both .tsv.gz files, keeps the rows worth having, and bulk-loads them
with COPY into a staging table before a single upsert into titles.

Idempotent. Re-running refreshes ratings and vote counts, and re-running with a
lower --min-votes adds newly-qualifying titles without touching existing rows.
The upsert deliberately never overwrites OMDb enrichment or embeddings - those
are expensive to regenerate and have nothing to do with these files.

    python scripts/ingest_imdb.py                  # uses MIN_VOTES from .env
    python scripts/ingest_imdb.py --min-votes 2500
    python scripts/ingest_imdb.py --dry-run        # count only, no writes

Source: IMDb non-commercial datasets. Personal, non-commercial use only;
the dumps must never be committed or redistributed.
"""

import argparse
import gzip
import sys
import time
from pathlib import Path

import psycopg

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.config import get_settings  # noqa: E402

# Series and films only. The dumps are ~77% tvEpisode rows, which we don't want:
# episode-level tracking isn't a feature, and they'd dwarf everything else.
KEEP_TYPES = {"movie", "tvSeries", "tvMiniSeries", "tvMovie"}

STAGING = """
create temp table titles_staging (
    imdb_id         text,
    title_type      text,
    primary_title   text,
    original_title  text,
    start_year      int,
    end_year        int,
    runtime_minutes int,
    genres_csv      text,
    imdb_rating     numeric(3,1),
    imdb_votes      int
) on commit drop
"""

# Only IMDb-sourced columns are refreshed. plot, poster_url, embedding and the
# rest of the OMDb/embedding columns are left exactly as they are.
UPSERT = """
insert into titles (
    imdb_id, title_type, primary_title, original_title,
    start_year, end_year, runtime_minutes, genres, imdb_rating, imdb_votes
)
select
    imdb_id, title_type, primary_title, original_title,
    start_year, end_year, runtime_minutes,
    case when coalesce(genres_csv, '') = '' then '{}'::text[]
         else string_to_array(genres_csv, ',') end,
    imdb_rating, imdb_votes
from titles_staging
on conflict (imdb_id) do update set
    title_type      = excluded.title_type,
    primary_title   = excluded.primary_title,
    original_title  = excluded.original_title,
    start_year      = excluded.start_year,
    end_year        = excluded.end_year,
    runtime_minutes = excluded.runtime_minutes,
    genres          = excluded.genres,
    imdb_rating     = excluded.imdb_rating,
    imdb_votes      = excluded.imdb_votes,
    updated_at      = clock_timestamp()
"""


def _int(value: str) -> int | None:
    """IMDb writes missing values as \\N."""
    return int(value) if value and value != "\\N" else None


def _text(value: str) -> str | None:
    return value if value and value != "\\N" else None


def load_ratings(path: Path, min_votes: int) -> dict[str, tuple[float, int]]:
    """tconst -> (rating, votes), pre-filtered.

    Filtering here rather than later keeps ~40k entries in memory instead of
    the full 1.7M.
    """
    ratings: dict[str, tuple[float, int]] = {}
    total = 0
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        next(fh)
        for line in fh:
            total += 1
            tconst, avg, votes = line.rstrip("\n").split("\t")
            n = int(votes)
            if n >= min_votes:
                ratings[tconst] = (float(avg), n)
    print(f"  {total:,} rated titles in the dump, {len(ratings):,} clear {min_votes:,} votes")
    return ratings


def stream_titles(path: Path, ratings: dict[str, tuple[float, int]]):
    """Yield rows worth keeping, with a progress counter."""
    kept = scanned = 0
    started = time.monotonic()
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        next(fh)
        for line in fh:
            scanned += 1
            if scanned % 2_000_000 == 0:
                print(f"    scanned {scanned:,} ... kept {kept:,}")

            parts = line.rstrip("\n").split("\t")
            if len(parts) != 9:
                continue
            tconst, ttype, primary, original, adult, start, end, runtime, genres = parts

            if ttype not in KEEP_TYPES or adult == "1":
                continue
            rating = ratings.get(tconst)
            if rating is None:
                continue

            kept += 1
            yield (
                tconst, ttype, primary, _text(original),
                _int(start), _int(end), _int(runtime),
                _text(genres), rating[0], rating[1],
            )

    print(f"  scanned {scanned:,} rows in {time.monotonic() - started:.1f}s, kept {kept:,}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--min-votes", type=int, default=None)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    settings = get_settings()
    min_votes = args.min_votes if args.min_votes is not None else settings.min_votes
    data = settings.imdb_data_path
    basics, ratings_path = data / "title.basics.tsv.gz", data / "title.ratings.tsv.gz"

    for path in (basics, ratings_path):
        if not path.is_file():
            print(f"missing {path}\nDownload the IMDb datasets into {data}")
            return 1

    print(f"ingest at min_votes={min_votes:,}\n")
    print("reading ratings ...")
    ratings = load_ratings(ratings_path, min_votes)

    if args.dry_run:
        print("\nscanning titles (dry run, nothing written) ...")
        print(f"\nwould load {sum(1 for _ in stream_titles(basics, ratings)):,} titles")
        return 0

    with psycopg.connect(settings.require_database_url(), connect_timeout=30) as conn:
        before = conn.execute("select count(*) from titles").fetchone()[0]

        print("\nstreaming titles into staging ...")
        with conn.cursor() as cur:
            cur.execute(STAGING)
            copy_sql = """copy titles_staging (
                imdb_id, title_type, primary_title, original_title,
                start_year, end_year, runtime_minutes, genres_csv,
                imdb_rating, imdb_votes) from stdin"""
            with cur.copy(copy_sql) as copy:
                for row in stream_titles(basics, ratings):
                    copy.write_row(row)

            print("\nupserting into titles ...")
            started = time.monotonic()
            cur.execute(UPSERT)
            print(f"  {cur.rowcount:,} rows in {time.monotonic() - started:.1f}s")

        conn.commit()

        after = conn.execute("select count(*) from titles").fetchone()[0]
        print(f"\ncatalog: {before:,} -> {after:,} titles (+{after - before:,})")

        print("\nby type:")
        for ttype, n in conn.execute(
            "select title_type, count(*) from titles group by 1 order by 2 desc"
        ).fetchall():
            print(f"  {ttype:14} {n:>8,}")

        print("\nsize:")
        for label, value in conn.execute("""
            select 'titles table', pg_size_pretty(pg_total_relation_size('titles'))
            union all
            select 'whole database', pg_size_pretty(pg_database_size(current_database()))
        """).fetchall():
            print(f"  {label:16} {value}")
        used = conn.execute("select pg_database_size(current_database())").fetchone()[0]
        print(f"  {'free tier':16} {used / 1024 / 1024:.0f} MB of 500 MB "
              f"({used / (500 * 1024 * 1024) * 100:.0f}%)")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
