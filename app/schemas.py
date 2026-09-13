from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, Field

class MovieCreate(BaseModel):
    title: str = Field(min_length=1, max_length=100)
    year: int = Field(ge=1888, le=datetime.now().year)
    genre_one: str = Field(min_length=1, max_length=50)
    genre_two: Optional[str] = Field(default=None, max_length=50)
    genre_three: Optional[str] = Field(default=None, max_length=50)
    rating: float = Field(ge=0.0, le=10.0)

class MovieResponse(MovieCreate):
    id: UUID
    imdb_id: Optional[str] = None
    poster_url: Optional[str] = None

class MovieDeleteRequest(BaseModel):
    ids: list[UUID] = Field(min_length=1)

class TitleResponse(BaseModel):
    """A catalog title. OMDb fields are null until Phase 4 enrichment runs."""

    imdb_id: str
    title_type: str
    primary_title: str
    start_year: Optional[int] = None
    end_year: Optional[int] = None
    runtime_minutes: Optional[int] = None
    genres: list[str] = Field(default_factory=list)
    imdb_rating: Optional[float] = None
    imdb_votes: Optional[int] = None

    plot: Optional[str] = None
    director: Optional[str] = None
    actors: Optional[list[str]] = None
    content_rating: Optional[str] = None
    poster_url: Optional[str] = None
    metascore: Optional[int] = None
    rt_score: Optional[int] = None
    watched: bool = False


class CatalogStats(BaseModel):
    total: int
    by_type: dict[str, int]
    enriched: int
