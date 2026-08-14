"""F-3.2·F-4.6 — 해외 화이트리스트 마스터 + SEC EDGAR 공시 수집.

실측(2026-08-14):
- company_tickers.json: {ticker, cik_str, title} — DART corp_code 매핑의 미국판 (F-3.2.1)
- submissions/CIK{10자리}.json: name·sic·sicDescription + filings.recent 병렬 배열
  (form, filingDate, accessionNumber, primaryDocument, primaryDocDescription)
- **User-Agent 헤더 필수**(없으면 차단), 요청 한도 초당 10회 → 종목 간 0.15s 간격.
"""

import json
import logging
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
from app.models import MARKET_OVERSEAS, StockMaster
from app.services.industry import (
    DATA_DIR,
    load_overseas_industries,
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
                item = upsert_source_item(
                    db,
                    tab="disclosure",
                    market=MARKET_OVERSEAS,
                    source_key=accession,
                    # SEC엔 산문 제목이 없다 — 문서 설명(영문) 그대로, 미번역 (F-5.1.2)
                    title=(recent["primaryDocDescription"][i] or form).strip(),
                    doc_type=form,
                    published_at=datetime.strptime(filed, "%Y-%m-%d"),
                    origin_url=(
                        f"https://www.sec.gov/Archives/edgar/data/{int(stock.cik)}/"
                        f"{accession.replace('-', '')}/{doc}"
                    ),
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
