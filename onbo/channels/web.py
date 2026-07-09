"""Web adapter (FastAPI): chat/form endpoint, optional audio, confirm buttons.

Also the mount point for the /admin KB management API. The heavy web deps are
imported lazily so this module can be imported without the ``web`` extra.
"""
from __future__ import annotations

from .base import Channel


class WebChannel(Channel):
    name = "web"

    def build_app(self):
        try:
            from fastapi import Body, FastAPI, UploadFile
        except ImportError as exc:  # pragma: no cover - depends on the web extra
            raise RuntimeError("fastapi не установлен (extra `web`).") from exc

        app = FastAPI(title="onbo")

        @app.post("/chat")
        async def chat(user_id: str = Body(...), text: str = Body(...), locale: str = Body("ru")):
            response = await self.handle_text(user_id, text, locale)
            return {"text": response.text, "results": [r.model_dump() for r in response.results]}

        @app.post("/voice")
        async def voice(user_id: str = Body(...), audio: "UploadFile" = None, locale: str = Body("ru")):
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

        config = uvicorn.Config(self.build_app(), host="0.0.0.0", port=8000)
        await uvicorn.Server(config).serve()
