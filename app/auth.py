"""Authentication against Supabase Auth.

Sign-in posts the password to Supabase's token endpoint and gets back a signed
JWT. That token is stored in an httpOnly cookie and verified on every request
against the project's JWKS - ES256, so the public key verifies signatures but
cannot mint them, and a leaked copy of this app cannot forge a login.

There is no sign-up route. Accounts are created out of band with
`scripts/create_user.py`, which uses the secret key. The app is invite-only and
a public registration form would be a liability rather than a feature.

The JWT's `sub` claim is the Supabase user id; `profiles.auth_user_id` maps it
to the profile the rest of the app uses.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

import httpx2 as httpx
import jwt
from fastapi import Depends, HTTPException, Request, status
from jwt import PyJWKClient

from app.config import get_settings
from app.db import admin_connection, current_household_id, current_profile_id

SESSION_COOKIE = "mc_session"
_jwk_client: PyJWKClient | None = None


def _jwks() -> PyJWKClient:
    """Cached JWKS client. It caches keys internally, so this is not per-request."""
    global _jwk_client
    if _jwk_client is None:
        _jwk_client = PyJWKClient(get_settings().supabase_jwks_url, cache_keys=True)
    return _jwk_client


def sign_in(email: str, password: str) -> dict[str, Any]:
    """Exchange credentials for tokens. Raises HTTPException on bad login."""
    settings = get_settings()
    response = httpx.post(
        f"{settings.supabase_url}/auth/v1/token",
        params={"grant_type": "password"},
        headers={
            "apikey": settings.supabase_publishable_key,
            "Content-Type": "application/json",
        },
        json={"email": email, "password": password},
        timeout=20.0,
    )
    if response.status_code != 200:
        # Deliberately vague: distinguishing "no such account" from "wrong
        # password" tells an attacker which addresses are registered.
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Incorrect email or password")
    return response.json()


# Tolerance for clock skew between this machine and Supabase's auth server.
#
# Not theoretical: the development machine's clock runs ~10 seconds slow, which
# made every freshly-issued token fail with "The token is not yet valid (iat)"
# - its issued-at looked like the future. Login was completely broken, with an
# error that points nowhere near the real cause.
#
# 30 seconds covers ordinary drift while staying far below the token lifetime,
# so an expired token is still rejected promptly.
CLOCK_SKEW_LEEWAY = 30


def verify_token(token: str) -> dict[str, Any]:
    """Verify signature, expiry and audience. Raises on anything suspect."""
    try:
        key = _jwks().get_signing_key_from_jwt(token).key
        return jwt.decode(
            token,
            key,
            algorithms=["ES256", "RS256"],
            audience="authenticated",
            leeway=CLOCK_SKEW_LEEWAY,
            options={"require": ["exp", "sub"]},
        )
    except jwt.PyJWTError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid session") from exc


def profile_for_auth_user(auth_user_id: str) -> dict[str, Any] | None:
    """Map a Supabase user id to our profile.

    Runs as admin: resolving identity is what *establishes* the RLS context, so
    it cannot itself be subject to it.
    """
    with admin_connection() as conn:
        return conn.execute(
            """select p.id, p.display_name, p.household_id, h.name as household_name
                 from profiles p
                 join households h on h.id = p.household_id
                where p.auth_user_id = %s""",
            (auth_user_id,),
        ).fetchone()


def current_profile(request: Request) -> dict[str, Any] | None:
    """Resolve the signed-in profile, or None. Never raises.

    Also binds the profile to the request context, which is what makes RLS
    apply to every query that follows.
    """
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        return None
    try:
        claims = verify_token(token)
    except HTTPException:
        return None

    profile = profile_for_auth_user(claims["sub"])
    if profile is None:
        # Authenticated with Supabase but no profile here - an account created
        # outside the bootstrap script. Treat as signed out.
        return None

    current_profile_id.set(UUID(str(profile["id"])))
    current_household_id.set(UUID(str(profile["household_id"])))
    return profile


def require_profile(
    profile: dict[str, Any] | None = Depends(current_profile),
) -> dict[str, Any]:
    """Dependency for anything that must not be public."""
    if profile is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Sign in required")
    return profile
