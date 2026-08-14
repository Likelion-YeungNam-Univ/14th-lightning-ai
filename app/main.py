from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.config import settings
from app.db import init_db
from app.errors import register_exception_handlers
from app.routers import (
    admin,
    auth,
    cards,
    events,
    health,
    markets,
    me_stocks,
    saved,
    sessions,
    stocks,
    terms,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    if settings.enable_scheduler:
        from app.scheduler import start_scheduler

        start_scheduler()
    yield


def create_app() -> FastAPI:
    app = FastAPI(title="assit API", lifespan=lifespan)
    register_exception_handlers(app)
    app.include_router(health.router)
    app.include_router(sessions.router)
    app.include_router(auth.router)
    app.include_router(markets.router)
    app.include_router(stocks.router)
    app.include_router(me_stocks.router)
    app.include_router(cards.router)
    app.include_router(saved.router)
    app.include_router(terms.router)
    app.include_router(events.router)
    app.include_router(admin.router)
    return app


app = create_app()
