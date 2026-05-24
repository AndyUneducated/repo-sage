"""Centralised settings, sourced from environment / .env.

Anything mutable at runtime should flow through `Settings`; tests can patch a
single object instead of monkey-patching env vars.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ---- LLM ----
    # Defaults to a local Ollama instance so `reposage ask` works out of the
    # box without API keys. Hosted providers are still one env edit away
    # (DD-014).
    llm_provider: Literal["ollama", "anthropic", "openai", "azure", "local"] = "ollama"
    anthropic_api_key: str | None = None
    openai_api_key: str | None = None
    llm_model: str = "ollama_chat/qwen2.5-coder:7b"
    router_model: str = "ollama_chat/qwen2.5-coder:7b"
    # LiteLLM picks this up automatically when the model string starts with
    # `ollama` / `ollama_chat`. We surface it explicitly so it shows up in
    # `.env.example` and in `Settings` for tests and observability.
    ollama_api_base: str = "http://localhost:11434"

    # ---- Embedding / rerank ----
    embed_model: str = "BAAI/bge-en-v1.5"
    reranker_model: str = "BAAI/bge-reranker-v2-m3"
    embed_device: Literal["cpu", "cuda", "mps"] = "cpu"
    embed_dim: int = 768

    # ---- Storage ----
    sqlite_path: Path = Field(default=Path("./data/reposage.db"))
    hnsw_data_dir: Path = Field(default=Path("./data/hnsw"))
    bm25_index_dir: Path = Field(default=Path("./data/bm25"))

    # ---- HNSW (Go service) ----
    hnsw_grpc_addr: str = "localhost:50051"
    hnsw_m: int = 16
    hnsw_ef_construction: int = 200
    hnsw_ef_search: int = 64

    # ---- GitHub App ----
    github_app_id: str | None = None
    github_app_private_key_path: Path | None = None
    github_webhook_secret: str | None = None

    # ---- Observability ----
    otel_exporter_otlp_endpoint: str = "http://localhost:4317"
    otel_service_name: str = "reposage"
    log_level: str = "INFO"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
