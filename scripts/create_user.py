"""Create a household member. Invite-only, so there is no sign-up route.

Creates the Supabase Auth user with the secret key (pre-confirmed, so no
confirmation email is needed) and links it to a profile.

If a profile with the same display name already exists and has no auth user
attached, it is claimed rather than duplicated - that is how the seeded
"Christian" profile keeps its five watch entries and ratings.

    python scripts/create_user.py --email you@example.com --name Christian
    python scripts/create_user.py --email her@example.com --name Alex --new-profile

Password is prompted for, never passed on the command line, so it stays out of
shell history.
"""

import argparse
import getpass
import sys
from pathlib import Path

import httpx2 as httpx
import psycopg
from psycopg.rows import dict_row

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.config import get_settings  # noqa: E402
from app.db import DEFAULT_HOUSEHOLD_ID  # noqa: E402


def create_auth_user(email: str, password: str) -> str:
    """Create a pre-confirmed Supabase user; return its id. Idempotent-ish."""
    settings = get_settings()
    headers = {
        "apikey": settings.supabase_secret_key,
        "Authorization": f"Bearer {settings.supabase_secret_key}",
        "Content-Type": "application/json",
    }
    response = httpx.post(
        f"{settings.supabase_url}/auth/v1/admin/users",
        headers=headers,
        json={"email": email, "password": password, "email_confirm": True},
        timeout=30.0,
    )
    if response.status_code in (200, 201):
        return response.json()["id"]

    body = response.text
    if "already been registered" in body or response.status_code == 422:
        # Already exists - find them so the script stays re-runnable.
        listing = httpx.get(
            f"{settings.supabase_url}/auth/v1/admin/users",
            headers=headers,
            params={"page": 1, "per_page": 200},
            timeout=30.0,
        )
        listing.raise_for_status()
        for user in listing.json().get("users", []):
            if user.get("email", "").lower() == email.lower():
                print("  auth user already existed - reusing it")
                return user["id"]
    raise SystemExit(f"could not create auth user: {response.status_code} {body[:300]}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--email", required=True)
    parser.add_argument(
        "-n", "--name", default=None,
        help="display name shown in the app. Omit it and the script claims the "
             "household's single unlinked profile, or falls back to the email "
             "local part.",
    )
    parser.add_argument(
        "--new-profile",
        action="store_true",
        help="always create a new profile instead of claiming a matching unlinked one",
    )
    args = parser.parse_args()

    # Resolve the display name before asking for a password, so a mistake here
    # doesn't waste the prompt.
    name = args.name
    if name is None and not args.new_profile:
        with psycopg.connect(get_settings().require_database_url(), row_factory=dict_row) as conn:
            unlinked = conn.execute(
                """select id, display_name from profiles
                    where auth_user_id is null and household_id = %s""",
                (DEFAULT_HOUSEHOLD_ID,),
            ).fetchall()
        if len(unlinked) == 1:
            name = unlinked[0]["display_name"]
            print(f"claiming the one unlinked profile: {name}")
        elif len(unlinked) > 1:
            names = ", ".join(u["display_name"] for u in unlinked)
            raise SystemExit(f"several unlinked profiles ({names}) - pass --name to pick one")
    if name is None:
        name = args.email.split("@")[0]
        print(f"no profile to claim - creating a new one named {name!r}")
    args.name = name

    password = getpass.getpass(f"Password for {args.email}: ")
    if len(password) < 8:
        raise SystemExit("password must be at least 8 characters")
    if password != getpass.getpass("Confirm: "):
        raise SystemExit("passwords do not match")

    settings = get_settings()
    print(f"\ncreating auth user {args.email} ...")
    auth_user_id = create_auth_user(args.email, password)
    print(f"  auth user id: {auth_user_id}")

    with psycopg.connect(settings.require_database_url(), row_factory=dict_row) as conn:
        existing = conn.execute(
            "select id, display_name from profiles where auth_user_id = %s",
            (auth_user_id,),
        ).fetchone()
        if existing:
            print(f"  already linked to profile {existing['display_name']} - nothing to do")
            return 0

        claimable = None
        if not args.new_profile:
            claimable = conn.execute(
                """select id, display_name from profiles
                    where auth_user_id is null and display_name = %s
                      and household_id = %s""",
                (args.name, DEFAULT_HOUSEHOLD_ID),
            ).fetchone()

        if claimable:
            conn.execute(
                "update profiles set auth_user_id = %s where id = %s",
                (auth_user_id, claimable["id"]),
            )
            entries = conn.execute(
                """select count(*) as n from entry_ratings where profile_id = %s""",
                (claimable["id"],),
            ).fetchone()["n"]
            print(f"  claimed existing profile {claimable['id']}")
            print(f"  keeps {entries} existing rating(s)")
        else:
            row = conn.execute(
                """insert into profiles (household_id, auth_user_id, display_name)
                   values (%s, %s, %s) returning id""",
                (DEFAULT_HOUSEHOLD_ID, auth_user_id, args.name),
            ).fetchone()
            print(f"  created profile {row['id']}")

        conn.commit()

        print("\nhousehold now:")
        for p in conn.execute(
            """select display_name, auth_user_id is not null as can_sign_in
                 from profiles where household_id = %s order by created_at""",
            (DEFAULT_HOUSEHOLD_ID,),
        ).fetchall():
            state = "can sign in" if p["can_sign_in"] else "no login yet"
            print(f"  {p['display_name']:<20} {state}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
