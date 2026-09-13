"""Catalog search behaviour.

Read-only against the real catalog. These skip rather than fail when the
catalog is empty, so a fresh clone that hasn't run the ingest still has a
green suite.
"""

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="module")
def stats(client):
    body = client.get("/api/catalog/stats").json()
    if body["total"] == 0:
        pytest.skip("catalog is empty - run scripts/ingest_imdb.py")
    return body


def test_stats_report_a_populated_catalog(stats):
    assert stats["total"] > 0
    assert "movie" in stats["by_type"]


def test_search_returns_results_ranked_by_popularity(client, stats):
    rows = client.get("/api/catalog/search", params={"limit": 20}).json()
    assert rows
    votes = [r["imdb_votes"] or 0 for r in rows]
    assert votes == sorted(votes, reverse=True)


def test_search_matches_a_known_title(client, stats):
    rows = client.get("/api/catalog/search", params={"q": "Inception"}).json()
    assert any(r["primary_title"] == "Inception" for r in rows)


def test_search_tolerates_a_typo(client, stats):
    """Trigram similarity should still find it - this is why pg_trgm is enabled."""
    rows = client.get("/api/catalog/search", params={"q": "inceptoin"}).json()
    assert any(r["primary_title"] == "Inception" for r in rows), "trigram match failed"


def test_genre_filter_applies_to_every_row(client, stats):
    rows = client.get(
        "/api/catalog/search", params={"genre": "Film-Noir", "limit": 25}
    ).json()
    assert rows
    assert all("Film-Noir" in r["genres"] for r in rows)


def test_year_and_rating_filters_apply(client, stats):
    rows = client.get(
        "/api/catalog/search",
        params={"year_from": 1990, "year_to": 1999, "min_rating": 8.0, "limit": 25},
    ).json()
    assert rows
    for r in rows:
        assert 1990 <= r["start_year"] <= 1999
        assert r["imdb_rating"] >= 8.0


def test_title_type_filter_applies(client, stats):
    rows = client.get(
        "/api/catalog/search", params={"title_type": "tvSeries", "limit": 20}
    ).json()
    assert rows
    assert all(r["title_type"] == "tvSeries" for r in rows)


def test_paging_does_not_repeat_rows(client, stats):
    first = client.get("/api/catalog/search", params={"limit": 10, "offset": 0}).json()
    second = client.get("/api/catalog/search", params={"limit": 10, "offset": 10}).json()
    assert not {r["imdb_id"] for r in first} & {r["imdb_id"] for r in second}


def test_exclude_watched_removes_seen_titles(client, stats):
    """Only meaningful once some entries are linked to catalog rows."""
    seen = [
        r for r in client.get(
            "/api/catalog/search", params={"q": "dragon", "limit": 60}
        ).json() if r["watched"]
    ]
    if not seen:
        pytest.skip("no watch entries linked to catalog titles yet")

    unseen = client.get(
        "/api/catalog/search",
        params={"q": "dragon", "exclude_watched": True, "limit": 60},
    ).json()
    ids = {r["imdb_id"] for r in unseen}
    assert not ids & {r["imdb_id"] for r in seen}
    assert all(not r["watched"] for r in unseen)


def test_lookup_by_imdb_id(client, stats):
    known = client.get("/api/catalog/search", params={"limit": 1}).json()[0]
    row = client.get(f"/api/catalog/{known['imdb_id']}").json()
    assert row["imdb_id"] == known["imdb_id"]
    assert row["primary_title"] == known["primary_title"]


def test_unknown_imdb_id_404s(client, stats):
    assert client.get("/api/catalog/tt00000000").status_code == 404


def test_invalid_title_type_is_rejected(client):
    assert client.get(
        "/api/catalog/search", params={"title_type": "podcast"}
    ).status_code == 422


def test_genres_endpoint_lists_real_genres(client, stats):
    genres = client.get("/api/catalog/genres").json()
    assert "Drama" in genres and "Comedy" in genres


def test_catalog_page_renders(client, stats):
    response = client.get("/catalog")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]


# --- Phase 4: enrichment ---------------------------------------------------

def test_catalog_is_enriched(client, stats):
    """Nearly every title should have OMDb data after enrichment."""
    assert stats["enriched"] / stats["total"] > 0.95


def test_search_results_carry_posters_and_plots(client, stats):
    rows = client.get("/api/catalog/search", params={"limit": 40}).json()
    assert sum(1 for r in rows if r["poster_url"]) / len(rows) > 0.9
    assert sum(1 for r in rows if r["plot"]) / len(rows) > 0.9


def test_poster_urls_are_absolute_https(client, stats):
    """The template drops the img on error; a bad scheme would fail silently."""
    rows = client.get("/api/catalog/search", params={"limit": 40}).json()
    for r in rows:
        if r["poster_url"]:
            assert r["poster_url"].startswith("http"), r["poster_url"]


def test_critic_scores_are_in_range(client, stats):
    rows = client.get("/api/catalog/search", params={"limit": 60}).json()
    for r in rows:
        if r["rt_score"] is not None:
            assert 0 <= r["rt_score"] <= 100
        if r["metascore"] is not None:
            assert 0 <= r["metascore"] <= 100


def test_title_detail_page_renders(client, stats):
    known = client.get("/api/catalog/search", params={"limit": 1}).json()[0]
    response = client.get(f"/title/{known['imdb_id']}")
    assert response.status_code == 200
    assert known["primary_title"] in response.text


def test_title_detail_shows_our_rating(client, stats):
    """A title we've watched should surface the household's score."""
    watched = [
        r for r in client.get(
            "/api/catalog/search", params={"q": "dragon", "limit": 60}
        ).json() if r["watched"]
    ]
    if not watched:
        pytest.skip("no linked watch entries")
    response = client.get(f"/title/{watched[0]['imdb_id']}")
    assert response.status_code == 200
    assert "What we thought" in response.text


def test_title_detail_404s_for_unknown(client, stats):
    assert client.get("/title/tt00000000").status_code == 404
