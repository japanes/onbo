"""Shared HTTP caller + a generic, config-driven action handler.

An action's ``api:`` block (see registry.ApiSpec) declares how to call the
target product's backend. This module turns that into a real request. If no
``product.base_url`` is configured it does NOT fake success — it returns a
``dry_run`` result that honestly says what it *would* have called.

Two ways to use it:
- ``GenericHTTPHandler`` — a zero-Python action: just an ``api:`` block in
  actions.yaml, no handler file. The registry wires it up automatically.
- Hand-written handlers (custom validate) call ``call_product_api(spec, ...)``
  from their ``execute`` to reuse the same request/dry-run logic.
"""
from __future__ import annotations

from ...config import load_settings
from ...core.schemas import ActionResult, Profile, ResultStatus
from .base import ActionHandler


def _render(value, ctx: dict):
    """Template a str with {user_id}/{param}; leave non-str values untouched."""
    if isinstance(value, str):
        try:
            return value.format(**ctx)
        except (KeyError, IndexError):
            return value
    return value


def _render_map(mapping: dict, ctx: dict) -> dict:
    return {key: _render(val, ctx) for key, val in (mapping or {}).items()}


async def call_product_api(spec, profile: Profile, entities: dict) -> ActionResult:
    """Execute ``spec.api`` against the configured product backend (or dry-run)."""
    api = getattr(spec, "api", None)
    name = getattr(spec, "name", None)
    description = getattr(spec, "description", "") or (name or "действие")
    ctx = {"user_id": profile.user_id, **entities}

    if api is None or not (api.url or api.path):
        # Nothing declared to call: treat as an unconfigured action, not a success.
        return ActionResult(
            status=ResultStatus.dry_run,
            action=name,
            message=f"«{description}»: не задан вызов API продукта (блок api в actions.yaml).",
        )

    settings = load_settings()
    product = settings.product
    body = _render_map(api.body, ctx)
    query = _render_map(api.query, ctx)
    success = _render(api.success_message, ctx) if api.success_message else f"Готово: {description}."

    if api.url:
        # Absolute address straight from actions.yaml: no product.base_url needed,
        # so one install can drive several backends (or a product whose API lives
        # on a host that has nothing to do with where onbo runs).
        url = _render(api.url, ctx)
    else:
        path = _render(api.path, ctx)
        if not product.base_url:
            return ActionResult(
                status=ResultStatus.dry_run,
                action=name,
                message=(
                    f"Демо-режим: «{description}» не выполнено по-настоящему "
                    f"(PRODUCT_API_BASE не задан, а в действии указан относительный "
                    f"path — задайте абсолютный url в actions.yaml). "
                    f"Вызвал бы {api.method} {path}."
                ),
            )
        url = product.base_url.rstrip("/") + "/" + path.lstrip("/")
    headers = {}
    if product.api_key:
        headers[product.auth_header] = f"{product.auth_scheme or ''} {product.api_key}".strip()

    try:
        import httpx

        async with httpx.AsyncClient(timeout=product.timeout) as client:
            resp = await client.request(
                api.method.upper(), url, json=body or None, params=query or None, headers=headers
            )
        if resp.status_code >= 400:
            return ActionResult(
                status=ResultStatus.failed,
                action=name,
                message=f"Не удалось выполнить «{description}»: бэкенд ответил {resp.status_code}.",
            )
    except Exception as exc:  # network error, DNS, timeout, ...
        return ActionResult(
            status=ResultStatus.failed,
            action=name,
            message=f"Не удалось выполнить «{description}»: {exc}.",
        )

    return ActionResult(status=ResultStatus.done, action=name, message=success)


class GenericHTTPHandler(ActionHandler):
    """Config-only action: no validate logic, executes ``spec.api`` over HTTP."""

    async def execute(self, profile: Profile, entities: dict) -> ActionResult:
        return await call_product_api(self.spec, profile, entities)
