"""A re-ingest must never discard OMDb enrichment or embeddings.

After Phase 4 the titles table holds tens of thousands of rows of OMDb data
(one API call each) and locally-generated embeddings (GPU time each). The IMDb
dumps contain none of that, so the upsert in scripts/ingest_imdb.py lists the
columns it refreshes explicitly and omits the rest.

That is easy to break: widening the DO UPDATE SET list, or reaching for a
blanket "update everything", would overwrite expensive columns with nulls from
the staging table - silently, and only noticed much later.

This test imports the real UPSERT statement from the script rather than
restating it, so the two cannot drift apart.
"""

import sys
from pathlib import Path

import psycopg
import pytest
from psycopg.rows import dict_row

from app.config import BASE_DIR, get_settings

sys.path.insert(0, str(BASE_DIR / "scripts"))
from ingest_imdb import STAGING, UPSERT  # noqa: E402

# Deliberately not a real IMDb id, so a stray row can never collide with the
# catalog. Rolled back regardless.
FAKE_ID = "tt-pytest-enrichment"

ENRICHMENT = {
    "plot": "A test plot that a re-ingest must not erase.",
    "poster_url": "https://example.invalid/poster.jpg",
    "director": "A Director",
    "content_rating": "PG",
    "metascore": 77,
    "rt_score": 88,
    "embedding_text": "text that was embedded",
}


@pytest.fixture
def conn():
    settings = get_settings()
    if not settings.database_url:
        pytest.skip("DATABASE_URL not configured")
    with psycopg.connect(settings.database_url, row_factory=dict_row) as c:
        yield c
        c.rollback()  # nothing this test does is ever committed


def test_upsert_refreshes_imdb_columns_but_keeps_enrichment(conn):
    # A catalog row as it looks after Phase 4: IMDb data plus enrichment.
    conn.execute(
        """insert into titles (
               imdb_id, title_type, primary_title, start_year, genres,
               imdb_rating, imdb_votes,
               plot, poster_url, director, content_rating, metascore, rt_score,
               embedding_text, embedding, omdb_fetched_at)
           values (%(imdb_id)s, 'movie', 'Old Title', 1999, '{Drama}',
                   5.0, 100,
                   %(plot)s, %(poster_url)s, %(director)s, %(content_rating)s,
                   %(metascore)s, %(rt_score)s, %(embedding_text)s,
                   %(embedding)s, now())""",
        {
            "imdb_id": FAKE_ID,
            "embedding": "[" + ",".join(["0.1"] * 768) + "]",
            **ENRICHMENT,
        },
    )

    # A fresh dump row for the same title: structured data only, all changed.
    conn.execute(STAGING)
    conn.execute(
        """insert into titles_staging (
               imdb_id, title_type, primary_title, original_title,
               start_year, end_year, runtime_minutes, genres_csv,
               imdb_rating, imdb_votes)
           values (%s, 'movie', 'New Title', null, 2001, null, 120,
                   'Action,Comedy', 8.8, 999999)""",
        (FAKE_ID,),
    )

    conn.execute(UPSERT)

    row = conn.execute(
        "select * from titles where imdb_id = %s", (FAKE_ID,)
    ).fetchone()

    # IMDb-sourced columns are refreshed...
    assert row["primary_title"] == "New Title"
    assert row["start_year"] == 2001
    assert row["runtime_minutes"] == 120
    assert row["genres"] == ["Action", "Comedy"]
    assert float(row["imdb_rating"]) == 8.8
    assert row["imdb_votes"] == 999999

    # ...and everything the dumps know nothing about survives untouched.
    for column, expected in ENRICHMENT.items():
        assert row[column] == expected, (
            f"re-ingest destroyed titles.{column} - the DO UPDATE SET list in "
            f"scripts/ingest_imdb.py must not include OMDb or embedding columns"
        )
    assert row["embedding"] is not None, "re-ingest destroyed titles.embedding"
    assert row["omdb_fetched_at"] is not None, (
        "re-ingest cleared omdb_fetched_at, which would make enrich_omdb.py "
        "re-fetch every already-enriched title"
    )


def test_upsert_statement_names_no_enrichment_columns(conn):
    """A cheap structural guard, independent of the behavioural test above."""
    forbidden = (
        "plot", "poster_url", "director", "writer", "actors", "awards",
        "content_rating", "released_on", "language", "country",
        "metascore", "rt_score", "omdb_fetched_at", "omdb_status",
        "embedding", "embedding_text",
    )
    assignments = UPSERT.split("do update set")[1].lower()
    named = [c for c in forbidden if f"{c} " in assignments or f"{c}=" in assignments]
    assert not named, (
        f"the upsert assigns to enrichment column(s): {named}. "
        "Those come from OMDb and the embedding pipeline, not the IMDb dumps."
    )
