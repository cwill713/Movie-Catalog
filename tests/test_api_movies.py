"""Smoke tests for the existing API and web routes.

Deliberately read-only: these run against the real ``app/movies.db``, so
nothing here writes. Validation is exercised with payloads FastAPI rejects
before they reach the database. Phase 2 moves the DB behind a repository,
at which point these get a proper throwaway test database.
"""

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def test_list_movies_returns_a_list(client):
    response = client.get("/api/movies")
    assert response.status_code == 200
    assert isinstance(response.json(), list)


def test_listed_movies_have_the_expected_shape(client):
    movies = client.get("/api/movies").json()
    if not movies:
        pytest.skip("no movies in the database")
    movie = movies[0]
    for field in ("id", "title", "year", "genre_one", "rating", "imdb_id", "poster_url"):
        assert field in movie
    assert 0.0 <= movie["rating"] <= 10.0


def test_search_by_title(client):
    response = client.get("/api/movies/search", params={"title": "dragon"})
    assert response.status_code == 200


def test_home_page_renders(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]


def test_edit_page_404s_for_unknown_id(client):
    """A well-formed UUID that isn't ours: 404, not a crash."""
    unknown = "11111111-2222-3333-4444-555555555555"
    assert client.get(f"/movie-edit/{unknown}").status_code == 404


def test_edit_page_422s_for_malformed_id(client):
    """Not a UUID at all: rejected at validation, never reaches the database."""
    assert client.get("/movie-edit/99999999").status_code == 422


@pytest.mark.parametrize(
    "payload, reason",
    [
        ({"title": "", "year": 2020, "genre_one": "Drama", "rating": 5.0}, "empty title"),
        ({"title": "X", "year": 1800, "genre_one": "Drama", "rating": 5.0}, "year before 1888"),
        ({"title": "X", "year": 2020, "genre_one": "Drama", "rating": 11.0}, "rating above 10"),
        ({"title": "X", "year": 2020, "rating": 5.0}, "missing genre_one"),
    ],
)
def test_invalid_payloads_are_rejected(client, payload, reason):
    """422 from validation, so nothing is written to the database."""
    assert client.post("/api/movies", json=payload).status_code == 422, reason
