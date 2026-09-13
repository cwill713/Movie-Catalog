from uuid import UUID

from fastapi import APIRouter, HTTPException

from app.repositories import watch_entries as crud
from app.schemas import MovieCreate, MovieResponse, MovieDeleteRequest

router = APIRouter(prefix="/api/movies", tags=["movies"])


@router.get("", response_model=list[MovieResponse])
def list_movies() -> list[MovieResponse]:
    return crud.get_movies()

@router.get("/search", response_model=list[MovieResponse])
def search_movies(title: str) -> list[MovieResponse]:
    return crud.search_movies(title)


@router.post("", response_model=MovieResponse, status_code=201)
def insert_movie(movie: MovieCreate) -> MovieResponse:
    return crud.create_movie(movie)

@router.delete("", status_code=200)
def delete_movies(payload: MovieDeleteRequest):
    deleted = crud.delete_movies(payload.ids)
    return {"deleted": deleted}

@router.put("/{movie_id}", response_model=MovieResponse)
def update_movie(movie_id: UUID, movie: MovieCreate) -> MovieResponse:
    result = crud.update_movie(movie_id, movie)
    if result is None:
        raise HTTPException(status_code=404, detail="Movie not found")
    return result