"""F-4.3 — 한국은행 기준금리 (ECOS StatisticSearch 722Y001).

실측(2026-08-14): 722Y001/D는 여러 항목이 섞여 오므로 **항목코드 0101000(기준금리) 필터 필수**.
행은 TIME 오름차순. 결정 요지 텍스트는 ECOS 미제공 — 수동 시드(scripts/seed_rate_decisions.py)가
결정문 카드를 만들고, 이 수집기는 최신 지표값·변동 방향을 그 카드에 갱신한다 (확정사항 2절 B3).
"""

import logging
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from app.collectors.base import get_with_retry, mark_status, upsert_source_item
from app.config import settings
from app.deps import utcnow
from app.models import MARKET_DOMESTIC, SourceItem

logger = logging.getLogger(__name__)

STAT_CODE = "722Y001"
ITEM_CODE = "0101000"  # 한국은행 기준금리
WINDOW_DAYS = 400  # 직전 변동을 찾을 만큼 넉넉히


def _direction(values: list[tuple[str, float]]) -> str:
    """(날짜, 값) 오름차순에서 마지막 변동 방향 — 인상/인하/동결."""
    if len(values) < 2:
        return "동결"
    last = values[-1][1]
    for _, prev in reversed(values[:-1]):
        if prev != last:
            return "인상" if last > prev else "인하"
    return "동결"


def sync_bok_rate(db: Session) -> dict:
    start = (utcnow() - timedelta(days=WINDOW_DAYS)).strftime("%Y%m%d")
    end = utcnow().strftime("%Y%m%d")
    url = (
        f"https://ecos.bok.or.kr/api/StatisticSearch/{settings.ecos_api_key}"
        f"/json/kr/1/1000/{STAT_CODE}/D/{start}/{end}/{ITEM_CODE}"
    )
    try:
        data = get_with_retry(url).json()
        payload = data.get("StatisticSearch")
        if not payload or not payload.get("row"):
            raise RuntimeError(f"ECOS 응답 이상: {str(data)[:200]}")

        rows = payload["row"]
        values: list[tuple[str, float]] = []
        for row in rows:  # TIME 오름차순 (실측)
            if not values or float(row["DATA_VALUE"]) != values[-1][1]:
                values.append((row["TIME"], float(row["DATA_VALUE"])))
        latest_date, latest = rows[-1]["TIME"], float(rows[-1]["DATA_VALUE"])
        direction = _direction(values)
        rate_str = f"{latest:.2f}%"

        # 최신 bok 카드(시드된 결정문 우선)에 지표를 싣는다. 없으면 지표 전용 카드 생성
        card = (
            db.query(SourceItem)
            .filter(SourceItem.tab == "bok")
            .order_by(SourceItem.published_at.desc().nullslast())
            .first()
        )
        if card is None:
            card = upsert_source_item(
                db,
                tab="bok",
                market=MARKET_DOMESTIC,  # 금리 탭 카드는 tab 기준으로 조회한다(구분 공통)
                source_key="bok-indicator",
                title="한국은행 기준금리",
                published_at=datetime.strptime(latest_date, "%Y%m%d"),
            )
        card.indicator_value = rate_str
        card.doc_type = direction  # 금리 탭 한정 — 변동 방향 (F-5.3 연결 문장 입력)
        db.commit()
        mark_status(db, "bok", "global", True, f"{rate_str} {direction}")
        return {"rate": rate_str, "direction": direction, "date": latest_date}
    except Exception as e:
        db.rollback()
        mark_status(db, "bok", "global", False, str(e))
        logger.warning("ECOS 수집 실패: %s", e)
        return {"error": str(e)[:200]}
