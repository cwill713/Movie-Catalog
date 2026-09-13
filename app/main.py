from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.db import close_pool, init_pool
from app.routes.api_movies import router as movies_api_router
from app.routes.web import router as web_router


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

app.mount("/static", StaticFiles(directory="app/static"), name="static")
app.include_router(web_router)
app.include_router(movies_api_router)
