"""Catalog search and lookup - the IMDb titles we haven't necessarily seen."""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from app.auth import require_profile

from app.repositories import titles as titles_repo
from app.schemas import CatalogStats, TitleResponse

router = APIRouter(prefix="/api/catalog", tags=["catalog"], dependencies=[Depends(require_profile)])


@router.get("/search", response_model=list[TitleResponse])
def search_catalog(
    q: Optional[str] = Query(None, description="Title text; tolerates typos"),
    genre: Optional[str] = None,
    title_type: Optional[str] = Query(
        None, pattern="^(movie|tvSeries|tvMiniSeries|tvMovie)$"
    ),
    year_from: Optional[int] = Query(None, ge=1888),
    year_to: Optional[int] = Query(None, ge=1888),
    min_rating: Optional[float] = Query(None, ge=0, le=10),
    exclude_watched: bool = False,
    limit: int = Query(60, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> list[TitleResponse]:
    return titles_repo.search(
        q,
        genre=genre,
        title_type=title_type,
        year_from=year_from,
        year_to=year_to,
        min_rating=min_rating,
        exclude_watched=exclude_watched,
        limit=limit,
        offset=offset,
    )


@router.get("/genres", response_model=list[str])
def list_genres() -> list[str]:
    return titles_repo.genres()


@router.get("/stats", response_model=CatalogStats)
def catalog_stats() -> CatalogStats:
    return titles_repo.stats()


@router.get("/{imdb_id}", response_model=TitleResponse)
def get_title(imdb_id: str) -> TitleResponse:
    title = titles_repo.get(imdb_id)
    if title is None:
        raise HTTPException(status_code=404, detail="Title not found")
    return title
