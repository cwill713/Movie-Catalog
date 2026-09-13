"""Postgres connection pool.

Replaces the previous sqlite3 module. A single pool is opened on app startup
and closed on shutdown; ``connection()`` hands out a pooled connection and
commits (or rolls back) around the block.
"""

from contextlib import contextmanager
from typing import Iterator
from uuid import UUID

import psycopg
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from app.config import get_settings

# Fixed IDs seeded by migrations/002_seed_household.sql. Until Phase 5 adds
# auth, every request acts as this household and profile.
DEFAULT_HOUSEHOLD_ID = UUID("00000000-0000-0000-0000-000000000001")
DEFAULT_PROFILE_ID = UUID("00000000-0000-0000-0000-000000000002")

_pool: ConnectionPool | None = None


def init_pool() -> ConnectionPool:
    """Open the pool. Called once, from the app's lifespan handler."""
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
def connection() -> Iterator[psycopg.Connection]:
    """A pooled connection wrapped in a transaction."""
    with get_pool().connection() as conn:
        yield conn
