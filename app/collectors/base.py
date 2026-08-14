"""수집기 공통 — 재시도(F-4.9)·수집 상태 기록·source_item upsert."""

import logging
import time

import httpx
from sqlalchemy.orm import Session

from app.deps import utcnow
from app.models import CollectStatus, SourceItem, SourceItemIndustry, SourceItemStock

logger = logging.getLogger(__name__)


class FetchError(Exception):
    """외부 API 호출 실패 — 수집 단위 격리 후 collect_status에 기록된다."""


def get_with_retry(
    url: str,
    *,
    params: dict | None = None,
    headers: dict | None = None,
    timeout: float = 30.0,
    retries: int = 3,
    backoff: float = 0.5,
) -> httpx.Response:
    """지수 백오프 3회 (F-4.9 확정). 4xx는 재시도하지 않는다 — 키·요청 오류는 반복해도 같다."""
    last: Exception | None = None
    for attempt in range(retries):
        try:
            resp = httpx.get(url, params=params, headers=headers, timeout=timeout)
        except httpx.HTTPError as e:
            last = e
        else:
            if resp.status_code < 400:
                return resp
            if resp.status_code < 500:
                raise FetchError(f"HTTP {resp.status_code}: {resp.text[:200]}")
            last = FetchError(f"HTTP {resp.status_code}")
        if attempt < retries - 1:
            time.sleep(backoff * (2**attempt))
    raise FetchError(str(last))


def mark_status(db: Session, tab: str, scope_key: str, ok: bool, detail: str | None = None) -> None:
    """수집 단위별 상태 upsert — F-6.4의 no_data/fetch_failed 구분 근거."""
    row = db.get(CollectStatus, (tab, scope_key))
    if row is None:
        row = CollectStatus(tab=tab, scope_key=scope_key)
        db.add(row)
    row.status = "ok" if ok else "failed"
    row.detail = (detail or "")[:500] or None
    row.updated_at = utcnow()
    db.commit()


def is_fresh(db: Session, tab: str, scope_key: str, ttl_hours: int) -> bool:
    """캐시 TTL 검사 — 최근 성공 수집이 있으면 재수집하지 않는다 (유튜브 24h, F-4.5)."""
    row = db.get(CollectStatus, (tab, scope_key))
    if row is None or row.status != "ok":
        return False
    return (utcnow() - row.updated_at).total_seconds() < ttl_hours * 3600


def upsert_source_item(
    db: Session, *, tab: str, market: str, source_key: str, **fields
) -> SourceItem:
    item = (
        db.query(SourceItem).filter_by(tab=tab, market=market, source_key=source_key).one_or_none()
    )
    if item is None:
        item = SourceItem(tab=tab, market=market, source_key=source_key, **fields)
        db.add(item)
        db.flush()
    else:
        for key, value in fields.items():
            setattr(item, key, value)
    return item


def ensure_stock_link(db: Session, source_item_id: int, stock_code: str) -> None:
    if db.get(SourceItemStock, (source_item_id, stock_code)) is None:
        db.add(SourceItemStock(source_item_id=source_item_id, stock_code=stock_code))


def ensure_industry_link(db: Session, source_item_id: int, market: str, industry_key: str) -> None:
    if db.get(SourceItemIndustry, (source_item_id, market, industry_key)) is None:
        db.add(
            SourceItemIndustry(
                source_item_id=source_item_id, market=market, industry_key=industry_key
            )
        )
