"""Fill in plot, cast, artwork and critic scores from OMDb.

Works through titles that have no OMDb data yet, most-voted first, so an
interrupted run still leaves the catalog useful where it matters most.

Resumable and safe to re-run: it only selects rows where omdb_fetched_at is
null, and it stamps that column even on a miss so a title that genuinely isn't
in OMDb is not retried forever. Progress is committed in batches, so stopping it
with Ctrl-C loses at most one batch.

    python scripts/enrich_omdb.py --limit 50     # small sample first
    python scripts/enrich_omdb.py                # everything outstanding
    python scripts/enrich_omdb.py --retry-errors # re-attempt previous failures

Data from the OMDb API, CC BY-NC 4.0. Non-commercial use only.
"""

import argparse
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

import httpx2 as httpx
import psycopg
from psycopg.rows import dict_row

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.config import get_settings  # noqa: E402

ENDPOINT = "http://www.omdbapi.com/"
BATCH = 200


def clean(value: str | None) -> str | None:
    """OMDb writes missing values as the string 'N/A'."""
    if value is None:
        return None
    value = value.strip()
    return None if value in ("", "N/A") else value


def as_int(value: str | None) -> int | None:
    """First whole number in the string, thousands separators allowed.

    Takes the *leading* number rather than every digit, so "75/100" is 75 and
    not 75100. OMDb returns scores as "75", "75/100" and "99%" depending on
    where they appear.
    """
    value = clean(value)
    if not value:
        return None
    match = re.match(r"\D*(\d[\d,]*)", value)
    return int(match.group(1).replace(",", "")) if match else None


def parse_released(value: str | None) -> str | None:
    """'26 Mar 2010' -> '2010-03-26'."""
    value = clean(value)
    if not value:
        return None
    try:
        return datetime.strptime(value, "%d %b %Y").date().isoformat()
    except ValueError:
        return None


def parse_scores(ratings: list[dict] | None) -> tuple[int | None, int | None]:
    """Pull Rotten Tomatoes (`99%`) and Metacritic (`75/100`) out of Ratings."""
    rt = mc = None
    for entry in ratings or []:
        source, value = entry.get("Source"), entry.get("Value", "")
        if source == "Rotten Tomatoes" and value.endswith("%"):
            rt = as_int(value)
        elif source == "Metacritic" and "/" in value:
            mc = as_int(value.split("/")[0])
    return rt, mc


def to_row(imdb_id: str, payload: dict) -> dict:
    rt, mc_from_ratings = parse_scores(payload.get("Ratings"))
    actors = clean(payload.get("Actors"))
    return {
        "imdb_id": imdb_id,
        "plot": clean(payload.get("Plot")),
        "director": clean(payload.get("Director")),
        "writer": clean(payload.get("Writer")),
        "actors": [a.strip() for a in actors.split(",")] if actors else None,
        "content_rating": clean(payload.get("Rated")),
        "released_on": parse_released(payload.get("Released")),
        "language": clean(payload.get("Language")),
        "country": clean(payload.get("Country")),
        "awards": clean(payload.get("Awards")),
        "poster_url": clean(payload.get("Poster")),
        "metascore": as_int(payload.get("Metascore")) or mc_from_ratings,
        "rt_score": rt,
        "omdb_status": "ok",
    }


def miss(imdb_id: str, status: str) -> dict:
    row = {k: None for k in (
        "plot", "director", "writer", "actors", "content_rating", "released_on",
        "language", "country", "awards", "poster_url", "metascore", "rt_score",
    )}
    return {"imdb_id": imdb_id, "omdb_status": status, **row}


UPDATE = """
update titles set
    plot = %(plot)s, director = %(director)s, writer = %(writer)s,
    actors = %(actors)s, content_rating = %(content_rating)s,
    released_on = %(released_on)s, language = %(language)s,
    country = %(country)s, awards = %(awards)s, poster_url = %(poster_url)s,
    metascore = %(metascore)s, rt_score = %(rt_score)s,
    omdb_status = %(omdb_status)s, omdb_fetched_at = clock_timestamp()
where imdb_id = %(imdb_id)s
"""


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--retry-errors", action="store_true")
    args = parser.parse_args()

    settings = get_settings()
    key = settings.require_omdb_key()

    where = (
        "omdb_fetched_at is null or omdb_status = 'error'"
        if args.retry_errors
        else "omdb_fetched_at is null"
    )
    sql = f"""select imdb_id, primary_title from titles
               where {where}
               order by imdb_votes desc nulls last"""
    if args.limit:
        sql += f" limit {int(args.limit)}"

    with psycopg.connect(settings.require_database_url(), row_factory=dict_row) as conn:
        todo = conn.execute(sql).fetchall()
        done = conn.execute(
            "select count(*) as n from titles where omdb_fetched_at is not null"
        ).fetchone()["n"]

        if not todo:
            print(f"nothing outstanding - {done:,} titles already enriched")
            return 0

        print(f"{len(todo):,} titles to fetch  ({done:,} already done)")
        print(f"{args.workers} workers\n")

        counts = {"ok": 0, "not_found": 0, "error": 0}
        lock = threading.Lock()
        started = time.monotonic()

        client = httpx.Client(
            timeout=httpx.Timeout(20.0),
            limits=httpx.Limits(max_connections=args.workers * 2),
        )

        def fetch(row: dict) -> dict:
            imdb_id = row["imdb_id"]
            for attempt in range(3):
                try:
                    resp = client.get(
                        ENDPOINT,
                        params={"i": imdb_id, "plot": "full", "apikey": key},
                    )
                    if resp.status_code == 429:      # rate limited - ease off
                        time.sleep(2 * (attempt + 1))
                        continue
                    resp.raise_for_status()
                    payload = resp.json()
                    if payload.get("Response") == "True":
                        return to_row(imdb_id, payload)
                    return miss(imdb_id, "not_found")
                except Exception:
                    if attempt == 2:
                        return miss(imdb_id, "error")
                    time.sleep(1 + attempt)
            return miss(imdb_id, "error")

        try:
            with ThreadPoolExecutor(max_workers=args.workers) as pool:
                batch: list[dict] = []
                for i, result in enumerate(pool.map(fetch, todo), 1):
                    with lock:
                        counts[result["omdb_status"]] += 1
                    batch.append(result)

                    if len(batch) >= BATCH or i == len(todo):
                        with conn.cursor() as cur:
                            cur.executemany(UPDATE, batch)
                        conn.commit()
                        batch = []

                        elapsed = time.monotonic() - started
                        rate = i / elapsed
                        remaining = (len(todo) - i) / rate if rate else 0
                        print(
                            f"  {i:>6,}/{len(todo):,}  "
                            f"ok {counts['ok']:,}  missing {counts['not_found']:,}  "
                            f"errors {counts['error']:,}  "
                            f"{rate:.1f}/s  ~{remaining / 60:.0f} min left"
                        )
        except KeyboardInterrupt:
            conn.commit()
            print("\ninterrupted - progress committed, re-run to continue")
            return 1
        finally:
            client.close()

        print(f"\nfinished in {(time.monotonic() - started) / 60:.1f} min")
        print(f"  ok {counts['ok']:,}   missing {counts['not_found']:,}   errors {counts['error']:,}")

        stats = conn.execute("""
            select count(*) filter (where omdb_fetched_at is not null) as enriched,
                   count(*) filter (where plot is not null)            as with_plot,
                   count(*) filter (where poster_url is not null)      as with_poster,
                   count(*) filter (where rt_score is not null)        as with_rt,
                   avg(length(plot))::int                              as avg_plot,
                   max(length(plot))                                   as max_plot,
                   sum(length(plot))                                   as total_plot,
                   count(*)                                            as total
              from titles
        """).fetchone()
        print(f"\ncatalog: {stats['enriched']:,}/{stats['total']:,} enriched")
        print(f"  with plot   {stats['with_plot']:,}")
        print(f"  with poster {stats['with_poster']:,}")
        print(f"  with RT     {stats['with_rt']:,}")
        if stats["avg_plot"]:
            print(f"\nplot text: avg {stats['avg_plot']:,} chars, max {stats['max_plot']:,}, "
                  f"{stats['total_plot'] / 1024 / 1024:.1f} MB total")

        size = conn.execute("""
            select pg_size_pretty(pg_total_relation_size('titles')) as titles,
                   pg_size_pretty(pg_database_size(current_database())) as db,
                   pg_database_size(current_database()) as bytes
        """).fetchone()
        print(f"\nsize: titles {size['titles']}, database {size['db']} "
              f"({size['bytes'] / (500 * 1024 * 1024) * 100:.0f}% of the free tier)")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
