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
from app.services.industry import load_dart_detail_map

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


def _enrich_details(db: Session, stock: StockMaster, typed_items: dict, bgn_de: str) -> None:
    """이슈 #18 — 정형 API(자기주식·증자·감자·소송)를 접수번호로 조인해 구조화 필드 저장.

    실패해도 목록 수집분은 유효하므로 유형 단위로 삼키고 로그만 남긴다.
    content(라벨: 값 텍스트)는 요약 입력, detail_json.slots는 카드 배지용.
    """
    detail_map = load_dart_detail_map()
    for doc_type, items in typed_items.items():
        spec = detail_map[doc_type]
        try:
            resp = get_with_retry(
                f"https://opendart.fss.or.kr/api/{spec['endpoint']}.json",
                params={
                    "crtfc_key": settings.dart_api_key,
                    "corp_code": stock.corp_code,
                    "bgn_de": bgn_de,
                    "end_de": utcnow().strftime("%Y%m%d"),
                },
            )
            data = resp.json()
            if data.get("status") != "000":
                continue  # 013 포함 — 상세 없음이면 제목 기반 요약으로 진행
            by_rcept = {row.get("rcept_no"): row for row in data.get("list", [])}
            for item in items:
                row = by_rcept.get(item.source_key)
                if row is None:
                    continue
                lines, slots = [], []
                for field in spec["fields"]:
                    value = str(row.get(field["key"]) or "").strip()
                    if not value or value == "-":
                        continue
                    rendered = f"{value}{field.get('unit', '')}"
                    lines.append(f"{field['label']}: {rendered}")
                    if field.get("slot"):
                        slots.append({"label": field["label"], "value": rendered})
                if lines:
                    item.content = "\n".join(lines)
                    item.detail_json = {"slots": slots}
        except Exception as e:  # 유형 단위 격리 — 실패해도 목록은 살아 있다
            logger.warning("정형 API 실패 %s/%s: %s", stock.stock_code, doc_type, e)


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

            typed_items: dict[str, list] = {}  # 정형 API 대상 유형 → 아이템 (이슈 #18)
            for it in items:
                title = it["report_nm"].strip()
                doc_type = next((fc for fc in form_codes if fc in title), None)
                item = upsert_source_item(
                    db,
                    tab="disclosure",
                    market=stock.market,
                    source_key=it["rcept_no"],
                    title=title,  # 원문 그대로 (F-5.1.2)
                    doc_type=doc_type,
                    published_at=datetime.strptime(it["rcept_dt"], "%Y%m%d"),
                    origin_url=f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={it['rcept_no']}",
                )
                ensure_stock_link(db, item.id, stock.stock_code)
                if doc_type in load_dart_detail_map():
                    typed_items.setdefault(doc_type, []).append(item)
            _enrich_details(db, stock, typed_items, bgn_de)
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
