"""Smoke tests for the existing API and web routes.

Deliberately read-only: these run against the real ``app/movies.db``, so
nothing here writes. Validation is exercised with payloads FastAPI rejects
before they reach the database. Phase 2 moves the DB behind a repository,
at which point these get a proper throwaway test database.
"""

import json
from html import unescape as html_unescape

import pytest


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


# ---------------------------------------------------------------------------
# PATCH /api/movies/{id}/rating - re-rating from the poster grid (Phase 5b)
# ---------------------------------------------------------------------------


@pytest.fixture
def a_movie(client):
    """One entry, with its rating restored afterwards so the suite is idempotent."""
    movies = client.get("/api/movies").json()
    if not movies:
        pytest.skip("no movies in the database")
    movie = movies[0]
    yield movie
    client.patch(
        f"/api/movies/{movie['id']}/rating",
        json={"rating": movie["rating"], "review": movie["review"] or ""},
    )


def test_rating_update_returns_the_new_rating(client, a_movie):
    """The response body must carry the value just written, not the previous one.

    This is the assertion that matters. An implementation that selects the row
    *before* the update still persists correctly, so a test that only checked a
    later GET would pass while the endpoint returned a stale rating under a
    response model promising the updated one. The grid hides that, because
    saveRating() refetches the whole list after saving.
    """
    new_rating = 1.0 if a_movie["rating"] != 1.0 else 2.0
    response = client.patch(
        f"/api/movies/{a_movie['id']}/rating", json={"rating": new_rating}
    )
    assert response.status_code == 200
    assert response.json()["rating"] == new_rating


def test_rating_update_persists(client, a_movie):
    """And the value survives into a fresh read."""
    new_rating = 3.5 if a_movie["rating"] != 3.5 else 4.5
    client.patch(f"/api/movies/{a_movie['id']}/rating", json={"rating": new_rating})

    refetched = next(
        m for m in client.get("/api/movies").json() if m["id"] == a_movie["id"]
    )
    assert refetched["rating"] == new_rating


def test_rating_update_leaves_the_rest_of_the_entry_alone(client, a_movie):
    """Only the rating moves - title, year and genres are untouched."""
    response = client.patch(f"/api/movies/{a_movie['id']}/rating", json={"rating": 6.0})
    updated = response.json()
    for field in ("title", "year", "genre_one", "genre_two", "genre_three", "imdb_id"):
        assert updated[field] == a_movie[field], field


@pytest.mark.parametrize("rating", [-0.1, 10.1, 99])
def test_out_of_range_ratings_are_rejected(client, a_movie, rating):
    assert (
        client.patch(
            f"/api/movies/{a_movie['id']}/rating", json={"rating": rating}
        ).status_code
        == 422
    )


def test_rating_update_404s_for_an_entry_that_is_not_ours(client):
    unknown = "11111111-2222-3333-4444-555555555555"
    response = client.patch(f"/api/movies/{unknown}/rating", json={"rating": 5.0})
    assert response.status_code == 404


def test_review_is_saved_and_returned(client, a_movie):
    response = client.patch(
        f"/api/movies/{a_movie['id']}/rating",
        json={"rating": 8.0, "review": "Holds up better than it has any right to."},
    )
    assert response.status_code == 200
    assert response.json()["review"] == "Holds up better than it has any right to."


def test_review_survives_a_refetch(client, a_movie):
    client.patch(
        f"/api/movies/{a_movie['id']}/rating",
        json={"rating": 8.0, "review": "Second viewing was better."},
    )
    refetched = next(
        m for m in client.get("/api/movies").json() if m["id"] == a_movie["id"]
    )
    assert refetched["review"] == "Second viewing was better."


def test_updating_only_the_rating_does_not_destroy_the_review(client, a_movie):
    """Omitting `review` must leave the existing prose alone.

    This is the guard that matters. `review` and `rating` share one row, so an
    implementation that always writes both would wipe a review every time a
    score was nudged - silently, and only noticed long afterwards. Same shape of
    loss as the ingest upsert overwriting OMDb enrichment.
    """
    client.patch(
        f"/api/movies/{a_movie['id']}/rating",
        json={"rating": 7.0, "review": "Worth keeping."},
    )

    response = client.patch(f"/api/movies/{a_movie['id']}/rating", json={"rating": 9.0})

    assert response.json()["rating"] == 9.0
    assert response.json()["review"] == "Worth keeping."


def test_an_empty_review_clears_it(client, a_movie):
    """Sending the field empty is an explicit erase, unlike omitting it."""
    client.patch(
        f"/api/movies/{a_movie['id']}/rating",
        json={"rating": 7.0, "review": "Delete me."},
    )

    response = client.patch(
        f"/api/movies/{a_movie['id']}/rating", json={"rating": 7.0, "review": ""}
    )
    assert response.json()["review"] is None


def test_an_overlong_review_is_rejected(client, a_movie):
    response = client.patch(
        f"/api/movies/{a_movie['id']}/rating",
        json={"rating": 7.0, "review": "x" * 5001},
    )
    assert response.status_code == 422


def test_the_page_ships_every_field_the_renderer_needs(client):
    """The inline payload must carry each field renderMovieCard/openRatingModal read.

    There is now exactly one renderer (app.js), fed by this payload, so the old
    template-vs-JS drift cannot recur. What replaces it as the failure mode is a
    field the renderer reads and the payload does not send - which shows up as
    "undefined" on a card rather than an error.
    """
    html = client.get("/").text
    payload = html.split('id="bootstrap-movies" type="application/json">')[1]
    payload = payload.split("</script>")[0]
    rows = json.loads(html_unescape(payload))
    if not rows:
        pytest.skip("no movies in the database")

    needed = {
        "id", "title", "year", "rating", "review",
        "genre_one", "genre_two", "genre_three", "imdb_id", "poster_url",
    }
    missing = needed - set(rows[0])
    assert not missing, f"bootstrap payload is missing {sorted(missing)}"


def test_the_grid_is_rendered_client_side_only(client):
    """One renderer. The template must not grow a second copy of the card."""
    html = client.get("/").text
    assert 'id="movie-grid" class="poster-grid"></div>' in html, (
        "the template is building cards again - that duplication drifted once already"
    )
