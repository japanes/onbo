"""Configuration loading: settings.yaml -> typed Settings.

The config directory defaults to ``./config`` and can be overridden with the
``ONBO_CONFIG_DIR`` environment variable. Raw YAML is expanded first, so values
may reference environment variables as ``${ENV_VAR}`` or with a fallback as
``${ENV_VAR:-default}`` (POSIX-style). An unset variable with no default becomes
an empty string, which the models below treat as "not configured".
"""
from __future__ import annotations

import os
import re
from functools import lru_cache
from pathlib import Path

import yaml
from pydantic import BaseModel, Field, field_validator

# Matches ${VAR} and ${VAR:-default}. Nested braces in the default are not supported.
_ENV_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")


def _expand_env(text: str) -> str:
    """Expand ${VAR} / ${VAR:-default}; unset-or-empty falls back to the default."""

    def repl(match: re.Match[str]) -> str:
        var, default = match.group(1), match.group(2)
        value = os.environ.get(var)
        if value:  # unset or empty -> use default (or "" if none given)
            return value
        return default if default is not None else ""

    return _ENV_RE.sub(repl, text)


class LLMSettings(BaseModel):
    # Model string is resolved by LiteLLM. Defaults to OpenAI (api_key needed);
    # point it at a local GPU server instead with e.g. "ollama_chat/qwen2.5:7b"
    # + api_base http://localhost:11434. Empty strings -> None.
    model: str = "gpt-5.6-terra"
    api_key: str | None = None
    api_base: str | None = None

    @field_validator("api_key", "api_base", mode="before")
    @classmethod
    def _empty_to_none(cls, value: object) -> object:
        # An unset ${VAR:-} expands to "" — treat that as "not configured" so
        # LiteLLM falls back to provider env vars (OPENAI_API_KEY, etc.).
        if isinstance(value, str) and value.strip() == "":
            return None
        return value


class STTSettings(BaseModel):
    enabled: bool = False
    # Whisper model size, e.g. "base", "small", "medium", "large-v3".
    model: str = "base"
    # "cuda" to use the local GPU, "cpu" otherwise. On GPU-load failure the STT
    # service falls back to CPU int8 automatically (see stt/whisper.py).
    device: str = "cpu"
    compute_type: str = "int8"


class TTSSettings(BaseModel):
    # Voice output is deferred; kept here as an explicit, off-by-default extension point.
    enabled: bool = False
    model: str = "piper"


class QdrantSettings(BaseModel):
    url: str = "http://localhost:6333"
    collection: str = "onbo"


class EmbeddingSettings(BaseModel):
    """Which model turns text into vectors, and where it runs.

    ``provider``: "local" runs the model on this machine via fastembed, "api"
    routes it through LiteLLM to a hosted vendor (OpenAI, Gemini, Voyage,
    Cohere...), and "auto" infers it from the model string. Empty key/base ->
    None, so LiteLLM can pick up OPENAI_API_KEY & co from the environment.
    """

    model: str = "text-embedding-3-large"
    provider: str = "auto"
    api_key: str | None = None
    api_base: str | None = None

    @field_validator("api_key", "api_base", mode="before")
    @classmethod
    def _empty_to_none(cls, value: object) -> object:
        if isinstance(value, str) and value.strip() == "":
            return None
        return value

    @field_validator("provider", mode="before")
    @classmethod
    def _known_provider(cls, value: object) -> str:
        provider = str(value or "auto").strip().lower() or "auto"
        if provider not in {"auto", "local", "api"}:
            raise ValueError("embeddings.provider must be auto, local or api")
        return provider


class MediaSettings(BaseModel):
    """Where walkthrough videos (attached to Q&A via ``video_url``) live.

    ``dir`` is served at ``/media`` by the web channel. ``base_url`` is prefixed
    onto ``/media/...`` links for channels where a site-relative path is useless
    (Telegram); leave it empty for the web UI, which serves ``/media`` itself.
    """
    dir: str = "media"
    base_url: str = ""

    @field_validator("base_url", mode="before")
    @classmethod
    def _null_to_empty(cls, value: object) -> object:
        # `${MEDIA_BASE_URL:-}` expands to an empty YAML value (null); treat that
        # (and an explicit null) as "not configured" rather than failing.
        return "" if value is None else value


class WelcomeSettings(BaseModel):
    """Proactive first-contact digest (see handlers/welcome.py).

    ``video`` / ``text_overrides`` map a department **or** role name to a starter
    video URL / a hand-written text that replaces the generated digest.
    """
    enabled: bool = True
    video: dict[str, str] = Field(default_factory=dict)
    text_overrides: dict[str, str] = Field(default_factory=dict)

    @field_validator("video", "text_overrides", mode="before")
    @classmethod
    def _null_to_empty(cls, value: object) -> object:
        # `${VAR:-}` / an omitted mapping expands to null; treat as "{}".
        return {} if value is None else value


class FeatureSettings(BaseModel):
    """Top-level on/off switches for whole subsystems.

    Turning one off removes the corresponding web mount and/or routing path, so a
    deployment can run a minimal slice (e.g. only the ``llm.json`` manifest, or an
    actions-only assistant with no knowledge base). ``chat`` gates the /chat,
    /voice and /confirm endpoints; ``actions``/``rag`` gate what the classifier is
    even allowed to route.
    """
    chat: bool = True          # /chat, /voice, /confirm text pipeline
    admin: bool = True         # /admin KB management panel + API
    media: bool = True         # /media static walkthrough videos
    llm_manifest: bool = True  # /llm.json manifest for external LLM agents
    welcome: bool = True       # proactive first-contact digest (/welcome, /start)
    actions: bool = True       # classifier may route profile actions/pipelines
    rag: bool = True           # classifier may answer from the knowledge base


class ChannelSettings(BaseModel):
    enabled: bool = False
    accept_voice: bool = False
    token: str | None = None  # e.g. Telegram bot token
    port: int = 18000         # web channel listen port


class ProductSettings(BaseModel):
    """The target software's backend that actions call over HTTP.

    Empty ``base_url`` = demo/dry-run: actions validate and report what they
    *would* have called, but make no real request (nothing to change against).
    """
    # Human-facing identity, surfaced in llm.json for external agents.
    name: str = ""
    description: str = ""
    # None-tolerant: `${VAR:-}` expands to an empty YAML value (null), not "".
    base_url: str | None = ""
    api_key: str | None = ""
    auth_header: str = "Authorization"
    auth_scheme: str | None = "Bearer"   # header value = "<scheme> <api_key>"; empty = raw key
    timeout: float = 10.0

    @field_validator("name", "description", mode="before")
    @classmethod
    def _null_to_empty(cls, value: object) -> object:
        # `${VAR:-}` expands to an empty YAML value (null); treat as "".
        return "" if value is None else value


class Settings(BaseModel):
    llm: LLMSettings = Field(default_factory=LLMSettings)
    stt: STTSettings = Field(default_factory=STTSettings)
    tts: TTSSettings = Field(default_factory=TTSSettings)
    qdrant: QdrantSettings = Field(default_factory=QdrantSettings)
    embeddings: EmbeddingSettings = Field(default_factory=EmbeddingSettings)
    media: MediaSettings = Field(default_factory=MediaSettings)
    welcome: WelcomeSettings = Field(default_factory=WelcomeSettings)
    features: FeatureSettings = Field(default_factory=FeatureSettings)
    product: ProductSettings = Field(default_factory=ProductSettings)
    postgres_dsn: str = "postgresql+psycopg://onbo:onbo@localhost:5432/onbo"
    redis_url: str = "redis://localhost:6379/0"
    channels: dict[str, ChannelSettings] = Field(default_factory=dict)


class ConfigError(RuntimeError):
    """Configuration that cannot work. Reported as one line, without a traceback."""


# The example values .env.example ships. Copied over verbatim they reach the vendor
# as if they were real keys and come back as a 401 deep inside a library traceback,
# so catch them before the first request instead.
_PLACEHOLDER_KEYS = frozenset({"sk-...", "sk-proj-...", "sk-ant-...", "AIza...", "pa-..."})


def check_env_keys() -> None:
    """Raise if an API key in the environment is still a placeholder."""
    for name, value in sorted(os.environ.items()):
        if name.endswith("_API_KEY") and value.strip() in _PLACEHOLDER_KEYS:
            raise ConfigError(
                f"{name}={value.strip()} is the example value from .env.example, not a key. "
                f"Put a real key in .env, or drop {name} and switch to models that need none: "
                "EMBED_MODEL=intfloat/multilingual-e5-large plus an Ollama LLM_MODEL."
            )


def config_dir() -> Path:
    """Directory that holds settings.yaml / actions.yaml / seed_faq.yaml."""
    return Path(os.environ.get("ONBO_CONFIG_DIR", "config")).expanduser()


def _read_yaml(path: Path) -> dict:
    if not path.exists():
        return {}
    raw = _expand_env(path.read_text(encoding="utf-8"))
    return yaml.safe_load(raw) or {}


@lru_cache(maxsize=1)
def load_settings() -> Settings:
    return Settings(**_read_yaml(config_dir() / "settings.yaml"))
