"""F-3.6·F-4 — 배치 스케줄러. uvicorn 워커 1개 전제(CLAUDE.md 불변식 3).

일 06:00 KST: 종목 마스터 → 전체 수집(공시·유튜브·규제·금리·정리)을 한 체인으로.
주 1회(월 05:30): DART corp_code 매핑 갱신 — 신규 상장 반영이면 충분하다.
"""

import logging

from apscheduler.schedulers.background import BackgroundScheduler

logger = logging.getLogger(__name__)

scheduler = BackgroundScheduler(timezone="Asia/Seoul")


def _job_daily() -> None:
    from app.collectors.krx import sync_stock_master
    from app.collectors.runner import collect_all
    from app.db import SessionLocal

    with SessionLocal() as db:
        try:  # 마스터 실패가 수집까지 막지 않게 격리
            logger.info("stock master sync: %s", sync_stock_master(db))
        except Exception as e:
            db.rollback()
            logger.warning("stock master sync 실패: %s", e)
        collect_all(db)


def _job_weekly_corp_codes() -> None:
    from app.collectors.dart import sync_corp_codes
    from app.db import SessionLocal

    with SessionLocal() as db:
        try:
            logger.info("corp_code sync: %s", sync_corp_codes(db))
        except Exception as e:
            db.rollback()
            logger.warning("corp_code sync 실패: %s", e)


def start_scheduler() -> None:
    scheduler.add_job(_job_daily, "cron", hour=6, minute=0, id="daily_collect")
    scheduler.add_job(
        _job_weekly_corp_codes, "cron", day_of_week="mon", hour=5, minute=30, id="corp_codes"
    )
    scheduler.start()
    logger.info("scheduler started (daily 06:00 / mon 05:30 KST)")
