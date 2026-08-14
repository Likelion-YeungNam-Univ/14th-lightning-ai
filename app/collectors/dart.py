"""DART — 고유번호 매핑(F-3.1.1, 주 1회) + 국내 공시 수집(F-4.1, 일 1회).

DART는 종목코드가 아니라 8자리 corp_code로 조회된다.
이 매핑이 없으면 공시 탭은 한 건도 못 불러온다. 매핑 없는 종목은 검색에서 제외.
"""

import io
import logging
import xml.etree.ElementTree as ET
import zipfile
from datetime import datetime, timedelta

import httpx
from sqlalchemy.orm import Session

from app.collectors.base import (
    FetchError,
    ensure_stock_link,
    get_with_retry,
    mark_status,
    upsert_source_item,
)
from app.config import settings
from app.deps import utcnow
from app.models import MARKET_DOMESTIC, DisclosureFormType, StockMaster

logger = logging.getLogger(__name__)

CORP_CODE_URL = "https://opendart.fss.or.kr/api/corpCode.xml"
LIST_URL = "https://opendart.fss.or.kr/api/list.json"
DISCLOSURE_WINDOW_DAYS = 90  # 최근 3개월 (F-4.1)
DISCLOSURES_PER_STOCK = 20  # 종목당 최신 20건 (F-4.1)


def fetch_corp_code_map() -> dict[str, str]:
    """DART corpCode.xml(zip) → {종목코드: corp_code}. 비상장사는 stock_code가 비어 있어 제외."""
    if not settings.dart_api_key:
        raise RuntimeError("DART_API_KEY가 설정되지 않았습니다 (.env 확인)")

    resp = httpx.get(CORP_CODE_URL, params={"crtfc_key": settings.dart_api_key}, timeout=60)
    resp.raise_for_status()
    try:
        zf = zipfile.ZipFile(io.BytesIO(resp.content))
    except zipfile.BadZipFile as e:
        # 키 오류 시 DART는 zip 대신 XML 에러 본문을 준다
        raise RuntimeError(f"DART 응답이 ZIP이 아닙니다 (키 확인): {resp.text[:200]}") from e

    root = ET.fromstring(zf.read(zf.namelist()[0]))
    mapping: dict[str, str] = {}
    for corp in root.iter("list"):
        stock_code = (corp.findtext("stock_code") or "").strip()
        corp_code = (corp.findtext("corp_code") or "").strip()
        if stock_code and corp_code:
            mapping[stock_code] = corp_code
    return mapping


def sync_corp_codes(db: Session) -> dict:
    """stock_master.corp_code 갱신. 우선주 등 매핑 없는 종목은 None 유지(검색 제외 대상)."""
    mapping = fetch_corp_code_map()
    stats = {"mapped": 0, "unmapped": 0}
    for stock in db.query(StockMaster).all():
        corp = mapping.get(stock.stock_code)
        if corp:
            stock.corp_code = corp
            stats["mapped"] += 1
        else:
            stats["unmapped"] += 1
    db.commit()
    logger.info("corp_code sync: %s", stats)
    return stats


def sync_disclosures(db: Session, stocks: list[StockMaster]) -> dict:
    """F-4.1 — 종목별 최근 공시 적재. 종목 단위 실패 격리(F-4.9).

    실측(2026-08-14): list.json status "000"=정상, "013"=데이터 없음.
    응답 필드: rcept_no, report_nm(원문 제목), rcept_dt(YYYYMMDD).
    원본파일 파싱은 범위 밖 — 제목·유형까지만 적재한다.
    """
    stats = {"stocks": 0, "items": 0, "failed": 0, "no_corp_code": 0}
    bgn_de = (utcnow() - timedelta(days=DISCLOSURE_WINDOW_DAYS)).strftime("%Y%m%d")
    # report_nm → 유형 분류 (부분 일치, 긴 이름 우선) — 해설 주입(F-5.1)·RAG의 키가 된다
    form_codes = sorted(
        (
            fc
            for (fc,) in db.query(DisclosureFormType.form_code).filter(
                DisclosureFormType.market == MARKET_DOMESTIC
            )
        ),
        key=len,
        reverse=True,
    )

    for stock in stocks:
        if not stock.corp_code:
            stats["no_corp_code"] += 1
            continue
        try:
            resp = get_with_retry(
                LIST_URL,
                params={
                    "crtfc_key": settings.dart_api_key,
                    "corp_code": stock.corp_code,
                    "bgn_de": bgn_de,
                    "end_de": utcnow().strftime("%Y%m%d"),
                    "page_count": DISCLOSURES_PER_STOCK,
                },
            )
            data = resp.json()
            if data.get("status") == "013":  # 조회된 데이터 없음 — 실패가 아니다
                items = []
            elif data.get("status") != "000":
                raise FetchError(f"DART {data.get('status')}: {data.get('message')}")
            else:
                items = data.get("list", [])

            for it in items:
                title = it["report_nm"].strip()
                item = upsert_source_item(
                    db,
                    tab="disclosure",
                    market=stock.market,
                    source_key=it["rcept_no"],
                    title=title,  # 원문 그대로 (F-5.1.2)
                    doc_type=next((fc for fc in form_codes if fc in title), None),
                    published_at=datetime.strptime(it["rcept_dt"], "%Y%m%d"),
                    origin_url=f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={it['rcept_no']}",
                )
                ensure_stock_link(db, item.id, stock.stock_code)
            db.commit()
            mark_status(db, "disclosure", stock.stock_code, True, f"{len(items)}건")
            stats["stocks"] += 1
            stats["items"] += len(items)
        except Exception as e:  # 한 종목의 실패가 다른 수집을 막지 않는다
            db.rollback()
            mark_status(db, "disclosure", stock.stock_code, False, str(e))
            stats["failed"] += 1
            logger.warning("공시 수집 실패 %s(%s): %s", stock.name, stock.stock_code, e)
    return stats
