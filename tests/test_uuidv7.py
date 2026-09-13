"""The uuidv7() shim must produce spec-compliant, time-ordered UUIDs.

Postgres 18 has uuidv7() built in; this database is 17.6, so migration 003
defines it. These tests exist so that when the function is eventually dropped in
favour of the native one, anything that regressed shows up immediately.

They also pin the fix for the ordering bug: created_at defaults to
clock_timestamp(), not now(). now() returns the *transaction* start time, so a
bulk insert used to give every row an identical timestamp.
"""

import time
import uuid

import psycopg
import pytest

from app.config import get_settings


@pytest.fixture(scope="module")
def conn():
    settings = get_settings()
    if not settings.database_url:
        pytest.skip("DATABASE_URL not configured")
    with psycopg.connect(settings.database_url, connect_timeout=30) as c:
        c.autocommit = True
        yield c


def _uuidv7(conn) -> str:
    return conn.execute("select uuidv7()::text").fetchone()[0]


def test_version_and_variant_nibbles(conn):
    """Layout is xxxxxxxx-xxxx-Vxxx-Nxxx-xxxxxxxxxxxx: V must be 7, N must be 8-b."""
    value = _uuidv7(conn)
    assert value[14] == "7", f"version nibble is {value[14]}, expected 7"
    assert value[19] in "89ab", f"variant nibble is {value[19]}, expected 8/9/a/b"


def test_parses_as_a_real_uuid(conn):
    assert uuid.UUID(_uuidv7(conn)).version == 7


def test_leading_bits_encode_the_current_time(conn):
    """The first 48 bits are a unix millisecond timestamp.

    Compared against the *server* clock - the local machine's clock may drift,
    and that would be a false failure.
    """
    value, server_epoch = conn.execute(
        "select uuidv7()::text, extract(epoch from clock_timestamp())"
    ).fetchone()
    encoded_ms = int(value.replace("-", "")[:12], 16)
    assert abs(encoded_ms / 1000 - float(server_epoch)) < 1.0


def test_ids_generated_over_time_sort_chronologically(conn):
    values = []
    for _ in range(5):
        values.append(_uuidv7(conn))
        time.sleep(0.02)
    assert values == sorted(values)


def test_bulk_insert_in_one_transaction_keeps_its_order(conn):
    """The regression test for the ordering bug.

    With UUIDv4 keys and a now() default, five rows written in a single
    transaction shared one created_at and sorted arbitrarily.
    """
    conn.execute("drop table if exists _uuidv7_check")
    conn.execute(
        """create temp table _uuidv7_check (
               id uuid default uuidv7(),
               n int,
               created_at timestamptz default clock_timestamp()
           )"""
    )
    try:
        conn.execute("begin")
        for n in range(5):
            conn.execute("insert into _uuidv7_check (n) values (%s)", (n,))
        conn.execute("commit")

        by_id = [r[0] for r in conn.execute(
            "select n from _uuidv7_check order by id").fetchall()]
        assert by_id == [0, 1, 2, 3, 4], "uuidv7 keys did not preserve insert order"

        distinct = conn.execute(
            "select count(distinct created_at) from _uuidv7_check").fetchone()[0]
        assert distinct == 5, (
            f"only {distinct} distinct created_at values across 5 rows - "
            "a now() default has crept back in somewhere"
        )
    finally:
        conn.execute("drop table if exists _uuidv7_check")


def test_existing_rows_use_v7(conn):
    """Every watch entry and rating should carry a v7 key."""
    for table in ("watch_entries", "entry_ratings"):
        rows = conn.execute(f"select id::text from {table}").fetchall()
        if not rows:
            continue
        bad = [r[0] for r in rows if r[0][14] != "7"]
        assert not bad, f"{table} still has non-v7 ids: {bad[:3]}"
