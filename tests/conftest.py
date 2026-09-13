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
def clean_context():
    """Start every test with NO identity bound.

    This is autouse and deliberately does not set an identity. An earlier
    version bound the seeded profile here, which silently masked a real bug:
    the app bound identity inside a FastAPI dependency, where it never reached
    the endpoint, and every authenticated page 500'd in the browser while the
    whole suite stayed green. The fixture was supplying out of band exactly what
    the app failed to supply.

    HTTP tests must now get their identity from the real middleware. Tests that
    call repositories directly ask for `as_profile`.
    """
    current_profile_id.set(None)
    current_household_id.set(None)
    yield
    current_profile_id.set(None)
    current_household_id.set(None)


@pytest.fixture
def as_profile(test_profile):
    """Bind the seeded profile, for tests that call repositories directly."""
    current_profile_id.set(test_profile["id"])
    current_household_id.set(test_profile["household_id"])
    yield test_profile
    current_profile_id.set(None)
    current_household_id.set(None)


@pytest.fixture(scope="session")
def client(test_profile):
    """A TestClient signed in as the seeded profile.

    Patches `resolve_profile` - the function the middleware calls - rather than
    overriding the route dependencies. That is deliberate: it means requests go
    through the REAL middleware, which is what binds identity into the context
    RLS reads from.

    An earlier version overrode the dependencies and let the conftest fixture
    set the ContextVars itself. Every test passed, and the app was broken in the
    browser: FastAPI runs sync dependencies and sync endpoints as separate
    threadpool tasks with separate context copies, so a ContextVar set in a
    dependency never reached the endpoint. The tests masked it by setting the
    variable outside the request entirely. Patch the middleware's input, not the
    machinery under test.
    """
    import app.main as main_module

    original = main_module.resolve_profile
    main_module.resolve_profile = lambda request: dict(test_profile)
    try:
        with TestClient(app) as c:
            yield c
    finally:
        main_module.resolve_profile = original


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
    import app.main as main_module

    saved_overrides = dict(app.dependency_overrides)
    saved_resolver = main_module.resolve_profile
    app.dependency_overrides.clear()
    main_module.resolve_profile = lambda request: None
    try:
        with TestClient(app) as c:
            yield c
    finally:
        app.dependency_overrides.update(saved_overrides)
        main_module.resolve_profile = saved_resolver


@pytest.fixture(scope="session")
def household_id():
    return DEFAULT_HOUSEHOLD_ID


def pytest_configure(config):
    config.addinivalue_line(
        "markers", "no_identity: run without a signed-in profile in context"
    )
