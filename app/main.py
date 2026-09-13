from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool

from app.auth import bind_identity, resolve_profile


from app.db import close_pool, init_pool
from app.routes.api_movies import router as movies_api_router
from app.routes.auth import router as auth_router
from app.routes.catalog import router as catalog_router
from app.routes.web import router as web_router


class IdentityMiddleware:
    """Resolve the signed-in profile once, before anything else runs.

    Pure ASGI middleware, NOT `@app.middleware("http")`. That decorator uses
    Starlette's BaseHTTPMiddleware, which spawns the downstream application as
    a separate anyio task *before* running the dispatch body. Tasks copy the
    context at spawn time, so a ContextVar set in the dispatch function is
    already too late and never reaches the endpoint.

    That failure is silent and environment-dependent: the TestClient happened
    to paper over it, so the whole suite passed while every authenticated page
    returned 500 in a browser.

    Pure ASGI middleware runs in the same task as the app it wraps, so values
    set here propagate to the endpoint, its dependencies, and the repositories -
    which is what binds row-level security to the right person.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        request = Request(scope, receive)
        # The lookup is sync psycopg, so keep it off the event loop. The
        # ContextVars are set back out here, in the request's own context.
        profile = await run_in_threadpool(resolve_profile, request)
        scope.setdefault("state", {})["profile"] = profile
        bind_identity(profile)
        await self.app(scope, receive, send)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_pool()
    yield
    close_pool()


app = FastAPI(
    title="Movie Catalog API",
    description="A personal movie and TV catalog with recommendations",
    lifespan=lifespan,
)

app.add_middleware(IdentityMiddleware)

app.mount("/static", StaticFiles(directory="app/static"), name="static")
app.include_router(auth_router)
app.include_router(web_router)
app.include_router(movies_api_router)
app.include_router(catalog_router)
