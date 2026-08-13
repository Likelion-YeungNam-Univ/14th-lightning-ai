from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.config import settings
from app.db import init_db
from app.errors import register_exception_handlers
from app.routers import health


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
    return app


app = create_app()
