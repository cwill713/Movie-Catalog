"""Shared fixtures.

Every route now requires a signed-in profile, so tests need an identity. Rather
than minting a real Supabase user on each run - slow, and it would leave
accounts behind on failure - the auth dependency is overridden with the seeded
household profile, and the same identity is pushed into the ContextVars that
drive row-level security.

That means these tests exercise the app's own logic against a realistic RLS
context. The genuine token path - signing in, JWKS verification, tampering,
expiry - is covered separately in test_auth.py.
"""

import pytest
from fastapi.testclient import TestClient

from app.auth import current_profile, require_profile
from app.config import get_settings
from app.db import (
    DEFAULT_HOUSEHOLD_ID,
    DEFAULT_PROFILE_ID,
    admin_connection,
    close_pool,
    current_household_id,
    current_profile_id,
    init_pool,
)
from app.main import app


def _requires_database():
    if not get_settings().database_url:
        pytest.skip("DATABASE_URL not configured")


@pytest.fixture(scope="session")
def test_profile():
    """The seeded household profile, as the auth layer would return it."""
    _requires_database()
    init_pool()
    with admin_connection() as conn:
        profile = conn.execute(
            """select p.id, p.display_name, p.household_id, h.name as household_name
                 from profiles p
                 join households h on h.id = p.household_id
                where p.id = %s""",
            (DEFAULT_PROFILE_ID,),
        ).fetchone()
    if profile is None:
        close_pool()
        pytest.skip("seeded profile missing - run scripts/migrate.py")
    yield profile
    close_pool()


@pytest.fixture(autouse=True)
def rls_context(request, test_profile):
    """Bind the identity for the duration of each test.

    autouse so repository-level tests get RLS context without asking. Tests
    that deliberately want *no* identity mark themselves with
    @pytest.mark.no_identity.
    """
    if "no_identity" in request.keywords:
        current_profile_id.set(None)
        current_household_id.set(None)
        yield
        return

    token_p = current_profile_id.set(test_profile["id"])
    token_h = current_household_id.set(test_profile["household_id"])
    yield
    current_profile_id.reset(token_p)
    current_household_id.reset(token_h)


@pytest.fixture(scope="session")
def client(test_profile):
    """A TestClient that is always signed in as the seeded profile."""

    def _profile():
        current_profile_id.set(test_profile["id"])
        current_household_id.set(test_profile["household_id"])
        return dict(test_profile)

    app.dependency_overrides[current_profile] = _profile
    app.dependency_overrides[require_profile] = _profile
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture
def anon_client():
    """A TestClient with no identity at all, for checking things are gated.

    Function-scoped and it strips `app.dependency_overrides` for its lifetime.
    The signed-in `client` fixture is session-scoped and installs those
    overrides on the shared app object, so without this an "anonymous" client
    would silently inherit them and every gating test would pass for the wrong
    reason.
    """
    _requires_database()
    saved = dict(app.dependency_overrides)
    app.dependency_overrides.clear()
    current_profile_id.set(None)
    current_household_id.set(None)
    try:
        with TestClient(app) as c:
            yield c
    finally:
        app.dependency_overrides.update(saved)


@pytest.fixture(scope="session")
def household_id():
    return DEFAULT_HOUSEHOLD_ID


def pytest_configure(config):
    config.addinivalue_line(
        "markers", "no_identity: run without a signed-in profile in context"
    )
