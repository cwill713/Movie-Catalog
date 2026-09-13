"""Data access for the catalog.

Search combines two strategies, because they fail in different ways:

  * `ilike '%query%'` finds substrings reliably but can't handle typos
  * trigram similarity handles typos and word order but misses short substrings

Rows matching either are returned, ranked by similarity first and popularity
second, so "dragon" puts the well-known dragon films above obscure ones.
`pg_trgm` and the GIN index on primary_title do the heavy lifting.
"""

from typing import Any
from uuid import UUID

from app.db import DEFAULT_HOUSEHOLD_ID, connection

_COLUMNS = """
    t.imdb_id, t.title_type, t.primary_title, t.start_year, t.end_year,
    t.runtime_minutes, t.genres, t.imdb_rating, t.imdb_votes,
    t.plot, t.director, t.actors, t.content_rating, t.poster_url,
    t.metascore, t.rt_score
"""

# Whether this household has already logged the title. Correlated exists() is
# the right shape here - a join would multiply rows when a title somehow has
# more than one entry, and we only ever want a boolean.
_WATCHED = """
    exists (
        select 1 from watch_entries we
         where we.household_id = %(household_id)s and we.imdb_id = t.imdb_id
    ) as watched
"""


def _row(row: dict[str, Any]) -> dict[str, Any]:
    out = dict(row)
    out["genres"] = list(out.get("genres") or [])
    if out.get("imdb_rating") is not None:
        out["imdb_rating"] = float(out["imdb_rating"])
    return out


def search(
    query: str | None = None,
    *,
    genre: str | None = None,
    title_type: str | None = None,
    year_from: int | None = None,
    year_to: int | None = None,
    min_rating: float | None = None,
    exclude_watched: bool = False,
    limit: int = 60,
    offset: int = 0,
) -> list[dict[str, Any]]:
    params: dict[str, Any] = {
        "query": query or None,
        "pattern": f"%{query}%" if query else None,
        "genre": genre,
        "title_type": title_type,
        "year_from": year_from,
        "year_to": year_to,
        "min_rating": min_rating,
        "household_id": DEFAULT_HOUSEHOLD_ID,
        "limit": max(1, min(limit, 200)),
        "offset": max(0, offset),
    }

    watched_clause = (
        """and not exists (
               select 1 from watch_entries we
                where we.household_id = %(household_id)s and we.imdb_id = t.imdb_id
           )"""
        if exclude_watched
        else ""
    )

    # Every optional parameter carries an explicit cast. Postgres cannot infer a
    # type from `$1 is null` on its own and raises AmbiguousParameter without it.
    sql = f"""
        select {_COLUMNS}, {_WATCHED},
               case when %(query)s::text is null then 0
                    else similarity(t.primary_title, %(query)s::text) end as match_score
          from titles t
         where (%(query)s::text is null
                or t.primary_title ilike %(pattern)s::text
                or t.original_title ilike %(pattern)s::text
                or t.primary_title %% %(query)s::text)
           and (%(genre)s::text is null or %(genre)s::text = any(t.genres))
           and (%(title_type)s::text is null or t.title_type = %(title_type)s::text)
           and (%(year_from)s::int is null or t.start_year >= %(year_from)s::int)
           and (%(year_to)s::int is null or t.start_year <= %(year_to)s::int)
           and (%(min_rating)s::numeric is null or t.imdb_rating >= %(min_rating)s::numeric)
           {watched_clause}
         order by match_score desc, t.imdb_votes desc nulls last
         limit %(limit)s offset %(offset)s
    """
    with connection() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [_row(r) for r in rows]


def get(imdb_id: str) -> dict[str, Any] | None:
    with connection() as conn:
        row = conn.execute(
            f"select {_COLUMNS}, {_WATCHED} from titles t where t.imdb_id = %(imdb_id)s",
            {"imdb_id": imdb_id, "household_id": DEFAULT_HOUSEHOLD_ID},
        ).fetchone()
    return _row(row) if row else None


def genres() -> list[str]:
    """Distinct genres present in the catalog, most common first."""
    with connection() as conn:
        rows = conn.execute(
            """select g, count(*) as n
                 from titles, unnest(genres) as g
                group by g order by n desc"""
        ).fetchall()
    return [r["g"] for r in rows]


def stats() -> dict[str, Any]:
    with connection() as conn:
        total = conn.execute("select count(*) as n from titles").fetchone()["n"]
        by_type = conn.execute(
            "select title_type, count(*) as n from titles group by 1 order by 2 desc"
        ).fetchall()
        enriched = conn.execute(
            "select count(*) as n from titles where omdb_fetched_at is not null"
        ).fetchone()["n"]
    return {
        "total": total,
        "by_type": {r["title_type"]: r["n"] for r in by_type},
        "enriched": enriched,
    }


def link_watch_entry(entry_id: UUID, imdb_id: str) -> bool:
    """Attach a manual watch entry to a catalog title."""
    with connection() as conn:
        cur = conn.execute(
            """update watch_entries
                  set imdb_id = %s
                where id = %s and household_id = %s""",
            (imdb_id, entry_id, DEFAULT_HOUSEHOLD_ID),
        )
        return cur.rowcount > 0


def ratings_for(imdb_id: str) -> list[dict[str, Any]]:
    """Each household member's rating and review for a title, if any."""
    with connection() as conn:
        rows = conn.execute(
            """select p.display_name, er.rating, er.review
                 from watch_entries we
                 join entry_ratings er on er.entry_id = we.id
                 join profiles p on p.id = er.profile_id
                where we.household_id = %s and we.imdb_id = %s
                order by p.display_name""",
            (DEFAULT_HOUSEHOLD_ID, imdb_id),
        ).fetchall()
    return [
        {**r, "rating": float(r["rating"]) if r["rating"] is not None else None}
        for r in rows
    ]
