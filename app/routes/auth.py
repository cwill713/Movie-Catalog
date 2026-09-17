"""Sign in and out.

No registration route by design - the app is invite-only and accounts are
created with scripts/create_user.py.
"""

from pathlib import Path

from fastapi import APIRouter, Form, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

from app.auth import SESSION_COOKIE, current_profile, sign_in

router = APIRouter(tags=["auth"])
TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


@router.get("/login")
def login_page(request: Request, next: str = "/"):
    if current_profile(request) is not None:
        return RedirectResponse(next, status_code=303)
    return templates.TemplateResponse(
        request=request, name="login.html", context={"next": next, "error": None}
    )


@router.post("/login")
def login(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    next: str = Form("/"),
):
    try:
        tokens = sign_in(email.strip(), password)
    except Exception:
        return templates.TemplateResponse(
            request=request,
            name="login.html",
            context={"next": next, "error": "Incorrect email or password."},
            status_code=401,
        )

    # Only ever redirect somewhere inside this app. An attacker who can choose
    # ?next= should not be able to bounce a freshly-authenticated user offsite.
    target = next if next.startswith("/") and not next.startswith("//") else "/"

    response = RedirectResponse(target, status_code=303)
    response.set_cookie(
        SESSION_COOKIE,
        tokens["access_token"],
        httponly=True,               # not readable from JavaScript
        samesite="lax",              # survives normal navigation, blocks CSRF-by-form
        secure=True,                 # HTTPS everywhere, local included (ADR-015)
        max_age=tokens.get("expires_in", 3600),
        path="/",
    )
    return response


@router.post("/logout")
@router.get("/logout")
def logout():
    response = RedirectResponse("/login", status_code=303)
    response.delete_cookie(SESSION_COOKIE, path="/")
    return response
