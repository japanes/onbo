"""Short-lived dialog state (pending confirmations, slot-filling) in Redis.

Falls back to an in-process dict when the redis client is not installed, so the
skeleton stays runnable in development without a Redis server.
"""
from __future__ import annotations

import json

from ..config import Settings

_TTL_SECONDS = 600


class Session:
    def __init__(self, settings: Settings) -> None:
        self._url = settings.redis_url
        self._redis = None
        self._mem: dict[str, dict] = {}
        self._welcomed: set[str] = set()  # db-less fallback for first-contact marks

    async def _client(self):
        try:
            import redis.asyncio as redis
        except ImportError:
            return None  # use in-memory fallback
        if self._redis is None:
            self._redis = redis.from_url(self._url, decode_responses=True)
        return self._redis

    @staticmethod
    def _key(user_id: str, action: str) -> str:
        return f"pending:{user_id}:{action}"

    async def park(self, user_id: str, action: str, entities: dict) -> None:
        """Store a pending action awaiting the user's Ok/Cancel."""
        key = self._key(user_id, action)
        client = await self._client()
        if client is None:
            self._mem[key] = entities
            return
        await client.set(key, json.dumps(entities), ex=_TTL_SECONDS)

    async def pop(self, user_id: str, action: str) -> dict | None:
        """Fetch and clear a pending action (returns None if nothing parked)."""
        key = self._key(user_id, action)
        client = await self._client()
        if client is None:
            return self._mem.pop(key, None)
        raw = await client.get(key)
        if raw is not None:
            await client.delete(key)
        return json.loads(raw) if raw else None

    # -- half-finished action waiting for the missing details -------------------

    @staticmethod
    def _input_key(user_id: str) -> str:
        # One per user, not per action: a person is answering one question at a
        # time, and a new request replaces the old one rather than queueing.
        return f"awaiting:{user_id}"

    async def park_input(
        self, user_id: str, action: str, entities: dict, wanted: list[str] | None = None
    ) -> None:
        """Remember an action that could not run yet for want of a parameter.

        ``wanted`` is what the person was actually asked about. It is usually
        just "the required parameters that are still empty" and can be derived —
        except when the value we have is present but unusable («инстаграм» where
        the API needs a row id), which no re-derivation can tell from a filled-in
        parameter. So the question says what it was about, and the reply is read
        as an answer to that.
        """
        key = self._input_key(user_id)
        payload = {"action": action, "entities": entities, "wanted": wanted or []}
        client = await self._client()
        if client is None:
            self._mem[key] = payload
            return
        await client.set(key, json.dumps(payload), ex=_TTL_SECONDS)

    async def pop_input(self, user_id: str) -> dict | None:
        """Fetch and clear it. Popped, never peeked: if the next message turns
        out to be about something else, the person must not stay stuck in a
        half-filled form."""
        key = self._input_key(user_id)
        client = await self._client()
        if client is None:
            return self._mem.pop(key, None)
        raw = await client.get(key)
        if raw is not None:
            await client.delete(key)
        return json.loads(raw) if raw else None

    # -- first-contact marker (db-less fallback; DB is canonical when available) --

    @staticmethod
    def _welcome_key(user_id: str) -> str:
        return f"welcomed:{user_id}"

    async def is_welcomed(self, user_id: str) -> bool:
        client = await self._client()
        if client is None:
            return user_id in self._welcomed
        return bool(await client.get(self._welcome_key(user_id)))

    async def mark_welcomed(self, user_id: str) -> None:
        """Mark a user as greeted. No TTL — the welcome fires only once, ever."""
        client = await self._client()
        if client is None:
            self._welcomed.add(user_id)
            return
        await client.set(self._welcome_key(user_id), "1")
