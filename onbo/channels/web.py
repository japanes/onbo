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
            async def welcome(user_id: str = Body(..., embed=True)):
                # Explicit trigger; 404 when the digest itself is switched off.
                if not self.settings.welcome.enabled:
                    raise HTTPException(status_code=404, detail="Проактивное приветствие выключено.")
                response = await self.pipeline.welcome(user_id)
                return {"text": response.text, "results": [r.model_dump() for r in response.results]}

        if features.chat:
            @app.post("/chat")
            async def chat(user_id: str = Body(...), text: str = Body(...), locale: str = Body("ru")):
                # Prepend the one-time welcome on the user's first message.
                greeting = await self.pipeline.maybe_welcome(user_id)
                response = await self.handle_text(user_id, text, locale)
                results = (greeting.results if greeting else []) + response.results
                reply = f"{greeting.text}\n\n{response.text}" if greeting else response.text
                return {
                    "text": reply,
                    "results": [r.model_dump() for r in results],
                    "welcomed": greeting is not None,
                }

            @app.post("/voice")
            async def voice(user_id: str = Body(...), audio: UploadFile = None, locale: str = Body("ru")):
                if not self.accepts_voice():
                    return {"text": "Голосовой ввод выключен. Напишите, пожалуйста, текстом."}
                text = await self.transcribe(await audio.read(), locale=locale)
                response = await self.handle_text(user_id, text, locale)
                return {"text": response.text, "transcript": text}

            @app.post("/confirm")
            async def confirm(user_id: str = Body(...), action: str = Body(...), approved: bool = Body(...)):
                result = await self.pipeline.confirm(user_id, action, approved)
                return result.model_dump()

        return app

    async def start(self) -> None:
        import uvicorn

        cfg = self._channel_config()
        port = cfg.port if cfg else 18000
        config = uvicorn.Config(self.build_app(), host="0.0.0.0", port=port)
        await uvicorn.Server(config).serve()
