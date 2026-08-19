"""F-5.3 — "내 종목엔" 문장. 업종 단위 1~2문장, 지표 스냅샷 키로 캐시.

- 국내 종목은 한국은행·Fed 두 탭, 해외 종목은 Fed 탭에만 (F-5.3).
- 캐시 키: market+tab+업종+indicator_version — 지표 갱신 시에만 새로 생성 (F-5.3.2).
- 종목명 등장·금지 표현이면 재생성 1회, 그래도 위반이면 행을 만들지 않는다
  → 조회 시 문장 없이 카드만 노출 (F-5.3, F-5.3.1).
"""

import logging

from sqlalchemy.orm import Session

from app.ai.guardrail import find_stock_names, find_unsourced_numbers, find_violations
from app.ai.llm_client import OpenAIClient
from app.ai.prompts import (
    LINK_SENTENCE_SCHEMA,
    LINK_SENTENCE_USER_TMPL,
    RETRY_SUFFIX,
    SYSTEM_COMMON,
)
from app.models import IndustryAgency, RateLinkSentence, SourceItem, StockMaster

logger = logging.getLogger(__name__)

# (market, tab) 유효 조합 — F-2.4의 탭 구성과 일치
TAB_COMBOS = (("domestic", "bok"), ("domestic", "fed"), ("overseas", "fed"))
INDICATOR_NAME = {"bok": "한국은행 기준금리", "fed": "미국 기준금리"}


def _latest_indicator(db: Session, tab: str) -> SourceItem | None:
    return (
        db.query(SourceItem)
        .filter(SourceItem.tab == tab, SourceItem.indicator_value.isnot(None))
        .order_by(SourceItem.published_at.desc().nullslast())
        .first()
    )


def generate_link_sentences(db: Session, client: OpenAIClient, limit: int | None = None) -> dict:
    stats = {"generated": 0, "cached": 0, "no_indicator": 0, "dropped": 0, "failed": 0}

    for market, tab in TAB_COMBOS:
        indicator = _latest_indicator(db, tab)
        if indicator is None:  # 지표 없음 → 문장 없이 카드만 (F-5.3)
            stats["no_indicator"] += 1
            continue
        direction = indicator.doc_type or "동결"
        version = f"{indicator.indicator_value}|{direction}"
        stock_names = [
            name for (name,) in db.query(StockMaster.name).filter(StockMaster.market == market)
        ]
        industries = (
            db.query(IndustryAgency)
            .filter(IndustryAgency.market == market, IndustryAgency.profile.isnot(None))
            .all()
        )

        for ind in industries:
            if limit is not None and stats["generated"] >= limit:
                return stats
            key = (market, tab, ind.industry_key, version)
            if db.get(RateLinkSentence, key) is not None:
                stats["cached"] += 1
                continue

            user = LINK_SENTENCE_USER_TMPL.format(
                indicator_name=INDICATOR_NAME[tab],
                value=indicator.indicator_value,
                direction=direction,
                industry_name=ind.name,
                profile=ind.profile,
            )
            try:
                out = client.generate_json(
                    system=SYSTEM_COMMON, user=user, schema=LINK_SENTENCE_SCHEMA
                )
                sentence = (out.get("sentence") or "").strip()
                bad = (
                    find_violations(sentence)
                    + find_stock_names(sentence, stock_names)
                    + find_unsourced_numbers(sentence, user)
                )
                if bad:
                    reasons = ", ".join(dict.fromkeys(bad))
                    logger.info("연결 문장 재생성(%s/%s): %s", tab, ind.industry_key, reasons)
                    out = client.generate_json(
                        system=SYSTEM_COMMON,
                        user=user + RETRY_SUFFIX.format(reasons=reasons),
                        schema=LINK_SENTENCE_SCHEMA,
                    )
                    sentence = (out.get("sentence") or "").strip()
                    bad = (
                        find_violations(sentence)
                        + find_stock_names(sentence, stock_names)
                        + find_unsourced_numbers(sentence, user)
                    )
                if bad or not sentence:  # 재생성도 위반 — 행을 만들지 않는다
                    stats["dropped"] += 1
                    continue
                db.add(
                    RateLinkSentence(
                        market=market,
                        tab=tab,
                        industry_key=ind.industry_key,
                        indicator_version=version,
                        sentence=sentence,
                    )
                )
                db.commit()
                stats["generated"] += 1
            except Exception as e:  # 업종 단위 격리
                db.rollback()
                stats["failed"] += 1
                logger.warning("연결 문장 실패 %s/%s/%s: %s", market, tab, ind.industry_key, e)
    return stats
