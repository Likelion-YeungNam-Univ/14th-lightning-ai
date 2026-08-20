"""F-6 — 카드 목록 조회. DB만 읽는다(불변식 1) — 수집·생성은 배치가 끝냈다.

탭별 카드 구성:
- youtube/disclosure: 종목 링크 기준. 공시는 **노출 유형 필터**(이슈 #18, display=true만)
- regulation: 종목의 업종(국내 12분류 / 해외 SIC) 기준
- bok/fed: 탭 전체(결정문·지표 카드) + 업종별 연결 문장(F-6.2)
빈 목록 사유는 collect_status로 no_data / fetch_failed를 구분한다 (F-6.4).
"""

from sqlalchemy.orm import Session

from app.errors import AppError
from app.models import (
    MARKET_DOMESTIC,
    CollectStatus,
    GeneratedContent,
    RateLinkSentence,
    SavedCard,
    SessionStock,
    SourceItem,
    SourceItemIndustry,
    SourceItemStock,
    StockMaster,
    UserSession,
)
from app.services.industry import displayed_form_codes, form_type_name
from app.services.markets import RATE_TABS, set_last_stock, validate_combination

CARDS_PER_TAB = 20  # 탭당 20건 내외 (F-6 제안 채택)

SOURCE_NAMES = {
    ("youtube", "domestic"): "YouTube",
    ("youtube", "overseas"): "YouTube",
    ("disclosure", "domestic"): "DART",
    ("disclosure", "overseas"): "SEC EDGAR",
    ("regulation", "domestic"): "정책브리핑",
    ("regulation", "overseas"): "Federal Register",
    ("bok", "domestic"): "한국은행",
    ("fed", "domestic"): "Fed",
    ("fed", "overseas"): "Fed",
}


def list_cards(db: Session, session: UserSession | None, tab: str, stock_code: str) -> dict:
    """F-6.1 — 탭 × 종목 조합의 카드 반환. 구분은 파라미터로 받지 않는다(종목이 결정)."""
    stock = db.get(StockMaster, stock_code)
    if stock is None:
        raise AppError("unknown_stock", "존재하지 않는 종목코드입니다", 404)
    validate_combination(stock.market, tab)  # F-2.4

    # F-2.2 — 등록된 종목을 조회했을 때만 구분별 복귀 지점 갱신
    if session is not None:
        registered = db.get(SessionStock, (session.id, stock_code))
        if registered is not None:
            set_last_stock(session, stock.market, stock_code)
            db.commit()

    items = _query_items(db, tab, stock)
    cards = [_build_card(db, tab, stock, item) for item in items]
    _mark_saved(db, session, cards)
    return {
        "tab": tab,
        "market": stock.market,
        "stock_code": stock_code,
        "link_sentence": _link_sentence(db, tab, stock) if tab in RATE_TABS else None,
        "disclaimer": tab == "youtube",
        "reason": None if cards else _empty_reason(db, tab, stock),
        "items": cards,
    }


def _query_items(db: Session, tab: str, stock: StockMaster) -> list[SourceItem]:
    if tab in RATE_TABS:  # 금리 탭은 종목 무관 — 탭 전체 (F-6.2)
        return (
            db.query(SourceItem)
            .filter(SourceItem.tab == tab)
            .order_by(SourceItem.published_at.desc().nullslast())
            .limit(CARDS_PER_TAB)
            .all()
        )
    if tab == "regulation":
        industry_key = stock.industry_code if stock.market == MARKET_DOMESTIC else stock.sic_code
        if not industry_key:
            return []
        return (
            db.query(SourceItem)
            .join(SourceItemIndustry, SourceItemIndustry.source_item_id == SourceItem.id)
            .filter(
                SourceItemIndustry.market == stock.market,
                SourceItemIndustry.industry_key == industry_key,
                SourceItem.tab == "regulation",
            )
            .order_by(SourceItem.published_at.desc().nullslast())
            .limit(CARDS_PER_TAB)
            .all()
        )

    query = (
        db.query(SourceItem)
        .join(SourceItemStock, SourceItemStock.source_item_id == SourceItem.id)
        .filter(SourceItemStock.stock_code == stock.stock_code, SourceItem.tab == tab)
    )
    if tab == "disclosure":  # 노출 유형 필터 (이슈 #18) — 수집은 전체, 노출만 거른다
        query = query.filter(SourceItem.doc_type.in_(displayed_form_codes(_market_key(stock))))
        order = SourceItem.published_at.desc().nullslast()
    else:  # youtube — 조회수순 (수집 정렬과 동일)
        order = SourceItem.view_count.desc().nullslast()
    return query.order_by(order).limit(CARDS_PER_TAB).all()


def _generated_for(db: Session, tab: str, stock: StockMaster, item: SourceItem):
    if tab == "youtube":  # 유튜브는 생성물 없음 (F-5.1)
        return None
    if tab in RATE_TABS:
        scope, key = "global", "global"
    elif tab == "regulation":
        scope = "industry"
        key = stock.industry_code if stock.market == MARKET_DOMESTIC else stock.sic_code
    else:
        scope, key = "stock", stock.stock_code
    return (
        db.query(GeneratedContent)
        .filter_by(source_item_id=item.id, scope=scope, scope_key=key)
        .one_or_none()
    )


def _industry_key(stock: StockMaster) -> str | None:
    return stock.industry_code if stock.market == MARKET_DOMESTIC else stock.sic_code


def _market_key(stock: StockMaster) -> str:
    """data/*.json의 구분 키 — MARKET_DOMESTIC/OVERSEAS 상수값과 동일한 문자열."""
    return "domestic" if stock.market == MARKET_DOMESTIC else "overseas"


def _link_sentence_for_item(
    db: Session, tab: str, market: str, industry_key: str | None, item: SourceItem
) -> str | None:
    """카드 1건의 지표 스냅샷 기준 문장 조회. 캐시(F-5.3.2)는 업종+지표 버전 단위 그대로다."""
    if not industry_key:
        return None
    version = f"{item.indicator_value}|{item.doc_type or '동결'}"
    row = db.get(RateLinkSentence, (market, tab, industry_key, version))
    return row.sentence if row else None


def _build_card(db: Session, tab: str, stock: StockMaster, item: SourceItem) -> dict:
    generated = _generated_for(db, tab, stock, item)
    with_label = tab in ("disclosure", "regulation")  # F-5.2 — 라벨 부착 탭
    return {
        "card_id": item.id,
        "label": generated.label if generated and with_label else None,
        "label_reason": generated.label_reason if generated and with_label else None,
        "title": item.title,
        "summary_short": generated.summary_short if generated else None,
        "summary_full": generated.summary_full if generated else None,
        "hard_terms": (generated.hard_terms or None) if generated else None,  # #77 탭 풀이 대상
        "source_name": SOURCE_NAMES.get((tab, stock.market), tab),
        "published_at": item.published_at,
        "origin_url": item.origin_url,
        "is_saved": False,
        "thumbnail_url": item.thumbnail_url,
        "channel_name": item.channel_name,
        "view_count": item.view_count,
        "indicator_value": item.indicator_value if tab in RATE_TABS else None,
        "details": (item.detail_json or {}).get("slots") or None,  # 정형 공시 배지 (이슈 #18)
        "link_sentence": (
            _link_sentence_for_item(db, tab, stock.market, _industry_key(stock), item)
            if tab in RATE_TABS
            else None
        ),
        # 공시만 — 서식 코드 + 한글 서식명 칩 (이슈 #58). 제목은 원문 그대로(F-5.1.2)
        "doc_type": item.doc_type if tab == "disclosure" else None,
        "doc_type_name": (
            form_type_name(_market_key(stock), item.doc_type) if tab == "disclosure" else None
        ),
    }


def _mark_saved(db: Session, session: UserSession | None, cards: list[dict]) -> None:
    if session is None or not cards:
        return
    saved_ids = {
        sid
        for (sid,) in db.query(SavedCard.source_item_id).filter(
            SavedCard.session_id == session.id,
            SavedCard.source_item_id.in_([c["card_id"] for c in cards]),
        )
    }
    for card in cards:
        card["is_saved"] = card["card_id"] in saved_ids


def _link_sentence(db: Session, tab: str, stock: StockMaster) -> str | None:
    """F-6.2 — 목록 최상단 1회(하위호환). 최신 지표 카드 기준, 카드별 값은 items[].link_sentence."""
    industry_key = _industry_key(stock)
    if not industry_key:
        return None
    indicator = (
        db.query(SourceItem)
        .filter(SourceItem.tab == tab, SourceItem.indicator_value.isnot(None))
        .order_by(SourceItem.published_at.desc().nullslast())
        .first()
    )
    if indicator is None:
        return None
    return _link_sentence_for_item(db, tab, stock.market, industry_key, indicator)


def _empty_reason(db: Session, tab: str, stock: StockMaster) -> str:
    """F-6.4 — 빈 목록 사유. 최근 수집이 실패했으면 fetch_failed, 아니면 no_data."""
    scope_key = {
        "youtube": stock.stock_code,
        "disclosure": stock.stock_code,
        "regulation": "domestic" if stock.market == MARKET_DOMESTIC else "overseas",
    }.get(tab, "global")
    status = db.get(CollectStatus, (tab, scope_key))
    if status is not None and status.status == "failed":
        return "fetch_failed"
    return "no_data"
