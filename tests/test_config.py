"""Env-var expansion in settings + None-tolerance of the product block."""
from __future__ import annotations

from onbo.config import ProductSettings, Settings, _expand_env


def test_expand_uses_env_value(monkeypatch):
    monkeypatch.setenv("ONBO_X", "hello")
    assert _expand_env("v=${ONBO_X}") == "v=hello"


def test_expand_falls_back_to_default_when_unset(monkeypatch):
    monkeypatch.delenv("ONBO_MISSING", raising=False)
    assert _expand_env("v=${ONBO_MISSING:-def}") == "v=def"


def test_expand_empty_var_uses_default(monkeypatch):
    # An exported-but-empty var must behave like "unset" -> take the default.
    monkeypatch.setenv("ONBO_EMPTY", "")
    assert _expand_env("v=${ONBO_EMPTY:-def}") == "v=def"


def test_expand_no_default_becomes_empty(monkeypatch):
    monkeypatch.delenv("ONBO_MISSING", raising=False)
    assert _expand_env("a=${ONBO_MISSING}b") == "a=b"


def test_product_none_is_not_configured():
    # `${PRODUCT_API_BASE:-}` -> empty YAML value -> None; must not blow up and
    # must read as "no backend configured" (empty base_url).
    p = ProductSettings(base_url=None, api_key=None, auth_scheme=None)
    assert not p.base_url
    assert p.timeout == 10.0


def test_settings_defaults_have_product():
    s = Settings()
    assert isinstance(s.product, ProductSettings)
    assert not s.product.base_url  # unconfigured by default -> dry-run mode
