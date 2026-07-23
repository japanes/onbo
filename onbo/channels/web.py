"""Web adapter (FastAPI): chat/form endpoint, optional audio, confirm buttons.

Also the mount point for the /admin KB management API. The heavy web deps are
imported lazily so this module can be imported without the ``web`` extra.

NOTE: this module deliberately does NOT use ``from __future__ import annotations``.
The endpoint type hints (e.g. ``UploadFile``) must stay as real objects so FastAPI
can build ``/openapi.json`` (Swagger). As strings they become unresolvable forward
references — ``UploadFile`` is imported lazily inside ``build_app`` and isn't in the
module globals, so schema generation would 500.
"""
import os

from .base import Channel


class WebChannel(Channel):
    name = "web"

    def build_app(self):
        try:
            from fastapi import Body, FastAPI, HTTPException, UploadFile
        except ImportError as exc:  # pragma: no cover - depends on the web extra
            raise RuntimeError("fastapi не установлен (extra `web`).") from exc

        app = FastAPI(title="onbo")
        features = self.settings.features
        self._install_cors(app)

        # Serve walkthrough videos (Q&A video_url) from the media directory.
        if features.media:
            from fastapi.staticfiles import StaticFiles

            media_dir = self.settings.media.dir
            os.makedirs(media_dir, exist_ok=True)  # StaticFiles requires it to exist
            app.mount("/media", StaticFiles(directory=media_dir), name="media")

        # Visual KB admin lives under /admin (open unless ONBO_ADMIN_TOKEN is set).
        if features.admin:
            from .admin_api import build_admin_router

            app.include_router(build_admin_router(self.settings))

        if features.llm_manifest:
            @app.get("/llm.json")
            @app.get("/.well-known/llm.json")
            async def llm_manifest():
                # Public manifest for external LLM agents (only public Q&A/actions).
                from ..core.manifest import build_llm_manifest

                return build_llm_manifest(self.settings)

        if features.welcome:
            @app.post("/welcome")
            async def welcome(user_id: str = Body(None), token: str = Body(None)):
                # Explicit trigger; 404 when the digest itself is switched off.
                if not self.settings.welcome.enabled:
                    raise HTTPException(status_code=404, detail="Проактивное приветствие выключено.")
                user_id, profile = self._identify(user_id, token)
                response = await self.pipeline.welcome(user_id, profile)
                return {"text": response.text, "results": [r.model_dump() for r in response.results]}

        if features.chat:
            @app.post("/chat")
            async def chat(
                text: str = Body(...),
                user_id: str = Body(None),
                token: str = Body(None),
                locale: str = Body("ru"),
                # The browser's own local time, ISO-8601 with offset. Only ever a
                # hint for reading dates out of the message (core/clock.py) — it
                # grants nothing, so it may come from the body unsigned.
                ts: str = Body(None),
            ):
                user_id, profile = self._identify(user_id, token)
                # Prepend the one-time welcome on the user's first message. It is
                # awaited before the real answer, so it has to be cheap: it is a
                # fixed three-line text and touches no model and no database
                # unless `welcome.smooth` is on. Turning that flag on puts one
                # LLM round trip in front of the first answer — deliberately, and
                # only for that first message.
                greeting = await self.pipeline.maybe_welcome(user_id, profile)
                response = await self.handle_text(user_id, text, locale, profile, ts)
                results = (greeting.results if greeting else []) + response.results
                reply = f"{greeting.text}\n\n{response.text}" if greeting else response.text
                return {
                    "text": reply,
                    "results": [r.model_dump() for r in results],
                    "welcomed": greeting is not None,
                }

            @app.post("/voice")
            async def voice(
                audio: UploadFile = None,
                user_id: str = Body(None),
                token: str = Body(None),
                locale: str = Body("ru"),
                ts: str = Body(None),
            ):
                if not self.accepts_voice():
                    return {"text": "Голосовой ввод выключен. Напишите, пожалуйста, текстом."}
                user_id, profile = self._identify(user_id, token)
                text = await self.transcribe(await audio.read(), locale=locale)
                response = await self.handle_text(user_id, text, locale, profile, ts)
                return {
                    "text": response.text,
                    "transcript": text,
                    "results": [r.model_dump() for r in response.results],
                }

            @app.post("/confirm")
            async def confirm(
                action: str = Body(...),
                approved: bool = Body(...),
                user_id: str = Body(None),
                token: str = Body(None),
            ):
                user_id, profile = self._identify(user_id, token)
                result = await self.pipeline.confirm(user_id, action, approved, profile)
                return result.model_dump()

        return app

    # -- who is asking --------------------------------------------------------

    def _identify(self, user_id, token):
        """Turn the request body into (user_id, profile-or-None).

        A signed token wins: it carries the department and roles, so no directory
        lookup happens and nothing about access comes from the caller unsigned. A
        bare ``user_id`` is the local/proxy mode — the profile is then looked up
        in onbo's own users table further down the pipeline.
        """
        from fastapi import HTTPException

        from ..auth.tokens import TokenError, profile_from_token

        if token:
            try:
                profile = profile_from_token(token, self.settings)
            except TokenError as exc:
                raise HTTPException(status_code=401, detail=str(exc)) from exc
            return profile.user_id, profile
        if not self.settings.auth.allow_user_id:
            raise HTTPException(
                status_code=401,
                detail="Нужен подписанный токен (auth.allow_user_id выключен).",
            )
        if not user_id:
            raise HTTPException(status_code=422, detail="Нужен user_id или token.")
        return user_id, None

    def _install_cors(self, app) -> None:
        """Allow the browser widget to call this API directly, if configured.

        Only meaningful in token mode: with `*` and no tokens, any page on the
        internet could ask questions as any employee, so that combination is
        refused rather than quietly allowed.
        """
        cfg = self._channel_config()
        origins = list(cfg.cors_origins) if cfg else []
        if not origins:
            return
        if "*" in origins and not self.settings.auth.jwt_secret:
            raise RuntimeError(
                "cors_origins: '*' без auth.jwt_secret — так чат сможет дёргать любой сайт. "
                "Укажите конкретные адреса или включите вход по токену."
            )
        from fastapi.middleware.cors import CORSMiddleware

        app.add_middleware(
            CORSMiddleware,
            allow_origins=origins,
            allow_methods=["POST", "GET"],
            allow_headers=["Content-Type", "Authorization"],
        )

    async def start(self) -> None:
        import uvicorn

        cfg = self._channel_config()
        port = cfg.port if cfg else 18000
        config = uvicorn.Config(self.build_app(), host="0.0.0.0", port=port)
        await uvicorn.Server(config).serve()
