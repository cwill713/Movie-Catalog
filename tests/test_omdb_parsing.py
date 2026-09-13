"""Parsing of OMDb responses.

Pure functions, no network. The awkward cases are all real shapes OMDb returns:
the string "N/A" instead of null, scores buried in a Ratings array in two
different formats, dates as "26 Mar 2010", and actors as one comma-joined
string.
"""

import sys

import pytest

from app.config import BASE_DIR

sys.path.insert(0, str(BASE_DIR / "scripts"))
from enrich_omdb import (  # noqa: E402
    as_int,
    clean,
    miss,
    parse_released,
    parse_scores,
    to_row,
)


@pytest.mark.parametrize(
    "value, expected",
    [
        ("PG-13", "PG-13"),
        ("N/A", None),          # OMDb's null
        ("", None),
        ("  spaced  ", "spaced"),
        (None, None),
    ],
)
def test_clean_treats_na_as_missing(value, expected):
    assert clean(value) == expected


@pytest.mark.parametrize(
    "value, expected",
    [("75", 75), ("75/100", 75), ("99%", 99), ("1,234", 1234), ("N/A", None), (None, None), ("abc", None)],
)
def test_as_int_strips_punctuation(value, expected):
    assert as_int(value) == expected


@pytest.mark.parametrize(
    "value, expected",
    [
        ("26 Mar 2010", "2010-03-26"),
        ("01 Jan 1999", "1999-01-01"),
        ("N/A", None),
        ("not a date", None),   # must not raise
        (None, None),
    ],
)
def test_parse_released(value, expected):
    assert parse_released(value) == expected


def test_parse_scores_picks_out_rt_and_metacritic():
    ratings = [
        {"Source": "Internet Movie Database", "Value": "8.1/10"},
        {"Source": "Rotten Tomatoes", "Value": "99%"},
        {"Source": "Metacritic", "Value": "75/100"},
    ]
    assert parse_scores(ratings) == (99, 75)


@pytest.mark.parametrize("ratings", [None, [], [{"Source": "Internet Movie Database", "Value": "8.1/10"}]])
def test_parse_scores_handles_absent_sources(ratings):
    assert parse_scores(ratings) == (None, None)


def test_to_row_maps_a_full_response():
    payload = {
        "Title": "How to Train Your Dragon",
        "Rated": "PG",
        "Released": "26 Mar 2010",
        "Director": "Dean DeBlois, Chris Sanders",
        "Writer": "William Davies",
        "Actors": "Jay Baruchel, Gerard Butler, Christopher Mintz-Plasse",
        "Plot": "Long ago up North on the Island of Berk...",
        "Language": "English",
        "Country": "United Kingdom, France, United States",
        "Awards": "Nominated for 2 Oscars.",
        "Poster": "https://example.invalid/p.jpg",
        "Metascore": "75",
        "Ratings": [{"Source": "Rotten Tomatoes", "Value": "99%"}],
    }
    row = to_row("tt0892769", payload)
    assert row["imdb_id"] == "tt0892769"
    assert row["content_rating"] == "PG"
    assert row["released_on"] == "2010-03-26"
    assert row["actors"] == [
        "Jay Baruchel", "Gerard Butler", "Christopher Mintz-Plasse",
    ]
    assert row["rt_score"] == 99
    assert row["metascore"] == 75
    assert row["omdb_status"] == "ok"


def test_to_row_survives_a_response_that_is_all_na():
    """Older and obscure titles come back almost entirely 'N/A'."""
    payload = {k: "N/A" for k in (
        "Rated", "Released", "Director", "Writer", "Actors", "Plot",
        "Language", "Country", "Awards", "Poster", "Metascore",
    )}
    row = to_row("tt0000001", payload)
    assert row["imdb_id"] == "tt0000001"
    assert row["omdb_status"] == "ok"
    assert row["actors"] is None
    for field in ("plot", "poster_url", "content_rating", "released_on", "metascore"):
        assert row[field] is None


def test_metascore_falls_back_to_the_ratings_array():
    payload = {"Metascore": "N/A", "Ratings": [{"Source": "Metacritic", "Value": "62/100"}]}
    assert to_row("tt1", payload)["metascore"] == 62


def test_miss_produces_a_complete_null_row():
    """A miss must still set every column, or executemany gets ragged keys."""
    row = miss("tt404", "not_found")
    assert row["omdb_status"] == "not_found"
    assert row["imdb_id"] == "tt404"
    for key, value in row.items():
        if key not in ("imdb_id", "omdb_status"):
            assert value is None


def test_miss_and_to_row_agree_on_columns():
    """Both go through the same UPDATE, so they must have identical keys."""
    populated = to_row("tt1", {"Plot": "x", "Ratings": []})
    assert set(populated) == set(miss("tt1", "error"))
