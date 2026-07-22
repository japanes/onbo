"""A tiny stand-in for "the target product's backend".

It implements exactly the endpoints that config/actions.yaml points at, so you
can watch onbo actually execute profile actions without wiring up a real app:

    onbo demo-backend                                   # runs on :18100
    PRODUCT_API_BASE=http://localhost:18100 onbo serve web

State is kept in memory (a dict) — this is a demo, not a database.
"""
from __future__ import annotations

# user_id -> {"language": ..., "email": ...}
_STATE: dict[str, dict] = {}


def build_app():
    from fastapi import Body, FastAPI

    app = FastAPI(title="onbo demo product backend")

    @app.get("/")
    async def root():
        return {"service": "onbo demo backend", "users": _STATE}

    @app.get("/api/users/{user_id}")
    async def get_user(user_id: str):
        return {"user_id": user_id, **_STATE.get(user_id, {})}

    @app.post("/api/users/{user_id}/language")
    async def set_language(user_id: str, language: str = Body(..., embed=True)):
        _STATE.setdefault(user_id, {})["language"] = language
        return {"ok": True, "user_id": user_id, "language": language}

    @app.post("/api/users/{user_id}/email")
    async def set_email(user_id: str, email: str = Body(..., embed=True)):
        _STATE.setdefault(user_id, {})["email"] = email
        return {"ok": True, "user_id": user_id, "email": email}

    # -- pipeline demo: the new_order chain (invoice self, invoice client, send) --
    @app.post("/api/orders/{order_id}/invoice")
    async def create_invoice(order_id: str, party: str = Body("internal", embed=True)):
        order = _STATE.setdefault(f"order:{order_id}", {"invoices": []})
        order["invoices"].append(party)
        return {"ok": True, "order_id": order_id, "invoice_for": party}

    @app.post("/api/orders/{order_id}/send")
    async def send_invoice(order_id: str):
        _STATE.setdefault(f"order:{order_id}", {"invoices": []})["sent"] = True
        return {"ok": True, "order_id": order_id, "sent": True}

    return app


def run(port: int = 18100) -> None:
    import uvicorn

    uvicorn.run(build_app(), host="0.0.0.0", port=port)


if __name__ == "__main__":
    run()
