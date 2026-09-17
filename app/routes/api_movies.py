from datetime import date, timedelta
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator

from app.auth import require_profile

from app.repositories import watch_entries as crud
from app.schemas import MovieCreate, MovieResponse, MovieDeleteRequest

router = APIRouter(prefix="/api/movies", tags=["movies"], dependencies=[Depends(require_profile)])


class RatingUpdate(BaseModel):
    rating: float = Field(ge=0.0, le=10.0)
    review: Optional[str] = Field(default=None, max_length=5000)
    tags: Optional[list[str]] = Field(default=None, max_length=25)
    watched_on: Optional[date] = None

    @field_validator("watched_on")
    @classmethod
    def _within_living_memory(cls, value: Optional[date]) -> Optional[date]:
        """Catch typos, without pretending to know the user's intent.

        A mistyped year (2206 for 2026) would otherwise sit in the data and
        quietly skew any future recency weighting. The bounds are deliberately
        loose: 1888 matches the year field's floor elsewhere, and tomorrow rather
        than today allows for the client being a timezone ahead of the server.
        """
        if value is None:
            return value
        if value.year < 1888:
            raise ValueError("earlier than the first films")
        if value > date.today() + timedelta(days=1):
            raise ValueError("in the future")
        return value


@router.get("", response_model=list[MovieResponse])
def list_movies() -> list[MovieResponse]:
    return crud.get_movies()

@router.get("/search", response_model=list[MovieResponse])
def search_movies(title: str) -> list[MovieResponse]:
    return crud.search_movies(title)


@router.get("/tags", response_model=list[str])
def list_tags() -> list[str]:
    """Tags already in use in this household, most-used first."""
    return crud.all_tags()


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
    """Omitting a field leaves it untouched; sending it empty clears it.

    That applies to `review` and `tags` alike - the distinction matters because
    both share the entry with the rating, so treating absent as empty would wipe
    them every time a score was nudged.
    """
    sent = payload.model_fields_set
    extra: dict[str, object] = {}
    if "review" in sent:
        extra["review"] = payload.review
    if "tags" in sent:
        extra["tags"] = payload.tags or []
    if "watched_on" in sent:
        extra["watched_on"] = payload.watched_on
    result = crud.update_rating(movie_id, payload.rating, **extra)
    if result is None:
        raise HTTPException(status_code=404, detail="Movie not found")
    return result