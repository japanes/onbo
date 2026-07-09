"""Configuration loading: settings.yaml -> typed Settings.

The config directory defaults to ``./config`` and can be overridden with the
``ONBO_CONFIG_DIR`` environment variable. Raw YAML is passed through
``os.path.expandvars`` first, so values may reference secrets as ``${ENV_VAR}``.
"""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

import yaml
from pydantic import BaseModel, Field


class LLMSettings(BaseModel):
    # Model string is resolved by LiteLLM (e.g. "ollama/llama3", "gpt-4o-mini").
    model: str = "gpt-4o-mini"
    api_key: str | None = None
    api_base: str | None = None


class STTSettings(BaseModel):
    enabled: bool = False
    model: str = "faster-whisper"


class TTSSettings(BaseModel):
    # Voice output is deferred; kept here as an explicit, off-by-default extension point.
    enabled: bool = False
    model: str = "piper"


class QdrantSettings(BaseModel):
    url: str = "http://localhost:6333"
    collection: str = "onbo"


class EmbeddingSettings(BaseModel):
    model: str = "BAAI/bge-m3"


class ChannelSettings(BaseModel):
    enabled: bool = False
    accept_voice: bool = False
    token: str | None = None  # e.g. Telegram bot token


class Settings(BaseModel):
    llm: LLMSettings = Field(default_factory=LLMSettings)
    stt: STTSettings = Field(default_factory=STTSettings)
    tts: TTSSettings = Field(default_factory=TTSSettings)
    qdrant: QdrantSettings = Field(default_factory=QdrantSettings)
    embeddings: EmbeddingSettings = Field(default_factory=EmbeddingSettings)
    postgres_dsn: str = "postgresql+psycopg://onbo:onbo@localhost:5432/onbo"
    redis_url: str = "redis://localhost:6379/0"
    channels: dict[str, ChannelSettings] = Field(default_factory=dict)


def config_dir() -> Path:
    """Directory that holds settings.yaml / actions.yaml / seed_faq.yaml."""
    return Path(os.environ.get("ONBO_CONFIG_DIR", "config")).expanduser()


def _read_yaml(path: Path) -> dict:
    if not path.exists():
        return {}
    raw = os.path.expandvars(path.read_text(encoding="utf-8"))
    return yaml.safe_load(raw) or {}


@lru_cache(maxsize=1)
def load_settings() -> Settings:
    return Settings(**_read_yaml(config_dir() / "settings.yaml"))
