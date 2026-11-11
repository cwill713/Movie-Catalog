"""Generate a one-sentence premise for every title and embed it (migration 008).

Resumable: it only touches rows where premise_embedding is null, so you can stop
and restart. Each batch commits on its own; a failing row is retried alone and
skipped, like scripts/db_embedding.py.

    python scripts/db_premise.py --dry-run --limit 5    # print premises, write nothing
    python scripts/db_premise.py --limit 200            # small real run
    python scripts/db_premise.py                        # everything (~3 hours)
"""
import argparse
import logging
import sys
import time
from pathlib import Path

import ollama
import psycopg

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from app.config import get_settings  # noqa: E402
from db_embedding import embed_db_rows  # noqa: E402  (same model call as the main pipeline)

LOG_PATH = Path(__file__).resolve().parent.parent / "premise.log"
log = logging.getLogger("db_premise")

GEN_MODEL = "llama3.2"
BATCH = 50
PROMPT = (
    "In one plain sentence (max 30 words), state the premise of this story. "
    "Describe the kind of story it is and what happens, no character names, no title.\n\n"
    "Title: {title}\nPlot: {plot}"
)


def generate_premise(title: str, plot: str) -> str:
    resp = ollama.chat(
        model=GEN_MODEL,
        messages=[{"role": "user", "content": PROMPT.format(title=title, plot=plot[:1500])}],
        options={"temperature": 0},
    )
    # The model sometimes adds commentary after a blank line; keep the first paragraph only.
    premise = resp["message"]["content"].strip().split("\n\n")[0].strip()
    if not premise:
        raise ValueError("model returned an empty premise")
    return premise


def get_rows(conn, skip_ids, limit):
    # Most-voted first, so a partial run covers the titles people search for.
    return conn.execute(
        """
        SELECT imdb_id, primary_title, plot
        FROM titles
        WHERE premise_embedding IS NULL
          AND plot IS NOT NULL AND btrim(plot) <> ''
          AND NOT (imdb_id = ANY(%(skip)s::text[]))
        ORDER BY imdb_votes DESC NULLS LAST
        LIMIT %(limit)s
        """,
        {"skip": list(skip_ids), "limit": limit},
    ).fetchall()


def write_premise(conn, imdb_id, premise, vector):
    result = conn.execute(
        """
        UPDATE titles
        SET premise = %(premise)s, premise_embedding = %(vec)s::halfvec
        WHERE imdb_id = %(id)s
        """,
        {"premise": premise, "vec": vector, "id": imdb_id},
    )
    if result.rowcount != 1:
        raise RuntimeError(f"UPDATE touched {result.rowcount} rows for {imdb_id}, expected 1")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None, help="stop after this many titles")
    ap.add_argument("--dry-run", action="store_true", help="print premises, write nothing")
    args = ap.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[logging.FileHandler(LOG_PATH, encoding="utf-8"), logging.StreamHandler()],
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)

    failures = {}  # imdb_id -> (title, error); its keys are the skip-list
    done = 0
    started = time.perf_counter()
    with psycopg.connect(get_settings().require_database_url(), connect_timeout=30) as conn:
        while args.limit is None or done < args.limit:
            take = BATCH if args.limit is None else min(BATCH, args.limit - done)
            rows = get_rows(conn, failures.keys(), take)
            if not rows:
                break
            for imdb_id, title, plot in rows:
                try:
                    premise = generate_premise(title, plot)
                    if args.dry_run:
                        print(f"{imdb_id}  {title}\n    {premise}")
                        failures[imdb_id] = (title, "dry-run")  # keeps get_rows from repeating it
                        done += 1
                        continue
                    write_premise(conn, imdb_id, premise, embed_db_rows(premise))
                    conn.commit()  # per row: a later failure can't undo finished rows
                    done += 1
                except Exception as e:
                    conn.rollback()
                    failures[imdb_id] = (title, f"{type(e).__name__}: {e}")
                    log.exception("%s failed, skipping", imdb_id)
            log.info("%d done so far (%.0fs)", done, time.perf_counter() - started)

    real_failures = {k: v for k, v in failures.items() if v[1] != "dry-run"}
    log.info("Done: %d processed, %d failed, %.1fs", done, len(real_failures), time.perf_counter() - started)
    for imdb_id, (title, error) in real_failures.items():
        log.info("  %s  %s  -> %s", imdb_id, title, error)


if __name__ == "__main__":
    main()
