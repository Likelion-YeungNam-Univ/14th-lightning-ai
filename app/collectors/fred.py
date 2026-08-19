"""F-4.4 — 미국 기준금리 (FRED DFEDTARU: 연방기금 목표금리 상단).

실측(2026-08-14): observations는 date 오름차순, 휴장일 등 값 없는 날은 value "." — 스킵.
최신 3.75%. ECOS와 동일 패턴: 결정문 카드는 수동 시드, 이 수집기는 지표값·방향만 갱신.
"""

import logging
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from app.collectors.base import get_with_retry, mark_status, upsert_source_item
from app.config import settings
from app.deps import utcnow
from app.models import MARKET_DOMESTIC, SourceItem

logger = logging.getLogger(__name__)

SERIES_ID = "DFEDTARU"  # Federal Funds Target Range - Upper Limit
OBS_URL = "https://api.stlouisfed.org/fred/series/observations"
WINDOW_DAYS = 400


def _direction(values: list[float]) -> str:
    if len(values) < 2:
        return "동결"
    last = values[-1]
    for prev in reversed(values[:-1]):
        if prev != last:
            return "인상" if last > prev else "인하"
    return "동결"


def sync_fed_rate(db: Session) -> dict:
    try:
        resp = get_with_retry(
            OBS_URL,
            params={
                "series_id": SERIES_ID,
                "api_key": settings.fred_api_key,
                "file_type": "json",
                "observation_start": (utcnow() - timedelta(days=WINDOW_DAYS)).strftime("%Y-%m-%d"),
            },
        )
        observations = resp.json().get("observations", [])
        values: list[float] = []
        latest_date: str | None = None
        for obs in observations:  # date 오름차순 (실측)
            if obs["value"] == ".":  # 휴장일 — 값 없음
                continue
            v = float(obs["value"])
            if not values or v != values[-1]:
                values.append(v)
            latest_date = obs["date"]
        if not values or latest_date is None:
            raise RuntimeError("FRED 응답에 유효한 관측치가 없습니다")

        direction = _direction(values)
        rate_str = f"{values[-1]:.2f}%"

        card = (
            db.query(SourceItem)
            .filter(SourceItem.tab == "fed")
            .order_by(SourceItem.published_at.desc().nullslast())
            .first()
        )
        if card is None:
            card = upsert_source_item(
                db,
                tab="fed",
                market=MARKET_DOMESTIC,  # 금리 탭 카드는 tab 기준 조회 — 구분 공통
                source_key="fed-indicator",
                title="미국 연방기금 목표금리(상단)",
                published_at=datetime.strptime(latest_date, "%Y-%m-%d"),
            )
        card.indicator_value = rate_str
        card.doc_type = direction
        db.commit()
        mark_status(db, "fed", "global", True, f"{rate_str} {direction}")
        return {"rate": rate_str, "direction": direction, "date": latest_date}
    except Exception as e:
        db.rollback()
        mark_status(db, "fed", "global", False, str(e))
        logger.warning("FRED 수집 실패: %s", e)
        return {"error": str(e)[:200]}
