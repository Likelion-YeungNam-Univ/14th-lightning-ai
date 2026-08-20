"""DART — 고유번호 매핑(F-3.1.1, 주 1회) + 국내 공시 수집(F-4.1, 일 1회).

DART는 종목코드가 아니라 8자리 corp_code로 조회된다.
이 매핑이 없으면 공시 탭은 한 건도 못 불러온다. 매핑 없는 종목은 검색에서 제외.
"""

import io
import logging
import re
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
from app.models import StockMaster
from app.services.industry import dart_classification_codes, load_dart_detail_map

logger = logging.getLogger(__name__)

CORP_CODE_URL = "https://opendart.fss.or.kr/api/corpCode.xml"
LIST_URL = "https://opendart.fss.or.kr/api/list.json"
DISCLOSURE_WINDOW_DAYS = 90  # 최근 3개월 (F-4.1)
# DART list.json 자체가 최신순이라, 여기서 적게 가져오면 노출 필터(display=true) 이전에
# 임원·주요주주 소유상황보고서 같은 고빈도 노이즈가 진짜 공시 자리를 다 차지해버린다
# (QA 재현 — 삼성전자 90일치 31건 중 30건이 노이즈, 화면엔 1건만 노출됨).
# 100은 DART list API의 페이지당 상한 — 여기서 넉넉히 받아와 노출 필터가 고르게 한다
# (명세확정사항 §14, 2026-08-18 갱신 — F-4.1의 기존 "20건"에서 조정).
DISCLOSURES_PER_STOCK = 100


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


# 유상증자 자금조달 목적 6분류 — 합산이 곧 총 조달 금액 (DART 서식이 총액을 용도로 배분)
FUNDING_KEYS = ("fdpp_fclt", "fdpp_bsninh", "fdpp_op", "fdpp_dtrp", "fdpp_ocsa", "fdpp_etc")


def _funding_total(row: dict) -> int | None:
    """이슈 #26 — 조달 금액 합계. 숫자 아닌 값이 섞이면 None(틀린 총액을 만들지 않는다)."""
    total, seen = 0, False
    for key in FUNDING_KEYS:
        raw = str(row.get(key) or "").replace(",", "").strip()
        if not raw or raw == "-":
            continue
        if not raw.isdigit():
            return None
        total += int(raw)
        seen = True
    return total if seen and total > 0 else None


# 이슈 #98 — 정기보고서 3종 재무 슬롯. 제목 "(YYYY.MM)" → (사업연도, 보고서 코드)
PERIODIC_TYPES = ("사업보고서", "반기보고서", "분기보고서")
_REPRT_BY_MONTH = {"03": "11013", "06": "11012", "09": "11014", "12": "11011"}
_FIN_URL = "https://opendart.fss.or.kr/api/fnlttSinglAcnt.json"
# (응답 account_nm, 카드 라벨) — 주요계정 API의 손익 3종. 은행 등 매출액 없는 업종은 있는 것만
_FIN_ACCOUNTS = (("매출액", "매출액"), ("영업이익", "영업이익"), ("당기순이익(손실)", "당기순이익"))
_PERIOD_RE = re.compile(r"\((\d{4})\.(\d{2})\)")


def _format_krw(raw: str) -> str | None:
    """ "171,499,470,000,000" → "171.5조 원". 숫자 아니면 None(틀린 값을 만들지 않는다)."""
    cleaned = raw.replace(",", "").strip()
    sign = "-" if cleaned.startswith("-") else ""
    digits = cleaned.lstrip("-")
    if not digits.isdigit():
        return None
    value = int(digits)
    if value >= 10**12:
        return f"{sign}{value / 10**12:.1f}조 원"
    if value >= 10**8:
        return f"{sign}{value // 10**8:,}억 원"
    return f"{sign}{value:,}원"


def _report_period(title: str) -> tuple[str, str] | None:
    """제목의 "(2026.06)" → ("2026", "11012"). 없으면 None — 보강 없이 진행."""
    m = _PERIOD_RE.search(title)
    if m is None:
        return None
    year, month = m.group(1), m.group(2)
    code = _REPRT_BY_MONTH.get(month)
    return (year, code) if code else None


def _enrich_periodic_financials(db: Session, stock: StockMaster, items: list) -> None:
    """이슈 #98 — 정기보고서에 매출·영업이익·순이익 채움 (#53 해외 companyfacts와 같은 패턴).

    fnlttSinglAcnt(주요계정)를 (사업연도, 보고서코드)당 1회 호출해 rcept_no로 조인한다.
    연결(CFS) 우선, 없으면 개별(OFS). 실측: 반기보고서 IS의 thstrm_amount = 반기 누적.
    실패는 그룹 단위로 삼킨다 — 제목 기반 요약으로 진행(기존 동작과 동일).
    """
    groups: dict[tuple[str, str], list] = {}
    for item in items:
        period = _report_period(item.title)
        if period:
            groups.setdefault(period, []).append(item)
    for (year, reprt_code), group in groups.items():
        try:
            resp = get_with_retry(
                _FIN_URL,
                params={
                    "crtfc_key": settings.dart_api_key,
                    "corp_code": stock.corp_code,
                    "bsns_year": year,
                    "reprt_code": reprt_code,
                },
            )
            data = resp.json()
            if data.get("status") != "000":
                continue  # 013 포함 — 재무 없음이면 제목 기반 요약으로 진행
            rows = [r for r in data.get("list", []) if r.get("sj_div") == "IS"]
            fs_div = "CFS" if any(r.get("fs_div") == "CFS" for r in rows) else "OFS"
            by_account = {}
            for r in rows:
                if r.get("fs_div") == fs_div and r.get("account_nm") not in by_account:
                    by_account[r.get("account_nm")] = r
            rcept_nos = {r.get("rcept_no") for r in rows}
            period_name = next(iter(by_account.values()), {}).get("thstrm_nm", "").strip()

            lines, slots = [], []
            for account_nm, label in _FIN_ACCOUNTS:
                row = by_account.get(account_nm)
                if row is None:
                    continue
                rendered = _format_krw(str(row.get("thstrm_amount") or ""))
                if rendered is None:
                    continue
                lines.append(f"{label}: {rendered}")
                slots.append({"label": label, "value": rendered})
            if not slots:
                continue
            fs_name = "연결" if fs_div == "CFS" else "개별"
            header = f"재무 수치({fs_name}재무제표 주요계정, {period_name})".replace("(, ", "(")
            for item in group:
                if item.source_key not in rcept_nos:  # 다른 접수번호(정정 전 원본 등) — 오적재 방지
                    continue
                item.content = header + "\n" + "\n".join(lines)
                item.detail_json = {"slots": slots}
        except Exception as e:  # 그룹 단위 격리 — 실패해도 목록 수집분은 유효
            logger.warning(
                "정기보고서 재무 API 실패 %s/%s-%s: %s", stock.stock_code, year, reprt_code, e
            )


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
                if doc_type == "유상증자결정":  # 첫 슬롯 = 조달 금액 합계 (디자인 확정, 이슈 #26)
                    total = _funding_total(row)
                    if total is not None:
                        rendered = f"{total:,}원"
                        lines.append(f"조달 금액(합계): {rendered}")
                        slots.append({"label": "조달 금액", "value": rendered})
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
    # report_nm → 유형 분류 — 세부 유형 우선, 포장 유형(주요사항보고서 등)은 후순위 (이슈 #26)
    specific_codes, wrapper_codes = dart_classification_codes()

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
            periodic_items: list = []  # 정기보고서 3종 — 재무 슬롯 대상 (이슈 #98)
            for it in items:
                title = it["report_nm"].strip()
                doc_type = next(
                    (fc for fc in specific_codes if fc in title),
                    next((fc for fc in wrapper_codes if fc in title), None),
                )
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
                elif doc_type in PERIODIC_TYPES and item.detail_json is None:  # 이슈 #98
                    periodic_items.append(item)
            _enrich_details(db, stock, typed_items, bgn_de)
            _enrich_periodic_financials(db, stock, periodic_items)
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
