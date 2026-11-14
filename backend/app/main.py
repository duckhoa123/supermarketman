"""FastAPI application."""

import json
import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware

from app.api import (
    account,
    agent_home,
    agents,
    chat,
    customer,
    fleet,
    integrations,
    internal,
    inventory,
    operations,
    receiving,
    restaurant,
    webhooks,
    website,
)
from app.config import get_settings

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger(__name__)


class ErrorsAsJson:
    """Turn an unhandled error into a JSON 500 (logged with its traceback) instead of a dropped response."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        started = False

        async def tracked(message):
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        try:
            await self.app(scope, receive, tracked)
        except Exception:
            log.exception("Unhandled error on %s %s", scope.get("method"), scope.get("path"))
            if started:
                raise
            body = json.dumps({"detail": "Something went wrong on the server. Please try again; if it keeps "
                                         "happening, tell us what you were doing."}).encode()
            await send({"type": "http.response.start", "status": 500,
                        "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())]})
            await send({"type": "http.response.body", "body": body})


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="Food ecosystem API",
        version="0.2.0",
        docs_url=None if settings.is_production else "/docs",
        redoc_url=None,
    )
    # Added before CORS so CORS wraps it: even an unexpected crash reaches the browser as a readable error
    # (without CORS headers the browser only says "Failed to fetch").
    app.add_middleware(ErrorsAsJson)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["Authorization", "Content-Type"],
    )
    # Compress responses: restaurant websites are served from here (a long menu with "+ Add" steppers is a few
    # hundred KB of HTML, a tenth of that compressed). Streamed chat replies (text/event-stream) are left alone,
    # so they still arrive word by word (tests/test_website.py checks both).
    app.add_middleware(GZipMiddleware, minimum_size=1000)

    @app.get("/health", tags=["ops"])
    async def health():
        return {"ok": True}

    for module in (account, agents, agent_home, restaurant, operations, receiving, inventory, website, chat, customer,
                   fleet, integrations, webhooks, internal):
        app.include_router(module.router)
    app.include_router(website.public)  # restaurants' public websites: /sites/{slug} and custom domains
    return app


app = create_app()
