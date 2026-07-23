"""Signed identity tokens — the directory-free way to say who is asking.

Copying a user directory into onbo works for a few hundred people and turns into
its own synchronisation problem for a million. The alternative: your backend
already knows who the visitor is, so it signs a short-lived token carrying the
user id, department and roles, and onbo trusts the claims inside.

The token is not a secret — it is *unforgeable*. Only holders of the shared
secret can produce a signature, so a person cannot promote themselves by editing
the payload, and an expired token stops working on its own.

Format is a plain JWT (HS256), verified here with the standard library, so any
JWT library on your side produces a compatible token. Only HS256 is accepted:
"alg": "none" is the classic way these get bypassed.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time

from ..config import Settings
from ..core.schemas import Profile


class TokenError(RuntimeError):
    """A token that cannot be trusted. The caller answers 401, never a profile."""


def _b64url_decode(part: str) -> bytes:
    return base64.urlsafe_b64decode(part + "=" * (-len(part) % 4))


def _b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _sign(message: str, secret: str) -> bytes:
    return hmac.new(secret.encode(), message.encode(), hashlib.sha256).digest()


def decode_token(token: str, secret: str, leeway: int = 30) -> dict:
    """Verify signature and expiry, return the claims. Raises ``TokenError``."""
    parts = token.strip().split(".")
    if len(parts) != 3:
        raise TokenError("не похоже на JWT (нужны три части через точку)")
    header_b64, payload_b64, signature_b64 = parts
    try:
        header = json.loads(_b64url_decode(header_b64))
        claims = json.loads(_b64url_decode(payload_b64))
        signature = _b64url_decode(signature_b64)
    except Exception as exc:
        raise TokenError(f"токен не разбирается: {exc}") from exc

    if header.get("alg") != "HS256":
        raise TokenError(f"алгоритм {header.get('alg')!r} не поддерживается (нужен HS256)")
    if not hmac.compare_digest(_sign(f"{header_b64}.{payload_b64}", secret), signature):
        raise TokenError("подпись не сходится")

    # An expiry is mandatory: a leaked token that never expires is a permanent key.
    exp = claims.get("exp")
    if exp is None:
        raise TokenError("в токене нет exp — токены без срока жизни не принимаются")
    try:
        expires_at = float(exp)
    except (TypeError, ValueError) as exc:
        raise TokenError("exp должен быть числом (unix-время)") from exc
    if time.time() > expires_at + leeway:
        raise TokenError("токен истёк")
    return claims


def profile_from_token(token: str, settings: Settings) -> Profile:
    """Build the access profile from a verified token.

    Claims: ``sub`` (user id, required), ``department`` (or ``dept``) and
    ``roles`` — a list of role names or ids, whatever your system uses; onbo only
    compares them with the tags on knowledge-base entries and actions.

    Optional ``product_token``: the caller's own credential for the product's
    API. Put it in and actions run as that person, with the product's usual
    permission checks intact; leave it out and actions fall back to the single
    ``product.api_key`` from settings.
    """
    secret = settings.auth.jwt_secret
    if not secret:
        raise TokenError("вход по токену выключен (не задан auth.jwt_secret)")
    claims = decode_token(token, secret, settings.auth.jwt_leeway)

    user_id = str(claims.get("sub") or "").strip()
    if not user_id:
        raise TokenError("в токене нет sub (идентификатора пользователя)")

    roles = claims.get("roles") or []
    if isinstance(roles, (str, int)):
        roles = [roles]
    department = claims.get("department") or claims.get("dept")
    product_token = claims.get("product_token")
    return Profile(
        user_id=user_id,
        department=str(department) if department else None,
        roles=[str(role) for role in roles],
        product_token=str(product_token) if product_token else None,
    )


def sign_token(
    user_id: str,
    secret: str,
    department: str | None = None,
    roles: list[str] | None = None,
    ttl: int = 300,
    product_token: str | None = None,
) -> str:
    """Issue a token — for tests and for `onbo token`, to try the flow by hand.

    In production your own backend signs these, at login or per request; it is a
    few lines with any JWT library and needs nothing from onbo.
    """
    header = _b64url_encode(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
    payload = {"sub": user_id, "exp": int(time.time()) + ttl}
    if department:
        payload["department"] = department
    if roles:
        payload["roles"] = list(roles)
    if product_token:
        payload["product_token"] = product_token
    body = _b64url_encode(json.dumps(payload).encode())
    return f"{header}.{body}.{_b64url_encode(_sign(f'{header}.{body}', secret))}"
