"""F-2.1.1 — DART 고유번호(corp_code) 매핑 동기화 (주 1회).

DART는 종목코드가 아니라 8자리 corp_code로 조회된다.
이 매핑이 없으면 공시 탭은 한 건도 못 불러온다. 매핑 없는 종목은 검색에서 제외(F-2.1.1).
"""

import io
import logging
import xml.etree.ElementTree as ET
import zipfile

import httpx
from sqlalchemy.orm import Session

from app.config import settings
from app.models import StockMaster

logger = logging.getLogger(__name__)

CORP_CODE_URL = "https://opendart.fss.or.kr/api/corpCode.xml"


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
