from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request
from fastapi.templating import Jinja2Templates

from app.repositories import titles as titles_repo
from app.repositories import watch_entries as crud


router = APIRouter(tags=["web"])
TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

    
@router.get("/")
def index(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={"movies": crud.get_movies()},
    )


@router.get("/movie-input")
def movie_input(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="movie_input.html",
    )


@router.get("/movie-edit/{movie_id}")
def movie_edit(request: Request, movie_id: UUID):
    movie = crud.get_movie(movie_id)
    if movie is None:
        raise HTTPException(status_code=404, detail="Movie not found")
    return templates.TemplateResponse(
        request=request,
        name="movie_edit.html",
        context={"movie": movie},
    )

@router.get("/catalog")
def catalog(request: Request):
    stats = titles_repo.stats()
    return templates.TemplateResponse(
        request=request,
        name="catalog.html",
        context={"total": stats["total"], "genres": titles_repo.genres()},
    )
