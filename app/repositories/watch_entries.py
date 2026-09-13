"""Data access for watched titles.

An entry's display fields come from the linked catalog row when there is one,
and from the manual_* columns when there isn't. The existing five movies are
manual entries until Phase 3 links them to real IMDb ids, so ``coalesce`` here
is what keeps the API stable across that transition.

The API still speaks genre_one/two/three; storage uses a genres array. The
flattening happens in ``_row_to_movie`` so the schema is clean and the existing
frontend keeps working.
"""

from typing import Any
from uuid import UUID

from app.db import connection, require_context
from app.schemas import MovieCreate

# The display shape the API returns, resolved across catalog and manual columns.
_SELECT = """
    select
        we.id,
        coalesce(t.primary_title, we.manual_title)        as title,
        coalesce(t.start_year,    we.manual_year)         as year,
        case when we.imdb_id is not null then t.genres
             else we.manual_genres end                    as genres,
        er.rating                                         as rating,
        we.imdb_id,
        t.poster_url
    from watch_entries we
    left join titles t on t.imdb_id = we.imdb_id
    left join entry_ratings er
           on er.entry_id = we.id and er.profile_id = %(profile_id)s
    where we.household_id = %(household_id)s
"""


def _row_to_movie(row: dict[str, Any]) -> dict[str, Any]:
    genres = list(row.get("genres") or [])
    genres += [None] * (3 - len(genres))
    return {
        "id": row["id"],
        "title": row["title"],
        "year": row["year"],
        "genre_one": genres[0],
        "genre_two": genres[1],
        "genre_three": genres[2],
        "rating": float(row["rating"]) if row["rating"] is not None else 0.0,
        "imdb_id": row.get("imdb_id"),
        "poster_url": row.get("poster_url"),
    }


def _params(**extra: Any) -> dict[str, Any]:
    profile_id, household_id = require_context()
    return {"household_id": household_id, "profile_id": profile_id, **extra}


def _genres(movie: MovieCreate) -> list[str]:
    return [g for g in (movie.genre_one, movie.genre_two, movie.genre_three) if g]


def get_movies() -> list[dict[str, Any]]:
    with connection() as conn:
        rows = conn.execute(
            _SELECT + " order by we.created_at desc, we.id desc", _params()
        ).fetchall()
    return [_row_to_movie(r) for r in rows]


def get_movie(movie_id: UUID) -> dict[str, Any] | None:
    with connection() as conn:
        row = conn.execute(
            _SELECT + " and we.id = %(id)s", _params(id=movie_id)
        ).fetchone()
    return _row_to_movie(row) if row else None


def search_movies(title: str) -> list[dict[str, Any]]:
    with connection() as conn:
        rows = conn.execute(
            _SELECT
            + """ and coalesce(t.primary_title, we.manual_title) ilike %(pattern)s
                  order by we.created_at desc""",
            _params(pattern=f"%{title}%"),
        ).fetchall()
    return [_row_to_movie(r) for r in rows]


def create_movie(movie: MovieCreate) -> dict[str, Any]:
    """Create the entry and this profile's rating together, or neither."""
    params = _params(title=movie.title, year=movie.year, genres=_genres(movie))
    with connection() as conn:
        with conn.transaction():
            row = conn.execute(
                """insert into watch_entries
                       (household_id, manual_title, manual_year, manual_genres, added_by)
                   values (%(household_id)s, %(title)s, %(year)s, %(genres)s, %(profile_id)s)
                   returning id""",
                params,
            ).fetchone()
            entry_id = row["id"]
            conn.execute(
                """insert into entry_ratings (entry_id, profile_id, rating)
                   values (%s, %s, %s)""",
                (entry_id, params["profile_id"], movie.rating),
            )
    return {"id": entry_id, **movie.model_dump()}


def update_movie(movie_id: UUID, movie: MovieCreate) -> dict[str, Any] | None:
    params = _params(id=movie_id, title=movie.title, year=movie.year, genres=_genres(movie))
    with connection() as conn:
        with conn.transaction():
            updated = conn.execute(
                """update watch_entries
                      set manual_title = %(title)s,
                          manual_year = %(year)s,
                          manual_genres = %(genres)s
                    where id = %(id)s and household_id = %(household_id)s
                    returning id""",
                params,
            ).fetchone()
            if updated is None:
                return None
            conn.execute(
                """insert into entry_ratings (entry_id, profile_id, rating)
                   values (%s, %s, %s)
                   on conflict (entry_id, profile_id)
                   do update set rating = excluded.rating""",
                (movie_id, params["profile_id"], movie.rating),
            )
    return {"id": movie_id, **movie.model_dump()}


def delete_movies(ids: list[UUID]) -> int:
    """entry_ratings and entry_tags cascade from the foreign key."""
    if not ids:
        return 0
    with connection() as conn:
        cur = conn.execute(
            """delete from watch_entries
                where id = any(%(ids)s) and household_id = %(household_id)s""",
            _params(ids=ids),
        )
        return cur.rowcount
