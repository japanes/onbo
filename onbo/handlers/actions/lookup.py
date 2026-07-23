"""Turning what a person says into what the product's API actually wants.

A person says «со склада в Милане». The API wants ``warehouse: 3`` — the row id
in the product's own table. That table is different in every installation,
different per workspace, and changes without anyone editing actions.yaml, so it
cannot be written down as an ``enum``. A parameter therefore declares where to
read it (``lookup:``, see registry.LookupSpec) and this module does the reading.

Three outcomes, and none of them is a guess:

- exactly one row matches -> the id is substituted and the action goes on;
- several match -> we ask which one, listing them;
- none match (or nothing was said at all) -> we ask, showing what does exist.

That last part is the quiet win: the question stops being «уточните: склад» and
becomes «уточните: склад — Milano, Milano Nord, Torino» — built from live data,
so it is never out of date.

The list is fetched with the caller's own credential, exactly like the action
itself, so a person can only ever be offered what they are allowed to see.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

from ...config import load_settings
from ...core.schemas import Profile
from .http_action import build_ctx, product_headers, render, render_map

# How long a fetched directory is reused, per address and per credential.
# One question-and-answer («какой склад?» — «в Милане») would otherwise read
# the same list twice within seconds; anything longer starts hiding a row that
# was just created in the product.
CACHE_TTL = 60.0

_cache: dict[tuple, tuple[float, list]] = {}


def clear_cache() -> None:
    """Forget every fetched directory (tests, and after a config reload)."""
    _cache.clear()


@dataclass
class Resolution:
    """What resolving a parameter's directory produced.

    ``question`` and ``error`` are mutually exclusive with getting on with the
    action: the caller returns to the person instead of calling the product.
    """

    entities: dict = field(default_factory=dict)
    question: str | None = None   # ask the person (ambiguous / unknown / not said)
    asked: str | None = None      # which parameter the question is about
    error: str | None = None      # the directory itself could not be read

    @classmethod
    def ask(cls, entities: dict, name: str, question: str) -> "Resolution":
        """A question about ``name``, with the value that caused it thrown away.

        Dropping it matters twice over: a word that matched nothing must never
        reach the product as if it were a real value, and the reply to our
        question is only read as an answer for a parameter that is still empty.
        """
        pending = dict(entities)
        pending.pop(name, None)
        return cls(entities=pending, question=question, asked=name)


def _dig(payload, path: str):
    """Walk a dot path into a JSON response: "data", "result.rows", "" (the body)."""
    for step in filter(None, path.split(".")):
        if not isinstance(payload, dict):
            return None
        payload = payload.get(step)
    return payload


# Directory rows are often named in Latin («Milano», «Torino») while the person
# types them in their own alphabet («Милано»). Both sides are put through the
# same table before they are compared, so «Милано» finds "Milano" — and a row
# named «Милано» is still found by "milano".
_TRANSLIT = {
    "а": "a", "б": "b", "в": "v", "г": "g", "ґ": "g", "д": "d", "е": "e", "ё": "e",
    "є": "e", "ж": "zh", "з": "z", "и": "i", "і": "i", "ї": "i", "й": "i", "к": "k",
    "л": "l", "м": "m", "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t",
    "у": "u", "ф": "f", "х": "h", "ц": "c", "ч": "ch", "ш": "sh", "щ": "sch",
    "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya",
}


def _norm(value) -> str:
    """One comparable shape for both what was said and what the row is called."""
    text = " ".join(str(value or "").split()).strip().lower()
    return "".join(_TRANSLIT.get(ch, ch) for ch in text)


def _label_of(row: dict, spec) -> str:
    """What this row is called, falling back to its id so a list is never blank."""
    for candidate in (spec.label, *spec.match, spec.value):
        text = str(row.get(candidate, "") or "").strip()
        if text:
            return text
    return ""


def _matches(rows: list, spec, said: str) -> list:
    """Rows a person could have meant by ``said``, tightest interpretation first.

    An exact hit is never diluted by the loose ones: "Milano" must not become
    ambiguous just because "Milano Nord" also exists. Only when nothing matches
    exactly do we fall back to «starts with», then to «contains».
    """
    wanted = _norm(said)
    if not wanted:
        return []
    fields = spec.match_fields()
    # The value itself is accepted as-is: a repeated turn, or a page that already
    # passed the id in, should not have to be re-guessed from words.
    exact_value = [r for r in rows if _norm(r.get(spec.value)) == wanted]
    if exact_value:
        return exact_value
    for test in (
        lambda text: text == wanted,
        lambda text: text.startswith(wanted),
        lambda text: wanted in text,
    ):
        found = [r for r in rows if any(test(_norm(r.get(f))) for f in fields if f)]
        if found:
            return found
    return []


def _listing(rows: list, spec, limit: int = 12) -> str:
    """The directory as a person reads it, cut off before it becomes a wall."""
    names = [name for name in (_label_of(r, spec) for r in rows) if name]
    if len(names) > limit:
        return ", ".join(names[:limit]) + f" … (всего {len(names)})"
    return ", ".join(names)


async def _fetch(spec, ctx: dict, profile: Profile) -> list:
    """Read the directory, or raise. Cached per address and per credential."""
    settings = load_settings()
    product = settings.product
    if spec.url:
        url = render(spec.url, ctx)
    elif spec.path and product.base_url:
        url = product.base_url.rstrip("/") + "/" + render(spec.path, ctx).lstrip("/")
    else:
        raise RuntimeError("не задан адрес справочника (lookup.url или product.base_url + lookup.path)")

    query = render_map(spec.query, ctx)
    headers = product_headers(profile, ctx, product)
    # Cached per credential as well as per address: two people may be shown two
    # different lists by the same endpoint, and the cache must never cross that.
    key = (url, tuple(sorted(query.items())), headers.get(product.auth_header, ""))
    hit = _cache.get(key)
    now = time.monotonic()
    if hit and hit[0] > now:
        return hit[1]

    import httpx

    async with httpx.AsyncClient(timeout=product.timeout, verify=product.verify_tls) as client:
        resp = await client.request(
            spec.method.upper(), url, params=query or None, headers=headers
        )
    if resp.status_code >= 400:
        raise RuntimeError(f"бэкенд ответил {resp.status_code}")
    rows = _dig(resp.json(), spec.items)
    if not isinstance(rows, list):
        raise RuntimeError(f"ответ не содержит списка по пути «{spec.items or '(корень)'}»")
    rows = [r for r in rows if isinstance(r, dict)]
    _cache[key] = (now + CACHE_TTL, rows)
    return rows


async def resolve_lookups(spec, entities: dict, profile: Profile) -> Resolution:
    """Replace every directory-backed value with the id the API expects.

    Runs before the "what's missing" check, so a word that does not resolve is
    asked about rather than sent to the product as-is. Parameters without a
    ``lookup:`` are left alone, and an action that has none at all costs nothing.
    """
    resolved = dict(entities)
    for name, param in getattr(spec, "params", {}).items():
        lookup = getattr(param, "lookup", None)
        if lookup is None:
            continue
        said = str(resolved.get(name, "") or "").strip()
        if not said and not param.required:
            continue  # nothing said about an optional detail: leave it out

        ctx = build_ctx(profile, resolved)
        # A directory that is scoped by another parameter («склады этого
        # города») cannot be read before that parameter is known. Skip it: the
        # missing-parameter check right after this asks for the project first,
        # and on the next turn we come back here with it filled in.
        target = " ".join(
            str(part) for part in (lookup.url or lookup.path, *lookup.query.values())
        )
        if "{" in render(target, ctx):
            continue

        try:
            rows = await _fetch(lookup, ctx, profile)
        except Exception as exc:  # noqa: BLE001 - network, auth, bad shape
            return Resolution(
                entities=resolved,
                error=f"Не удалось прочитать справочник «{param.label(name)}»: {exc}.",
            )

        if not rows:
            return Resolution.ask(
                resolved, name, f"Не нашёл ни одного значения для «{param.label(name)}»."
            )
        if not said:
            return Resolution.ask(
                resolved, name, f"Уточните: {param.label(name)} — {_listing(rows, lookup)}."
            )

        found = _matches(rows, lookup, said)
        if len(found) == 1:
            resolved[name] = str(found[0].get(lookup.value, ""))
            # The confirmation must show the word, not the row id: nobody can
            # check «отгрузить со склада 3».
            resolved[f"{name}_label"] = _label_of(found[0], lookup)
        elif not found:
            return Resolution.ask(
                resolved,
                name,
                f"«{said}» — такого значения нет для «{param.label(name)}». "
                f"Есть: {_listing(rows, lookup)}.",
            )
        else:
            return Resolution.ask(
                resolved,
                name,
                f"Уточните, что именно: {_listing(found, lookup)} «{param.label(name)}».",
            )
    return Resolution(entities=resolved)
