"""Application settings, loaded from the repo-root .env file.

Values are read case-insensitively from the environment, so the field
``omdb_api_key`` is populated by ``OMDB_API_KEY``. Real values live in ``.env``
(gitignored); ``.env.example`` documents the full set with dummy values.
"""

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent

_PLACEHOLDER = "your_omdb_key_here"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=BASE_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- OMDb ---
    omdb_api_key: str = ""

    # --- Supabase (Phase 2) ---
    database_url: str = ""
    supabase_url: str = ""
    supabase_anon_key: str = ""
    supabase_service_key: str = ""

    # --- LLM provider ---
    llm_provider: str = "ollama"
    ollama_base_url: str = "http://localhost:11434"
    ollama_chat_model: str = "llama3.1"
    ollama_embed_model: str = "nomic-embed-text"
    anthropic_api_key: str = ""

    # --- Ingest ---
    min_votes: int = 5000
    imdb_data_dir: str = "data"

    @property
    def imdb_data_path(self) -> Path:
        """Absolute path to the IMDb dumps, resolved against the repo root."""
        path = Path(self.imdb_data_dir)
        return path if path.is_absolute() else BASE_DIR / path

    @property
    def omdb_key_is_set(self) -> bool:
        return bool(self.omdb_api_key) and self.omdb_api_key != _PLACEHOLDER

    def require_omdb_key(self) -> str:
        """Return the OMDb key, or explain how to set one."""
        if not self.omdb_key_is_set:
            raise RuntimeError(
                "OMDB_API_KEY is not set in .env. Get a patron key from "
                "https://www.omdbapi.com/apikey.aspx (Patreon tab) and paste it "
                "into .env as OMDB_API_KEY=<key>."
            )
        return self.omdb_api_key


@lru_cache
def get_settings() -> Settings:
    """Cached settings singleton. Import this, not ``Settings`` directly."""
    return Settings()
