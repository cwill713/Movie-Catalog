import logging
import ollama
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import psycopg
from app.config import get_settings  # noqa: E402

LOG_PATH = Path(__file__).resolve().parent.parent / "embedding.log"
log = logging.getLogger("db_embedding")


def embed_db_rows(movie_text: str):
    batch = ollama.embed(
      model = 'mxbai-embed-large',
      input = movie_text
      )

    return batch['embeddings'][0]

def get_movie_info(conn, skip_ids):
    COMMAND = """
    SELECT t.imdb_id,
           t.primary_title,
           t.start_year,
           t.title_type,
           t.genres,
           t.director,
           t.actors,
           t.plot,
           t.content_rating
    FROM titles t
    WHERE t.embedding IS NULL
      AND NOT (t.imdb_id = ANY(%(skip_ids)s::text[]))
    ORDER BY t.imdb_votes DESC NULLS LAST
    LIMIT 50
    """

    return conn.execute(COMMAND, {'skip_ids': list(skip_ids)}).fetchall()

def update_embedding(embedding, embedding_text, imdb_id, conn):
    UPDATE_COMMAND = """
    UPDATE titles
    SET embedding = %(embedding)s::halfvec,
        embedding_text = %(embedding_text)s
    WHERE imdb_id = %(imdb_id)s;
    """

    result = conn.execute(UPDATE_COMMAND, {'embedding': embedding, 'embedding_text': embedding_text, 'imdb_id': imdb_id})
    if result.rowcount != 1:
        raise RuntimeError(f"UPDATE touched {result.rowcount} rows for {imdb_id}, expected 1")

def _join(value, limit=None):
    """Turn a list or string column into a clean string, or None if empty."""
    if isinstance(value, list):
        value = ", ".join(str(v) for v in value[:limit])
    return str(value).strip() or None if value is not None else None


def create_embedding_text(movie_info):
    imdb_id, title, year, title_type, genres, director, actors, plot, content_rating = movie_info

    heading = title or "Untitled"
    if year:
        heading += f" ({year})"
    if title_type:
        heading += f" - {title_type}"

    lines = [heading]
    for label, value in (
        ("Genres", _join(genres)),
        ("Director", _join(director)),
        ("Actors", _join(actors, limit=5)),
        ("Plot", _join(plot)),
        ("Content Rating", _join(content_rating)),
        ("IMDb ID", _join(imdb_id)),
    ):
        if value:
            lines.append(f"{label}: {value}")
    return {str(imdb_id): "\n".join(lines)}


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[logging.FileHandler(LOG_PATH, encoding="utf-8"), logging.StreamHandler()],
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)  # one INFO line per embed call otherwise

    failures = {}  # imdb_id -> (title line, error); its keys double as the skip-list
    embedded = 0
    started = time.perf_counter()
    with psycopg.connect(get_settings().require_database_url(), connect_timeout=30) as conn:
        rows = get_movie_info(conn, failures.keys())

        while rows:
            # {imdb_id: text}; the key travels with its text, so we always know who failed
            texts = {}
            for row in rows:
                texts.update(create_embedding_text(row))

            try:
                # Fast path: embed every row, then write them all in one commit.
                vectors = {imdb_id: embed_db_rows(text) for imdb_id, text in texts.items()}
                for imdb_id, vector in vectors.items():
                    update_embedding(vector, texts[imdb_id], imdb_id, conn)
                conn.commit()
                embedded += len(texts)
                log.info("batch of %d embedded (%d total)", len(texts), embedded)
            except Exception:
                # Undo the batch and redo it one row at a time to find the culprit.
                conn.rollback()
                log.exception("batch of %d failed, retrying one at a time", len(texts))
                for imdb_id, text in texts.items():
                    try:
                        update_embedding(embed_db_rows(text), text, imdb_id, conn)
                        conn.commit()  # per row: a later failure can't undo finished rows
                        embedded += 1
                    except Exception as e:
                        conn.rollback()  # clear any aborted transaction so the next row can run
                        failures[imdb_id] = (text.splitlines()[0], f"{type(e).__name__}: {e}")
                        log.exception("%s failed, skipping", imdb_id)

            rows = get_movie_info(conn, failures.keys())

    elapsed = time.perf_counter() - started
    log.info("Done: %d embedded, %d failed, %.1fs", embedded, len(failures), elapsed)
    if failures:
        log.info("Failure report (%d):", len(failures))
        for imdb_id, (title, error) in failures.items():
            log.info("  %s  %s  -> %s", imdb_id, title, error)


if __name__ == "__main__":
    main()
