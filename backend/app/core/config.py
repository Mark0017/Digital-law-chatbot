from functools import lru_cache
from pathlib import Path

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    gemini_api_key: SecretStr
    gemini_model: str = "gemini-3.5-flash"
    gemini_classifier_model: str = "gemini-3.5-flash-lite"
    gemini_fallback_model: str = "gemini-3.7-flash"
    embedding_model: str = "gemini-embedding-2"
    embedding_dimension: int = Field(default=768, ge=128, le=3072)
    web_search_enabled: bool = True
    official_text_gateway_url: str | None = "https://r.jina.ai/"

    supabase_url: str
    supabase_secret_key: SecretStr

    admin_api_key: SecretStr | None = None
    allowed_origins: str = "http://localhost:5173"
    max_chat_message_length: int = Field(default=4000, ge=100, le=20000)
    max_upload_bytes: int = Field(default=26_214_400, ge=1_048_576)

    npc_dpa_url: str = "https://privacy.gov.ph/data-privacy-act/"
    npc_issuances_url: str = (
        "https://privacy.gov.ph/pips-and-pics/advisories-circulars/"
    )
    judiciary_republic_acts_url: str = (
        "https://elibrary.judiciary.gov.ph/republic_acts"
    )
    judiciary_republic_acts_search_url: str = (
        "https://elibrary.judiciary.gov.ph/republic_acts/fetch_ra"
    )
    judiciary_ra_10173_url: str = (
        "https://elibrary.judiciary.gov.ph/thebookshelf/showdocs/2/50253"
    )
    judiciary_ra_10175_url: str = (
        "https://elibrary.judiciary.gov.ph/thebookshelf/showdocs/2/50264"
    )
    judiciary_ra_8792_url: str = (
        "https://elibrary.judiciary.gov.ph/thebookshelf/showdocs/2/3888"
    )
    judiciary_ra_9470_url: str = (
        "https://elibrary.judiciary.gov.ph/thebookshelf/showdocs/2/19575"
    )
    judiciary_ra_10844_url: str = (
        "https://elibrary.judiciary.gov.ph/thebookshelf/showdocs/2/67157"
    )
    judiciary_ra_11032_url: str = (
        "https://elibrary.judiciary.gov.ph/thebookshelf/showdocs/2/83125"
    )
    judiciary_ra_11930_url: str = (
        "https://elibrary.judiciary.gov.ph/thebookshelf/showdocs/2/95572"
    )
    retrieval_match_threshold: float = Field(default=0.68, ge=-1, le=1)
    retrieval_match_count: int = Field(default=8, ge=1, le=20)

    model_config = SettingsConfigDict(
        env_file=Path(__file__).resolve().parents[2] / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @property
    def cors_origins(self) -> list[str]:
        origins = {item.strip().rstrip("/") for item in self.allowed_origins.split(",") if item.strip()}
        if "http://localhost:5173" in origins:
            origins.add("http://127.0.0.1:5173")
        if "http://127.0.0.1:5173" in origins:
            origins.add("http://localhost:5173")
        return sorted(origins)


@lru_cache
def get_settings() -> Settings:
    return Settings()
