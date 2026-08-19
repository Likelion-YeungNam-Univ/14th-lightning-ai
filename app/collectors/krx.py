"""F-3.1 — 국내 종목 마스터 동기화 (일 1회).

실측(2026-08-13) 기준 FinanceDataReader 필드:
- StockListing('KRX'): Code, Name, Market, Marcap (시가총액 직접 제공 — pykrx 불필요)
- StockListing('KRX-DESC'): Code, Industry(업종 문자열), Products
"""

import logging

import FinanceDataReader as fdr
import pandas as pd
from sqlalchemy.orm import Session

from app.models import MARKET_DOMESTIC, StockMaster
from app.services.industry import ETC_CODE, classify_industry, seed_domestic_industries

logger = logging.getLogger(__name__)

MARKETS = ("KOSPI", "KOSDAQ")  # 국내 상장 종목만(F-2.1). KONEX 제외


def fetch_krx_listing() -> pd.DataFrame:
    base = fdr.StockListing("KRX")[["Code", "Name", "Market", "Marcap"]]
    desc = fdr.StockListing("KRX-DESC")[["Code", "Industry", "Products"]]
    df = base.merge(desc, on="Code", how="left")
    df = df[df["Market"].isin(MARKETS)]
    # F-3.1.0(v3 갱신) — 스팩은 공시만 잔뜩 뜨고 나머지 탭이 비므로 마스터에서 제외
    df = df[~df["Name"].astype(str).str.contains("스팩", na=False)]
    return df.astype(object).where(pd.notna(df), None)  # NaN → None


def sync_stock_master(db: Session) -> dict:
    """국내 종목 마스터 upsert. 업종 매핑표(industry_agency)도 함께 갱신한다(멱등)."""
    seed_domestic_industries(db)
    df = fetch_krx_listing()

    stats = {"total": len(df), "created": 0, "updated": 0, "etc": 0}
    unmapped: dict[str, int] = {}
    existing = {
        s.stock_code: s
        for s in db.query(StockMaster).filter(StockMaster.market == MARKET_DOMESTIC).all()
    }

    for row in df.itertuples(index=False):
        industry = classify_industry(row.Industry)
        if industry == ETC_CODE:
            industry = classify_industry(row.Products)
        if industry == ETC_CODE:
            stats["etc"] += 1
            if row.Industry:
                unmapped[row.Industry] = unmapped.get(row.Industry, 0) + 1

        market_cap = int(row.Marcap) if row.Marcap is not None else None
        obj = existing.get(row.Code)
        if obj is None:
            db.add(
                StockMaster(
                    stock_code=row.Code,
                    market=MARKET_DOMESTIC,
                    name=row.Name,
                    aliases=[],
                    exchange=row.Market,  # KOSPI / KOSDAQ
                    industry_code=industry,
                    market_cap=market_cap,
                )
            )
            stats["created"] += 1
        else:
            obj.name = row.Name
            obj.exchange = row.Market
            obj.industry_code = industry
            obj.market_cap = market_cap
            stats["updated"] += 1

    db.commit()

    # 미매핑 업종은 규칙 파일(data/industry_rules.json)만 고치면 된다 — 로그로 남긴다
    if unmapped:
        top = sorted(unmapped.items(), key=lambda kv: -kv[1])[:15]
        logger.info("etc로 분류된 KRX 업종 상위: %s", top)
    return stats
