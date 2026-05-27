from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.db import init_db
from app.routes.api_movies import router as words_api_router
from app.routes.web import router as web_router

app = FastAPI(title="Netflix Movie Table API", description="A simple API to manage a movie list using FastAPI and SQLite")


init_db()

app.mount("/static", StaticFiles(directory="app/static"), name="static")
app.include_router(web_router)
app.include_router(words_api_router)