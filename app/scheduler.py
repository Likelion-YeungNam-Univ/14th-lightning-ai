"""F-3.6 — 배치 스케줄러. uvicorn 워커 1개 전제(CLAUDE.md 불변식 3).

P0에서는 종목 마스터 동기화만 등록한다. 수집 잡(P3)은 여기에 추가한다.
"""

import logging

from apscheduler.schedulers.background import BackgroundScheduler

logger = logging.getLogger(__name__)

scheduler = BackgroundScheduler(timezone="Asia/Seoul")


def _job_sync_stock_master() -> None:
    from app.collectors.krx import sync_stock_master
    from app.db import SessionLocal

    with SessionLocal() as db:
        stats = sync_stock_master(db)
        logger.info("stock master sync: %s", stats)


def start_scheduler() -> None:
    # 일 1회 06:00 KST (F-3.6 확정값). 수집 잡은 P3에서 같은 시각 체인에 추가.
    scheduler.add_job(_job_sync_stock_master, "cron", hour=6, minute=0, id="sync_stock_master")
    scheduler.start()
    logger.info("scheduler started (daily 06:00 KST)")
