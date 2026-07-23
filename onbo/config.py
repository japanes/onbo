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

    # Request knobs. Every one is optional and, when unset, is simply NOT sent —
    # flagship reasoning models reject the classic sampling params outright
    # ("Unsupported value: 'temperature' does not support 0"), so sending nothing
    # and letting the provider apply its own defaults is the only portable choice.
    # Set them when you run a model that does accept them (Ollama, gpt-4.1, ...).
    temperature: float | None = None
    top_p: float | None = None
    max_tokens: int | None = None
    # Reasoning budget: none | minimal | low | medium | high. "none" keeps the
    # flagships fast and cheap for classification, which is all onbo asks of them.
    # Ignored (dropped) for models that don't reason — see drop_unsupported.
    reasoning_effort: str | None = "none"
    # Let LiteLLM strip params the target model doesn't support instead of
    # erroring, so one settings block can serve reasoning and plain models alike.
    drop_unsupported: bool = True
    # Escape hatch: anything else LiteLLM accepts, passed through verbatim
    # (e.g. seed, presence_penalty, verbosity). Wins over the fields above.
    params: dict = Field(default_factory=dict)

    @field_validator(
        "api_key", "api_base", "temperature", "top_p", "max_tokens", "reasoning_effort",
        mode="before",
    )
    @classmethod
    def _empty_to_none(cls, value: object) -> object:
        # An unset ${VAR:-} expands to "" — treat that as "not configured" so
        # LiteLLM falls back to provider env vars (OPENAI_API_KEY, etc.) and the
        # knobs above stay out of the request entirely.
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
    # Origins allowed to call the API straight from a browser (the widget in
    # token mode). Empty = no cross-origin calls, which is right when your own
    # backend proxies the requests. "*" is refused when tokens are off, so an
    # open instance cannot be driven from any page on the internet.
    cors_origins: list[str] = Field(default_factory=list)

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_origins(cls, value: object) -> object:
        # `${ONBO_CORS_ORIGINS:-}` arrives as a string: "a, b" -> ["a", "b"].
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return [] if value is None else value


class AuthSettings(BaseModel):
    """How a request proves who it comes from (see auth/tokens.py).

    With ``jwt_secret`` set, a caller may send a signed token instead of a bare
    user id, and the profile — department and roles — comes from the token. That
    is the option for products whose directory is too large or too fast-moving to
    mirror into onbo's own users table.
    """
    jwt_secret: str = ""       # shared HS256 secret; empty = token auth off
    jwt_leeway: int = 30       # seconds of clock skew tolerated on `exp`
    # Keep true for local demos, where chat.html types a user id by hand. Set it
    # false in production: then the only way in is a signed token, and knowing
    # someone's id is worth nothing.
    allow_user_id: bool = True

    @field_validator("jwt_secret", mode="before")
    @classmethod
    def _null_to_empty(cls, value: object) -> object:
        # An unset ${ONBO_JWT_SECRET:-} expands to an empty YAML value (null).
        return "" if value is None else value


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
    # Extra headers on every outgoing action call, templated from the signed
    # token's ``context`` claim — e.g. {"Cookie": "active_account={account_id}"}
    # for an API that reads the current workspace from a cookie. A header whose
    # placeholders the token does not fill is left out rather than sent raw.
    # The credential header always wins: this cannot be used to act as someone
    # else. This is where the transport lives, deliberately apart from the data:
    # the caller's backend knows the values, this file knows how your API wants
    # to receive them.
    headers: dict[str, str] = Field(default_factory=dict)
    timeout: float = 10.0
    # TLS verification for outgoing calls. Turn it off ONLY to develop against a
    # self-signed https://localhost — with it off, a man in the middle can read
    # and rewrite everything onbo sends, api_key included. Never in production.
    verify_tls: bool = True

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
    auth: AuthSettings = Field(default_factory=AuthSettings)
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
    """Directory that holds settings.yaml / actions.yaml and the *.example.yaml."""
    return Path(os.environ.get("ONBO_CONFIG_DIR", "config")).expanduser()


def config_file(stem: str) -> Path:
    """``config/<stem>.yaml`` if it exists, else the ``.example.yaml`` shipped here.

    Only the example is tracked in git. Your own file is untracked, so ``git pull``
    can never overwrite the settings and actions you tuned for your product — and
    the repository can still change its example without a merge conflict.

    Falling back to the example means a fresh clone runs before anything is
    copied: the demo works out of the box, and the moment you copy the example to
    ``config/<stem>.yaml`` your version takes over.
    """
    own = config_dir() / f"{stem}.yaml"
    return own if own.exists() else config_dir() / f"{stem}.example.yaml"


def _read_yaml(path: Path) -> dict:
    if not path.exists():
        return {}
    raw = _expand_env(path.read_text(encoding="utf-8"))
    return yaml.safe_load(raw) or {}


@lru_cache(maxsize=1)
def load_settings() -> Settings:
    return Settings(**_read_yaml(config_file("settings")))
