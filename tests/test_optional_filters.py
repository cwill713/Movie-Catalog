"""Every filter is genuinely optional.

This is the path that breaks quietly. A filter query written without explicit
parameter casts still works while you test it with a value, then fails the first
time the filter is omitted - which is the entire reason it is optional. The
failure is `AmbiguousParameter: could not determine data type of parameter $N`:
psycopg3 sends a `None` (or a `str`) as an untyped value, and `IS NULL` gives
Postgres nothing to infer a type from.

So these tests exercise the *absence* of each filter, not its presence, and
check two separate things:

  * omitting a filter does not raise
  * omitting a filter does not silently constrain the results
"""

import itertools

import pytest

from app.repositories import titles as repo

# A value for each optional filter, used to build every supplied/omitted combo.
FILTERS = {
    "query": "dragon",
    "genre": "Action",
    "title_type": "movie",
    "year_from": 2000,
    "year_to": 2020,
    "min_rating": 7.0,
}


@pytest.fixture(autouse=True)
def _bound(as_profile):
    """These call the repository directly, so they need an identity bound."""
    if not repo.search(limit=1):
        pytest.skip("catalog is empty - run scripts/ingest_imdb.py")


def _combinations():
    names = list(FILTERS)
    for size in range(len(names) + 1):
        yield from itertools.combinations(names, size)


@pytest.mark.parametrize(
    "supplied", list(_combinations()), ids=lambda c: "+".join(c) or "none"
)
def test_every_combination_of_filters_runs(supplied):
    """All 64 supplied/omitted combinations, including supplying nothing."""
    repo.search(**{name: FILTERS[name] for name in supplied}, limit=3)


@pytest.mark.parametrize("exclude_watched", [False, True])
def test_exclude_watched_works_with_no_other_filters(exclude_watched):
    repo.search(exclude_watched=exclude_watched, limit=5)


def test_no_filters_returns_a_broad_mix():
    """With nothing supplied, results should span types, years and ratings."""
    rows = repo.search(limit=200)
    assert len(rows) == 200
    assert len({g for r in rows for g in r["genres"]}) > 5
    assert len({r["title_type"] for r in rows}) > 1
    years = [r["start_year"] for r in rows if r["start_year"]]
    assert max(years) - min(years) > 20


@pytest.mark.parametrize(
    "kwargs, predicate, description",
    [
        ({"genre": "Horror"}, lambda r: "Horror" in r["genres"], "genre"),
        ({"title_type": "tvSeries"}, lambda r: r["title_type"] == "tvSeries", "type"),
        ({"year_from": 2020}, lambda r: r["start_year"] >= 2020, "year_from"),
        ({"year_to": 1950}, lambda r: r["start_year"] <= 1950, "year_to"),
        ({"min_rating": 9.0}, lambda r: r["imdb_rating"] >= 9.0, "min_rating"),
    ],
    ids=lambda v: v if isinstance(v, str) else "",
)
def test_a_supplied_filter_constrains(kwargs, predicate, description):
    rows = repo.search(**kwargs, limit=100)
    assert rows, f"{description} filter matched nothing"
    assert all(predicate(r) for r in rows), f"{description} filter let a row through"


def test_an_omitted_filter_does_not_constrain():
    """Filtering by genre alone must leave every title_type reachable."""
    rows = repo.search(genre="Horror", limit=200)
    assert len({r["title_type"] for r in rows}) > 1, (
        "omitting title_type still narrowed the results - an unsupplied filter "
        "is being applied"
    )
