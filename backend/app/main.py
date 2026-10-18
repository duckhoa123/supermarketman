"""FastAPI application."""

import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import (
    account,
    agents,
    chat,
    customer,
    fleet,
    integrations,
    internal,
    operations,
    restaurant,
    webhooks,
)
from app.config import get_settings

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="Food ecosystem API",
        version="0.2.0",
        docs_url=None if settings.is_production else "/docs",
        redoc_url=None,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["Authorization", "Content-Type"],
    )

    @app.get("/health", tags=["ops"])
    async def health():
        return {"ok": True}

    for module in (account, agents, restaurant, operations, chat, customer, fleet, integrations, webhooks, internal):
        app.include_router(module.router)
    return app


app = create_app()
