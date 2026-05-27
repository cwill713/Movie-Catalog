from pydantic import BaseModel, Field
from datetime import datetime
from typing import Optional

class MovieCreate(BaseModel):
    title: str = Field(min_length=1, max_length=100)
    year: int = Field(ge=1888, le=datetime.now().year)
    genre_one: str = Field(min_length=1, max_length=50)
    genre_two: Optional[str] = Field(default=None, max_length=50)
    genre_three: Optional[str] = Field(default=None, max_length=50)
    rating: float = Field(ge=0.0, le=10.0)

class MovieResponse(MovieCreate):
    id: int

class MovieDeleteRequest(BaseModel):
    ids: list[int] = Field(min_length=1)