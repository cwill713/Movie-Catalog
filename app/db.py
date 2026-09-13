"""Postgres connection pool, with row-level security applied per request.

The important detail: the pool connects as `postgres`, which owns every table
and has rolbypassrls = true. Running queries as that role would silently skip
every RLS policy, so `connection()` switches role for the duration of the
transaction when a profile is in scope:

    set local role authenticated;              -- does NOT bypass RLS
    set local app.current_profile_id = '...';  -- what the policies read

`set local` is scoped to the transaction, so a pooled connection cannot carry
one request's identity into the next.

Admin scripts (ingest, enrichment, embeddings) call `admin_connection()` and
stay as `postgres` on purpose - they maintain the shared catalog and have no
user context.
"""

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Iterator
from uuid import UUID

import psycopg
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from app.config import get_settings

# Seeded by migrations/002. Used only by admin tooling and the bootstrap
# script; request paths resolve the real profile from the session cookie.
DEFAULT_HOUSEHOLD_ID = UUID("00000000-0000-0000-0000-000000000001")
DEFAULT_PROFILE_ID = UUID("00000000-0000-0000-0000-000000000002")

# Set per request by the auth dependency. A ContextVar rather than a global so
# concurrent requests can't see each other's identity.
current_profile_id: ContextVar[UUID | None] = ContextVar(
    "current_profile_id", default=None
)
current_household_id: ContextVar[UUID | None] = ContextVar(
    "current_household_id", default=None
)

_pool: ConnectionPool | None = None


def init_pool() -> ConnectionPool:
    global _pool
    if _pool is None:
        _pool = ConnectionPool(
            conninfo=get_settings().require_database_url(),
            min_size=1,
            max_size=8,
            kwargs={"row_factory": dict_row},
            open=True,
        )
    return _pool


def close_pool() -> None:
    global _pool
    if _pool is not None:
        _pool.close()
        _pool = None


def get_pool() -> ConnectionPool:
    return _pool if _pool is not None else init_pool()


@contextmanager
def connection(profile_id: UUID | None = None) -> Iterator[psycopg.Connection]:
    """A pooled connection scoped to a profile, with RLS in force.

    Falls back to the ContextVar when no profile is passed, so repositories
    don't have to thread identity through every call. With no profile in scope
    the role is still switched, which means RLS returns nothing - failing
    closed rather than open.
    """
    profile = profile_id or current_profile_id.get()
    with get_pool().connection() as conn:
        with conn.transaction():
            conn.execute("set local role authenticated")
            if profile is not None:
                conn.execute(
                    "select set_config('app.current_profile_id', %s, true)",
                    (str(profile),),
                )
            yield conn


@contextmanager
def admin_connection() -> Iterator[psycopg.Connection]:
    """A connection that bypasses RLS. For catalog maintenance only."""
    with get_pool().connection() as conn:
        yield conn


def require_context() -> tuple[UUID, UUID]:
    """The signed-in profile and household, or an explicit failure.

    Repositories call this instead of reaching for the seeded defaults, so a
    route that forgot its auth dependency fails loudly rather than quietly
    serving one household's data to everyone.
    """
    profile, household = current_profile_id.get(), current_household_id.get()
    if profile is None or household is None:
        raise RuntimeError(
            "no signed-in profile in context - the route is missing its auth "
            "dependency, or a script should be using admin_connection()"
        )
    return profile, household
