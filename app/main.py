import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.db import init_db
from app.errors import register_exception_handlers
from app.routers import (
    admin,
    auth,
    cards,
    econ_cards,
    events,
    gifticons,
    health,
    markets,
    me_stocks,
    points,
    rooms,
    saved,
    sessions,
    stocks,
    terms,
)

# 운영 서버에서 배치·수집·스케줄러 INFO 로그가 보이도록 (기본 WARNING이면 전부 버려진다).
# httpx는 요청 URL에 API 키가 실리므로 WARNING 유지 — 키가 로그에 남지 않게.
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    if settings.enable_scheduler:
        from app.scheduler import start_scheduler

        start_scheduler()
    yield


def create_app() -> FastAPI:
    app = FastAPI(title="assit API", lifespan=lifespan)
    origins = [o.strip() for o in settings.cors_origins.split(",") if o.strip()]
    if origins:  # 프론트 dev 서버(다른 포트)에서 직접 호출할 때 세션 쿠키가 붙도록
        app.add_middleware(
            CORSMiddleware,
            allow_origins=origins,
            allow_credentials=True,  # 쿠키 동반 필수 — 프론트도 fetch에 credentials: "include"
            allow_methods=["*"],
            allow_headers=["*"],
        )
    register_exception_handlers(app)
    app.include_router(health.router)
    app.include_router(sessions.router)
    app.include_router(auth.router)
    app.include_router(markets.router)
    app.include_router(stocks.router)
    app.include_router(me_stocks.router)
    app.include_router(rooms.router)
    app.include_router(points.router)
    app.include_router(cards.router)
    app.include_router(econ_cards.router)
    app.include_router(saved.router)
    app.include_router(gifticons.router)
    app.include_router(terms.router)
    app.include_router(events.router)
    app.include_router(admin.router)
    return app


app = create_app()
