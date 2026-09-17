from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.auth import require_profile

from app.repositories import watch_entries as crud
from app.schemas import MovieCreate, MovieResponse, MovieDeleteRequest

router = APIRouter(prefix="/api/movies", tags=["movies"], dependencies=[Depends(require_profile)])


class RatingUpdate(BaseModel):
    rating: float = Field(ge=0.0, le=10.0)
    review: Optional[str] = Field(default=None, max_length=5000)


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

@router.patch("/{movie_id}/rating", response_model=MovieResponse)
def update_movie_rating(movie_id: UUID, payload: RatingUpdate) -> MovieResponse:
    """Omitting `review` leaves the existing one untouched; sending it empty clears it."""
    extra = {"review": payload.review} if "review" in payload.model_fields_set else {}
    result = crud.update_rating(movie_id, payload.rating, **extra)
    if result is None:
        raise HTTPException(status_code=404, detail="Movie not found")
    return result