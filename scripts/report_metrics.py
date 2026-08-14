"""부록 D 지표 산출 — 발표용. event_log를 세션 단위 퍼널로 집계한다.
실행: .venv/bin/python -m scripts.report_metrics"""

from sqlalchemy import distinct, func
from sqlalchemy.orm import Session

from app.db import SessionLocal, init_db
from app.models import EventLog, SessionStock


def _sessions_with(db: Session, event_name: str) -> int:
    return (
        db.query(func.count(distinct(EventLog.session_id)))
        .filter(EventLog.event_name == event_name, EventLog.session_id.isnot(None))
        .scalar()
        or 0
    )


def _count(db: Session, event_name: str) -> int:
    return db.query(func.count(EventLog.id)).filter(EventLog.event_name == event_name).scalar() or 0


def build_report(db: Session) -> dict:
    home = _sessions_with(db, "home_view")

    def rate(part: int, whole: int) -> str:
        return f"{part}/{whole} ({part / whole * 100:.0f}%)" if whole else f"{part}/0"

    sheet_opens = _count(db, "sheet_open")
    origin_clicks = _count(db, "origin_click")
    direct_added = (
        db.query(func.count(distinct(SessionStock.session_id)))
        .filter(SessionStock.is_default.is_(False))
        .scalar()
        or 0
    )
    return {
        "홈 도달 세션": home,
        "종목 추가 전환": rate(_sessions_with(db, "stock_add"), home),
        "직접 추가 종목 보유 세션(기본 제공 제외)": direct_added,
        "로그인 전환": rate(_sessions_with(db, "login"), home),
        "카드 저장 세션": rate(_sessions_with(db, "card_save"), home),
        "구분 전환 발생 세션": rate(_sessions_with(db, "market_switch"), home),
        "유튜브 → 타 탭 이동 세션": rate(_sessions_with(db, "youtube_to_other_tab"), home),
        "요약 시트 열람 수": sheet_opens,
        "시트 대비 원문 이동률": rate(origin_clicks, sheet_opens),
        "재방문 세션": _sessions_with(db, "revisit"),
    }


def main() -> None:
    init_db()
    with SessionLocal() as db:
        for name, value in build_report(db).items():
            print(f"{name:<28} {value}")


if __name__ == "__main__":
    main()
