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
        er.review                                         as review,
        -- Correlated subquery rather than a join: entry_tags is one-to-many, so
        -- joining it would multiply every entry row by its tag count and corrupt
        -- the rating. This stays one round-trip - it is not the N+1 problem,
        -- which is N+1 *queries* from the application, not one query the planner
        -- evaluates per row.
        (select coalesce(array_agg(et.tag order by et.tag), '{}')
           from entry_tags et
          where et.entry_id = we.id
            and et.profile_id = %(profile_id)s)          as tags,
        we.imdb_id,
        t.poster_url
    from watch_entries we
    left join titles t on t.imdb_id = we.imdb_id
    left join entry_ratings er
           on er.entry_id = we.id and er.profile_id = %(profile_id)s
    where we.household_id = %(household_id)s
"""


# Distinguishes "caller omitted this field" from "caller sent an empty value".
_UNSET = object()


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
        "review": row.get("review"),
        "tags": list(row.get("tags") or []),
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


def normalise_tags(tags: list[str]) -> list[str]:
    """Trim, collapse inner whitespace, lowercase, drop blanks, de-duplicate.

    `tag` is part of the primary key, so "Cozy", "cozy" and " cozy " would
    otherwise be three separate tags and the vocabulary would fragment into
    near-duplicates within a week.
    """
    seen: dict[str, None] = {}
    for raw in tags:
        cleaned = " ".join(raw.split()).lower()
        if cleaned:
            seen[cleaned] = None
    return list(seen)


def _replace_tags(conn, entry_id: UUID, profile_id: UUID, tags: list[str]) -> None:
    """Write only the difference, so unchanged tags keep their created_at."""
    desired = set(normalise_tags(tags))
    current = {
        r["tag"]
        for r in conn.execute(
            "select tag from entry_tags where entry_id = %s and profile_id = %s",
            (entry_id, profile_id),
        ).fetchall()
    }

    removed = current - desired
    if removed:
        conn.execute(
            """delete from entry_tags
                where entry_id = %s and profile_id = %s and tag = any(%s)""",
            (entry_id, profile_id, list(removed)),
        )

    added = desired - current
    if added:
        # One statement via unnest rather than cursor.executemany: a single
        # round-trip, and Connection has no executemany in psycopg3 - that lives
        # on the cursor.
        conn.execute(
            """insert into entry_tags (entry_id, profile_id, tag)
               select %s, %s, unnest(%s::text[])""",
            (entry_id, profile_id, list(added)),
        )


def update_rating(
    movie_id: UUID,
    rating: float,
    review: str | None | object = _UNSET,
    tags: list[str] | object = _UNSET,
) -> dict[str, Any] | None:
    """Set this profile's rating, plus its review and tags when supplied.

    `review` and `tags` default to `_UNSET` rather than None so that omitting
    either leaves it alone, while passing an empty value clears it. Treating
    "absent" and "empty" as the same thing would mean a caller updating only the
    score silently destroyed the prose or the tags - the same shape of loss the
    ingest upsert guards against.

    The read must follow the write: returning a row selected beforehand sends
    the caller the previous values under a response model that promises the
    updated ones.
    """
    params = _params(id=movie_id)
    with connection() as conn:
        with conn.transaction():
            entry = conn.execute(
                """select id from watch_entries
                    where id = %(id)s and household_id = %(household_id)s""",
                params,
            ).fetchone()
            if entry is None:
                return None

            if review is _UNSET:
                conn.execute(
                    """insert into entry_ratings (entry_id, profile_id, rating)
                       values (%s, %s, %s)
                       on conflict (entry_id, profile_id)
                       do update set rating = excluded.rating""",
                    (movie_id, params["profile_id"], rating),
                )
            else:
                conn.execute(
                    """insert into entry_ratings (entry_id, profile_id, rating, review)
                       values (%s, %s, %s, %s)
                       on conflict (entry_id, profile_id)
                       do update set rating = excluded.rating,
                                     review = excluded.review""",
                    (movie_id, params["profile_id"], rating, review or None),
                )

            if tags is not _UNSET:
                _replace_tags(conn, movie_id, params["profile_id"], tags)

        row = conn.execute(_SELECT + " and we.id = %(id)s", params).fetchone()
    return _row_to_movie(row) if row else None


def all_tags() -> list[str]:
    """Every tag in use, most-used first, for offering reuse in the UI.

    Household-wide rather than per-profile: RLS already confines this to our own
    household, and seeing what the other person has used is what stops two
    people inventing "comfort watch" and "comfy" for the same thing.
    """
    with connection() as conn:
        rows = conn.execute(
            """select et.tag, count(*) as uses
                 from entry_tags et
                 join watch_entries we on we.id = et.entry_id
                where we.household_id = %(household_id)s
                group by et.tag
                order by uses desc, et.tag""",
            _params(),
        ).fetchall()
    return [r["tag"] for r in rows]


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
