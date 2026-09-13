"""Settings load correctly from .env and resolve paths against the repo root."""

from app.config import BASE_DIR, get_settings


def test_settings_load_from_env_file():
    settings = get_settings()
    assert settings.llm_provider in {"ollama", "anthropic"}
    assert settings.min_votes > 0


def test_omdb_key_is_configured():
    """The OMDb key must be present and not the .env.example placeholder."""
    settings = get_settings()
    assert settings.omdb_key_is_set, (
        "OMDB_API_KEY is unset or still the placeholder. Paste your patron key "
        "into .env before running the enrichment script."
    )
    assert settings.require_omdb_key() == settings.omdb_api_key


def test_imdb_data_path_resolves_to_repo_root():
    settings = get_settings()
    assert settings.imdb_data_path.is_absolute()
    assert settings.imdb_data_path.parent == BASE_DIR


def test_imdb_dumps_are_present():
    """The two dumps the ingest script reads should be in place."""
    data = get_settings().imdb_data_path
    for name in ("title.basics.tsv.gz", "title.ratings.tsv.gz"):
        assert (data / name).is_file(), f"missing {name} in {data}"
