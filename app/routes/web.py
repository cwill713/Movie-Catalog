from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

from app.auth import current_profile

from app.repositories import titles as titles_repo
from app.repositories import watch_entries as crud


router = APIRouter(tags=["web"])
TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

    
@router.get("/")
def index(request: Request, profile=Depends(current_profile)):
    if profile is None:
        return RedirectResponse(f"/login?next=/", status_code=303)
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={"movies": crud.get_movies(), "profile": profile},
    )


@router.get("/movie-input")
def movie_input(request: Request, profile=Depends(current_profile)):
    if profile is None:
        return RedirectResponse("/login?next=/movie-input", status_code=303)
    return templates.TemplateResponse(
        request=request,
        name="movie_input.html",
        context={"profile": profile},
    )


@router.get("/movie-edit/{movie_id}")
def movie_edit(request: Request, movie_id: UUID, profile=Depends(current_profile)):
    if profile is None:
        return RedirectResponse(f"/login?next=/movie-edit/{movie_id}", status_code=303)
    movie = crud.get_movie(movie_id)
    if movie is None:
        raise HTTPException(status_code=404, detail="Movie not found")
    return templates.TemplateResponse(
        request=request,
        name="movie_edit.html",
        context={"movie": movie, "profile": profile},
    )

@router.get("/catalog")
def catalog(request: Request, profile=Depends(current_profile)):
    if profile is None:
        return RedirectResponse(f"/login?next=/catalog", status_code=303)
    stats = titles_repo.stats()
    return templates.TemplateResponse(
        request=request,
        name="catalog.html",
        context={"total": stats["total"], "genres": titles_repo.genres(), "profile": profile},
    )


@router.get("/title/{imdb_id}")
def title_detail(request: Request, imdb_id: str, profile=Depends(current_profile)):
    if profile is None:
        return RedirectResponse(f"/login?next=/title/{imdb_id}", status_code=303)
    title = titles_repo.get(imdb_id)
    if title is None:
        raise HTTPException(status_code=404, detail="Title not found")
    return templates.TemplateResponse(
        request=request,
        name="title_detail.html",
        context={"title": title, "ratings": titles_repo.ratings_for(imdb_id), "profile": profile},
    )
