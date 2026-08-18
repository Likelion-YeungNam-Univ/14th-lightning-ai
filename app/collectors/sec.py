"""F-3.2·F-4.6 — 해외 화이트리스트 마스터 + SEC EDGAR 공시 수집.

실측(2026-08-14):
- company_tickers.json: {ticker, cik_str, title} — DART corp_code 매핑의 미국판 (F-3.2.1)
- submissions/CIK{10자리}.json: name·sic·sicDescription + filings.recent 병렬 배열
  (form, filingDate, accessionNumber, primaryDocument, primaryDocDescription)
- **User-Agent 헤더 필수**(없으면 차단), 요청 한도 초당 10회 → 종목 간 0.15s 간격.
"""

import html
import json
import logging
import re
import time
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from app.collectors.base import (
    ensure_stock_link,
    get_with_retry,
    mark_status,
    upsert_source_item,
)
from app.config import settings
from app.deps import utcnow
from app.models import MARKET_OVERSEAS, SourceItem, StockMaster
from app.services.industry import (
    DATA_DIR,
    load_overseas_industries,
    load_sec_form_items,
    seed_form_types,
    seed_overseas_industries,
)

logger = logging.getLogger(__name__)

TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"
TARGET_FORMS = ("8-K", "10-Q", "10-K", "6-K")  # 확정사항 3절
WINDOW_DAYS = 90  # 최근 3개월 (국내 공시와 동일)
DISCLOSURES_PER_STOCK = 20
SLEEP_BETWEEN = 0.15  # 초당 10회 한도 준수
COMPANYFACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
# 이슈 #53 — 8-K 첫 Item 문단은 SEC 서식상 정형 요약(실측 본문 3KB, 첫 문단에 핵심). 상한 400자
EIGHT_K_SNIPPET_CHARS = 400
_TAG_RE = re.compile(r"<[^>]+>")
_ITEM_HEAD_RE = re.compile(r"Item\s+\d\.\d\d\.?\s*", re.I)
# companyfacts에서 슬롯으로 쓰는 개념 — (라벨, 후보 개념 순서, 단위)
FINANCIAL_SLOTS = (
    (
        "매출",
        ("Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax", "SalesRevenueNet"),
        "USD",
    ),
    ("순이익", ("NetIncomeLoss",), "USD"),
    ("주당순이익(희석)", ("EarningsPerShareDiluted",), "USD/shares"),
)


def _headers() -> dict:
    """SEC 필수 User-Agent — 미설정이면 호출 전에 실패시킨다 (F-4.6)."""
    if not settings.sec_user_agent:
        raise RuntimeError("SEC_USER_AGENT 미설정 — SEC는 연락처 User-Agent 없이 차단한다")
    return {"User-Agent": settings.sec_user_agent}


def fetch_ticker_cik_map() -> dict[str, dict]:
    """티커 → {cik(10자리), 영문명}."""
    data = get_with_retry(TICKERS_URL, headers=_headers()).json()
    return {
        row["ticker"]: {"cik": str(row["cik_str"]).zfill(10), "title": row["title"]}
        for row in data.values()
    }


def sync_overseas_master(db: Session) -> dict:
    """화이트리스트 15개 적재 (F-3.2). CIK·SIC는 SEC에서 실시간 해석 — 수기 오류 방지.

    시가총액은 받지 않는다(F-3.2 확정). 업종·폼 해설 시드도 함께 갱신한다.
    """
    with open(DATA_DIR / "overseas_whitelist.json", encoding="utf-8") as f:
        whitelist = json.load(f)["stocks"]
    cik_map = fetch_ticker_cik_map()
    known_sics = {row["sic"] for row in load_overseas_industries()}
    stats = {"stocks": 0, "no_cik": 0}

    for entry in whitelist:
        ticker = entry["ticker"]
        mapped = cik_map.get(ticker)
        if mapped is None:
            stats["no_cik"] += 1
            logger.warning("CIK 매핑 없음: %s — 화이트리스트 티커 확인 필요", ticker)
            continue
        sub = get_with_retry(SUBMISSIONS_URL.format(cik=mapped["cik"]), headers=_headers()).json()
        time.sleep(SLEEP_BETWEEN)
        sic = (sub.get("sic") or "").strip() or None
        if sic and sic not in known_sics:  # 매핑표 공백을 로그로 드러낸다 (F-4.7.1)
            logger.warning("overseas_industry.json에 없는 SIC: %s (%s)", sic, ticker)

        stock = db.get(StockMaster, ticker)
        if stock is None:
            stock = StockMaster(stock_code=ticker, market=MARKET_OVERSEAS, name=entry["name"])
            db.add(stock)
        stock.name = entry["name"]
        stock.aliases = entry["aliases"]
        stock.exchange = "US"
        stock.cik = mapped["cik"]
        stock.sic_code = sic
        stats["stocks"] += 1
    db.commit()
    stats["industries"] = seed_overseas_industries(db)
    stats["form_types"] = seed_form_types(db)
    logger.info("overseas master sync: %s", stats)
    return stats


def _eight_k_snippet(origin_url: str) -> str | None:
    """8-K 본문 첫 Item 문단(≤400자). 실패해도 요약은 items·해설로 진행되므로 None 허용."""
    try:
        page = get_with_retry(origin_url, headers=_headers(), timeout=20).text
    except Exception as e:  # 본문 없이도 카드는 유효
        logger.info("8-K 본문 조회 실패 %s: %s", origin_url, e)
        return None
    text = re.sub(r"\s+", " ", html.unescape(_TAG_RE.sub(" ", page)))
    m = _ITEM_HEAD_RE.search(text)
    body = text[m.end() :] if m else text
    body = _ITEM_HEAD_RE.split(body)[0]  # 다음 Item 전까지
    body = body.strip()
    return body[:EIGHT_K_SNIPPET_CHARS] if len(body) >= 40 else None


def _fmt_money(value: float, unit: str) -> str:
    if unit == "USD/shares":
        return f"${value:,.2f}"
    if abs(value) >= 1e9:
        return f"${value / 1e9:,.1f}B"
    if abs(value) >= 1e6:
        return f"${value / 1e6:,.1f}M"
    return f"${value:,.0f}"


def _period_matches_form(row: dict, form: str) -> bool:
    """10-Q는 분기(80~100일), 10-K는 연간(350~380일) 기간 값만. 시점값(start 없음)은 통과."""
    start, end = row.get("start"), row.get("end")
    if not start or not end:
        return True
    days = (datetime.strptime(end, "%Y-%m-%d") - datetime.strptime(start, "%Y-%m-%d")).days
    return 80 <= days <= 100 if form == "10-Q" else 350 <= days <= 380


def _fetch_financial_slots(cik: str) -> dict[str, list[dict]]:
    """companyfacts → 접수번호(accn)별 재무 슬롯. 10-Q/10-K 카드에 붙는다 (이슈 #53).

    실측: RevenueFromContractWithCustomerExcludingAssessedTax·NetIncomeLoss·EPS가 accn과 1:1.
    같은 accn에 여러 기간 값이 있으면 종료일(end)이 가장 늦은 것을 쓴다.
    """
    try:
        facts = get_with_retry(COMPANYFACTS_URL.format(cik=cik), headers=_headers(), timeout=60)
        gaap = facts.json().get("facts", {}).get("us-gaap", {})
    except Exception as e:
        logger.info("companyfacts 조회 실패 CIK%s: %s", cik, e)
        return {}

    # accn → label → (end, value). 실측: 같은 10-Q·같은 fp=Q3에 분기(3개월)와 누적(9개월) 행이
    # 함께 있어 fp로는 구분 불가 → start~end 기간 길이로 분기(≈90일)/연간(≈365일)만 채택
    by_accn: dict[str, dict[str, tuple[str, float]]] = {}
    for label, concepts, unit in FINANCIAL_SLOTS:
        for concept in concepts:
            rows = gaap.get(concept, {}).get("units", {}).get(unit) or []
            for row in rows:
                form, accn = row.get("form"), row.get("accn")
                if not accn or form not in ("10-Q", "10-K"):
                    continue
                if not _period_matches_form(row, form):
                    continue
                slot = by_accn.setdefault(accn, {})
                prev = slot.get(label)
                if prev is None or row["end"] > prev[0]:
                    slot[label] = (row["end"], float(row["val"]))
            # 후보 개념을 전부 훑는다 — 실측: 애플은 Revenues가 옛 공시에만 있고 최신은
            # RevenueFromContract…에만 있어, 첫 개념에서 멈추면 최신 매출이 빠진다

    unit_of = {label: unit for label, _c, unit in FINANCIAL_SLOTS}
    return {
        accn: [
            {"label": label, "value": _fmt_money(val, unit_of[label])}
            for label, (_end, val) in slots.items()
        ]
        for accn, slots in by_accn.items()
    }


def sync_sec_disclosures(db: Session, stocks: list[StockMaster]) -> dict:
    """F-4.6 — 화이트리스트 종목 공시. 대상 폼 8-K·10-Q·10-K·6-K, 종목 단위 격리(F-4.9)."""
    stats = {"stocks": 0, "items": 0, "failed": 0, "no_cik": 0}
    cutoff = (utcnow() - timedelta(days=WINDOW_DAYS)).strftime("%Y-%m-%d")

    for stock in stocks:
        if not stock.cik:
            stats["no_cik"] += 1
            continue
        try:
            sub = get_with_retry(SUBMISSIONS_URL.format(cik=stock.cik), headers=_headers()).json()
            recent = sub["filings"]["recent"]
            item_names = load_sec_form_items()
            financial_slots: dict[str, list[dict]] | None = None  # 필요할 때 1회만 조회
            count = 0
            for i in range(len(recent["form"])):
                form = recent["form"][i]
                filed = recent["filingDate"][i]
                if form not in TARGET_FORMS or filed < cutoff:
                    continue
                if count >= DISCLOSURES_PER_STOCK:
                    break
                accession = recent["accessionNumber"][i]
                doc = recent["primaryDocument"][i]
                origin_url = (
                    f"https://www.sec.gov/Archives/edgar/data/{int(stock.cik)}/"
                    f"{accession.replace('-', '')}/{doc}"
                )
                existing = (
                    db.query(SourceItem)
                    .filter_by(tab="disclosure", market=MARKET_OVERSEAS, source_key=accession)
                    .one_or_none()
                )
                content, detail_json = (
                    (existing.content, existing.detail_json) if existing else (None, None)
                )
                if content is None:  # 요약 입력 — 이미 채운 건은 재조회하지 않는다(멱등·호출 절약)
                    lines: list[str] = []
                    if form == "8-K":
                        codes = [
                            c.strip()
                            for c in (
                                (recent.get("items") or [None] * len(recent["form"]))[i] or ""
                            ).split(",")
                            if c.strip()
                        ]
                        names = [f"{item_names.get(c, '기타')}({c})" for c in codes]
                        if names:
                            lines.append("사안: " + ", ".join(names))
                        snippet = _eight_k_snippet(origin_url)
                        time.sleep(SLEEP_BETWEEN)
                        if snippet:
                            lines.append(f"본문 요지(영문): {snippet}")
                    elif form in ("10-Q", "10-K"):
                        if financial_slots is None:
                            financial_slots = _fetch_financial_slots(stock.cik)
                            time.sleep(SLEEP_BETWEEN)
                        slots = financial_slots.get(accession) or []
                        if slots:
                            detail_json = {"slots": slots}
                            lines.append(
                                "재무 수치: "
                                + ", ".join(f"{s['label']} {s['value']}" for s in slots)
                            )
                    content = "\n".join(lines) or None
                item = upsert_source_item(
                    db,
                    tab="disclosure",
                    market=MARKET_OVERSEAS,
                    source_key=accession,
                    # SEC엔 산문 제목이 없다 — 문서 설명(영문) 그대로, 미번역 (F-5.1.2)
                    title=(recent["primaryDocDescription"][i] or form).strip(),
                    doc_type=form,
                    published_at=datetime.strptime(filed, "%Y-%m-%d"),
                    origin_url=origin_url,
                    content=content,
                    detail_json=detail_json,
                )
                ensure_stock_link(db, item.id, stock.stock_code)
                count += 1
            db.commit()
            mark_status(db, "disclosure", stock.stock_code, True, f"{count}건")
            stats["stocks"] += 1
            stats["items"] += count
        except Exception as e:  # 종목 단위 격리
            db.rollback()
            mark_status(db, "disclosure", stock.stock_code, False, str(e))
            stats["failed"] += 1
            logger.warning("SEC 수집 실패 %s: %s", stock.stock_code, e)
        time.sleep(SLEEP_BETWEEN)
    return stats
