"""F-3.6·F-4 — 배치 스케줄러. uvicorn 워커 1개 전제(CLAUDE.md 불변식 3).

일 06:00 KST: 종목 마스터 → 전체 수집(공시·유튜브·규제·금리·정리)을 한 체인으로.
주 1회(월 05:30): DART corp_code 매핑 갱신 — 신규 상장 반영이면 충분하다.
18:00·19:00·20:00 KST: 베팅방 자동 정산(C-6.2.4) — 종가 미확보 재시도(C-6.2.6).
짝수 시 정각(2h): 경제 상식 카드 노출 세트 회전(E-5.2).
"""

import logging

from apscheduler.schedulers.background import BackgroundScheduler

logger = logging.getLogger(__name__)

scheduler = BackgroundScheduler(timezone="Asia/Seoul")


def _job_daily() -> None:
    from app.ai.generate import generate_all
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
        try:  # 수집 직후 AI 가공 체인 (F-5.6) — 실패해도 이미 적재된 원자료는 유효
            generate_all(db)
        except Exception as e:
            db.rollback()
            logger.warning("AI 생성 배치 실패: %s", e)


def _job_econ_rotation() -> None:
    from app.ai.econ_cards import rotate
    from app.db import SessionLocal

    with SessionLocal() as db:
        try:
            logger.info("econ card rotation: %s", rotate(db))
        except Exception as e:
            db.rollback()
            logger.warning("econ card rotation 실패: %s", e)


def _job_weekly_corp_codes() -> None:
    from app.collectors.dart import sync_corp_codes
    from app.db import SessionLocal

    with SessionLocal() as db:
        try:
            logger.info("corp_code sync: %s", sync_corp_codes(db))
        except Exception as e:
            db.rollback()
            logger.warning("corp_code sync 실패: %s", e)


def _job_settle_rooms() -> None:
    from app.db import SessionLocal
    from app.services.settlement import sync_settle_rooms

    with SessionLocal() as db:
        try:
            logger.info("room settlement: %s", sync_settle_rooms(db))
        except Exception as e:
            db.rollback()
            logger.warning("베팅방 정산 실패: %s", e)


def start_scheduler() -> None:
    scheduler.add_job(_job_daily, "cron", hour=6, minute=0, id="daily_collect")
    scheduler.add_job(
        _job_weekly_corp_codes, "cron", day_of_week="mon", hour=5, minute=30, id="corp_codes"
    )
    scheduler.add_job(_job_settle_rooms, "cron", hour="18,19,20", minute=0, id="settle_rooms")
    # E-5.2 — 짝수 시 정각마다 노출 세트 교체
    scheduler.add_job(_job_econ_rotation, "cron", hour="*/2", minute=0, id="econ_rotation")
    scheduler.start()
    logger.info(
        "scheduler started (daily 06:00 / mon 05:30 / settle 18-20:00 / econ rotation 2h KST)"
    )
