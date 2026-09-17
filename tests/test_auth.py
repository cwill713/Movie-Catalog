"""Authentication and row-level security.

Two halves:

  * gating - every page redirects to /login and every API returns 401 without
    a session
  * isolation - one household cannot read or write another's rows, proven by
    creating a second household and trying

The isolation tests matter because RLS fails *silently* when it doesn't apply.
The app connects as `postgres`, which owns every table and has rolbypassrls, so
policies alone would enforce nothing. What makes them real is the per-request
`set local role authenticated`. If that were ever removed, nothing would error -
every household would simply see everything. These tests are the alarm.
"""

import pytest

from app.config import get_settings
from app.db import (
    DEFAULT_HOUSEHOLD_ID,
    DEFAULT_PROFILE_ID,
    admin_connection,
    connection,
    current_household_id,
    current_profile_id,
)


# --- gating ----------------------------------------------------------------

@pytest.mark.parametrize(
    "path", ["/", "/catalog", "/movie-input", "/title/tt0892769"]
)
def test_pages_redirect_to_login_when_signed_out(anon_client, path):
    response = anon_client.get(path, follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"].startswith("/login")


@pytest.mark.parametrize(
    "path",
    ["/api/movies", "/api/movies/search?title=x", "/api/catalog/stats",
     "/api/catalog/search", "/api/catalog/genres"],
)
def test_api_returns_401_when_signed_out(anon_client, path):
    """401 rather than a redirect - these are called by fetch, not navigation."""
    assert anon_client.get(path).status_code == 401


@pytest.mark.parametrize("method, path", [
    ("POST", "/api/movies"),
    ("DELETE", "/api/movies"),
    ("PUT", "/api/movies/00000000-0000-0000-0000-000000000000"),
    ("PATCH", "/api/movies/00000000-0000-0000-0000-000000000000/rating"),
])
def test_api_writes_are_gated(anon_client, method, path):
    """TestClient.delete() takes no json= kwarg, so go through .request()."""
    response = anon_client.request(method, path, json={})
    assert response.status_code == 401, "a write reached the app unauthenticated"


def test_login_page_is_public(anon_client):
    assert anon_client.get("/login").status_code == 200


def test_there_is_no_signup_route(anon_client):
    """Invite-only by design - accounts come from scripts/create_user.py."""
    for path in ("/signup", "/register", "/api/signup"):
        assert anon_client.get(path).status_code == 404


def test_redirect_preserves_where_you_were_going(anon_client):
    response = anon_client.get("/catalog", follow_redirects=False)
    assert "next=/catalog" in response.headers["location"]


# --- token handling --------------------------------------------------------

def test_tampered_token_is_rejected():
    from app.auth import verify_token
    from fastapi import HTTPException

    fake = (
        "eyJhbGciOiJFUzI1NiIsInR5cCI6IkpXVCJ9."
        "eyJzdWIiOiJhdHRhY2tlciIsImF1ZCI6ImF1dGhlbnRpY2F0ZWQiLCJleHAiOjk5OTk5OTk5OTl9."
        "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
    )
    with pytest.raises(HTTPException):
        verify_token(fake)


def test_garbage_cookie_is_treated_as_signed_out(anon_client):
    anon_client.cookies.set("mc_session", "not-a-jwt")
    try:
        assert anon_client.get("/", follow_redirects=False).status_code == 303
    finally:
        anon_client.cookies.clear()


def test_clock_skew_leeway_is_configured():
    """Guards a real outage: a ~10s slow clock made every token 'not yet valid'."""
    from app.auth import CLOCK_SKEW_LEEWAY

    assert CLOCK_SKEW_LEEWAY >= 10


# --- row-level security ----------------------------------------------------

@pytest.fixture
def other_household():
    """A second household with one entry, torn down afterwards."""
    if not get_settings().database_url:
        pytest.skip("DATABASE_URL not configured")
    with admin_connection() as conn:
        hh = conn.execute(
            "insert into households (name) values ('RLS Test') returning id"
        ).fetchone()["id"]
        profile = conn.execute(
            """insert into profiles (household_id, display_name)
               values (%s, 'RLS Tester') returning id""",
            (hh,),
        ).fetchone()["id"]
        entry = conn.execute(
            """insert into watch_entries (household_id, manual_title, manual_year)
               values (%s, 'Their Private Film', 2001) returning id""",
            (hh,),
        ).fetchone()["id"]
        conn.execute(
            "insert into entry_ratings (entry_id, profile_id, rating) values (%s,%s,4.0)",
            (entry, profile),
        )
        conn.commit()
    yield {"household_id": hh, "profile_id": profile, "entry_id": entry}
    with admin_connection() as conn:
        conn.execute("delete from households where id = %s", (hh,))
        conn.commit()


def _as(profile_id, household_id):
    current_profile_id.set(profile_id)
    current_household_id.set(household_id)


def test_each_household_sees_only_its_own_entries(other_household):
    _as(DEFAULT_PROFILE_ID, DEFAULT_HOUSEHOLD_ID)
    with connection() as conn:
        ours = conn.execute("select manual_title from watch_entries").fetchall()
    assert all(r["manual_title"] != "Their Private Film" for r in ours)

    _as(other_household["profile_id"], other_household["household_id"])
    with connection() as conn:
        theirs = conn.execute("select manual_title from watch_entries").fetchall()
    assert len(theirs) == 1
    assert theirs[0]["manual_title"] == "Their Private Film"


def test_unscoped_update_cannot_reach_another_household(other_household):
    """UPDATE with no WHERE must still only touch your own rows."""
    _as(other_household["profile_id"], other_household["household_id"])
    with connection() as conn:
        changed = conn.execute(
            "update watch_entries set manual_year = 1900"
        ).rowcount
    assert changed == 1, f"reached {changed} rows - RLS is not confining writes"

    _as(DEFAULT_PROFILE_ID, DEFAULT_HOUSEHOLD_ID)
    with connection() as conn:
        untouched = conn.execute(
            "select count(*) as n from watch_entries where manual_year = 1900"
        ).fetchone()["n"]
    assert untouched == 0, "another household modified our rows"


def test_unscoped_delete_cannot_reach_another_household(other_household):
    _as(other_household["profile_id"], other_household["household_id"])
    with connection() as conn:
        deleted = conn.execute("delete from entry_ratings").rowcount
    assert deleted == 1, f"deleted {deleted} rating rows - RLS is not confining deletes"

    _as(DEFAULT_PROFILE_ID, DEFAULT_HOUSEHOLD_ID)
    with connection() as conn:
        ours = conn.execute(
            "select count(*) as n from entry_ratings"
        ).fetchone()["n"]
    assert ours > 0, "another household deleted our ratings"


def test_insert_into_another_household_is_refused(other_household):
    import psycopg

    _as(other_household["profile_id"], other_household["household_id"])
    with pytest.raises(psycopg.Error):
        with connection() as conn:
            conn.execute(
                """insert into watch_entries (household_id, manual_title, manual_year)
                   values (%s, 'Injected', 2020)""",
                (str(DEFAULT_HOUSEHOLD_ID),),
            )


@pytest.mark.no_identity
def test_no_identity_sees_nothing():
    """Fail closed: an unauthenticated context returns no rows, not all rows."""
    with connection() as conn:
        n = conn.execute("select count(*) as n from watch_entries").fetchone()["n"]
    assert n == 0


@pytest.mark.no_identity
def test_repositories_refuse_to_run_without_identity():
    """The Python-side guard, in case a route ever forgets its dependency."""
    from app.repositories import watch_entries as repo

    with pytest.raises(RuntimeError, match="no signed-in profile"):
        repo.get_movies()


def test_admin_connection_still_bypasses_rls():
    """Ingest and enrichment need this - it is deliberate, so pin it."""
    with admin_connection() as conn:
        n = conn.execute("select count(*) as n from titles").fetchone()["n"]
    assert n > 0


# --- identity must reach the endpoint, not just the dependency -------------
#
# The bug this guards: identity was bound inside a FastAPI dependency. FastAPI
# runs sync dependencies and sync endpoints as separate threadpool tasks, each
# with its own context copy, so the ContextVar never reached the endpoint and
# every authenticated page returned 500 in a browser.
#
# The first fix used @app.middleware("http") - Starlette's BaseHTTPMiddleware -
# which spawns the downstream app as a task *before* running the dispatch body.
# Context is copied at spawn, so setting a ContextVar in the body is still too
# late. Only pure ASGI middleware, running in the same task, works.
#
# Both failures were invisible to the test suite at the time. These are the
# alarm.

def test_signed_in_pages_render(client):
    """The end-to-end symptom: this was a 500 while the suite stayed green."""
    response = client.get("/")
    assert response.status_code == 200, "authenticated page failed to render"


def test_endpoint_sees_the_identity_bound_by_middleware(client):
    """A route that reads the database proves the context crossed the boundary."""
    response = client.get("/api/movies")
    assert response.status_code == 200
    assert isinstance(response.json(), list)


def test_identity_middleware_is_pure_asgi():
    """BaseHTTPMiddleware silently breaks ContextVar propagation - pin the fix."""
    from starlette.middleware.base import BaseHTTPMiddleware

    from app.main import IdentityMiddleware

    assert not issubclass(IdentityMiddleware, BaseHTTPMiddleware), (
        "IdentityMiddleware must stay pure ASGI. BaseHTTPMiddleware runs the "
        "downstream app in a separate task whose context is copied before "
        "dispatch runs, so identity never reaches the endpoint."
    )


def test_the_app_actually_installs_the_identity_middleware():
    from app.main import IdentityMiddleware, app

    assert any(m.cls is IdentityMiddleware for m in app.user_middleware), (
        "IdentityMiddleware is not installed - nothing binds identity per request"
    )
