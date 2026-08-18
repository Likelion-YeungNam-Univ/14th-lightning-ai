"""국내 업종 12분류 (F-3.1.2, 확정사항 2절 B2).

KRX 업종/산업 문자열을 자체 12분류 코드로 변환한다.
규칙과 매핑표는 코드가 아니라 data/*.json 데이터로 관리한다 (F-4.2.1).
"""

import json
from functools import lru_cache
from pathlib import Path

from sqlalchemy.orm import Session

from app.models import MARKET_DOMESTIC, MARKET_OVERSEAS, DisclosureFormType, IndustryAgency

DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"

ETC_CODE = "etc"


@lru_cache(maxsize=1)
def load_industry_rules() -> list[dict]:
    with open(DATA_DIR / "industry_rules.json", encoding="utf-8") as f:
        return json.load(f)["rules"]


@lru_cache(maxsize=1)
def load_industry_ministries() -> list[dict]:
    with open(DATA_DIR / "industry_ministry.json", encoding="utf-8") as f:
        return json.load(f)["industries"]


def classify_industry(*texts: str | None) -> str:
    """KRX 업종·산업 문자열(들)을 12분류 코드로 변환. 규칙 순서대로 첫 매칭 채택."""
    haystack = " ".join(t for t in texts if t)
    if not haystack.strip():
        return ETC_CODE
    for rule in load_industry_rules():
        for keyword in rule["keywords"]:
            if keyword in haystack:
                return rule["industry_code"]
    return ETC_CODE


def seed_domestic_industries(db: Session) -> int:
    """industry_agency 테이블의 국내(domestic) 행을 데이터 파일로 갱신(멱등). 반환: 적재 건수."""
    rows = load_industry_ministries()
    for row in rows:
        existing = db.get(IndustryAgency, (MARKET_DOMESTIC, row["industry_code"]))
        if existing is None:
            db.add(
                IndustryAgency(
                    market=MARKET_DOMESTIC,
                    industry_key=row["industry_code"],
                    name=row["name"],
                    agencies=row["ministries"],
                    keywords=row["keywords"],
                    profile=row["profile"],
                )
            )
        else:
            existing.name = row["name"]
            existing.agencies = row["ministries"]
            existing.keywords = row["keywords"]
            existing.profile = row["profile"]
    db.commit()
    return len(rows)


@lru_cache(maxsize=1)
def load_overseas_industries() -> list[dict]:
    with open(DATA_DIR / "overseas_industry.json", encoding="utf-8") as f:
        return json.load(f)["industries"]


def seed_overseas_industries(db: Session) -> int:
    """해외(SIC) 업종 행 갱신(멱등) — F-4.7.1. industry_key = SIC 코드."""
    rows = load_overseas_industries()
    for row in rows:
        existing = db.get(IndustryAgency, (MARKET_OVERSEAS, row["sic"]))
        if existing is None:
            db.add(
                IndustryAgency(
                    market=MARKET_OVERSEAS,
                    industry_key=row["sic"],
                    name=row["name"],
                    agencies=row["agencies"],
                    keywords=row["keywords"],
                    profile=row["profile"],
                )
            )
        else:
            existing.name = row["name"]
            existing.agencies = row["agencies"]
            existing.keywords = row["keywords"]
            existing.profile = row["profile"]
    db.commit()
    return len(rows)


def seed_form_types(db: Session) -> int:
    """공시 유형 해설 시드(멱등) — 국내 report_nm 분류표 + 미국 폼 (F-4.6.1, 확정사항 5절)."""
    with open(DATA_DIR / "disclosure_form_types.json", encoding="utf-8") as f:
        data = json.load(f)
    count = 0
    for key, market in (("domestic", MARKET_DOMESTIC), ("overseas", MARKET_OVERSEAS)):
        for row in data[key]:
            existing = db.get(DisclosureFormType, (market, row["form_code"]))
            if existing is None:
                db.add(
                    DisclosureFormType(
                        market=market,
                        form_code=row["form_code"],
                        description=row["description"],
                    )
                )
            else:
                existing.description = row["description"]
            count += 1
    db.commit()
    return count


@lru_cache(maxsize=1)
def _form_type_data() -> dict:
    with open(DATA_DIR / "disclosure_form_types.json", encoding="utf-8") as f:
        return json.load(f)


def displayed_form_codes(market_key: str) -> frozenset[str]:
    """이슈 #18 — 카드로 노출하는 공시 유형(display=true). 수집은 전체, 노출만 거른다."""
    return frozenset(
        row["form_code"] for row in _form_type_data()[market_key] if row.get("display")
    )


@lru_cache(maxsize=1)
def load_dart_detail_map() -> dict:
    """이슈 #18 — 정형 API 매핑표: form_code → {endpoint, fields}."""
    with open(DATA_DIR / "dart_detail_map.json", encoding="utf-8") as f:
        return json.load(f)["types"]


def dart_classification_codes() -> tuple[list[str], list[str]]:
    """DART report_nm 분류용 코드 목록: (세부 유형, 포장 유형) — 각각 긴 이름 우선.

    '주요사항보고서(유상증자결정)'처럼 포장 유형이 세부 유형보다 길 수 있어
    단순 길이 정렬로는 포장이 먼저 잡힌다(이슈 #26에서 발견) — 세부 유형을 먼저 대조한다.
    """
    rows = _form_type_data()["domestic"]
    specific = sorted((r["form_code"] for r in rows if not r.get("wrapper")), key=len, reverse=True)
    wrappers = sorted((r["form_code"] for r in rows if r.get("wrapper")), key=len, reverse=True)
    return specific, wrappers


@lru_cache(maxsize=1)
def load_sec_form_items() -> dict[str, str]:
    """이슈 #53 — 8-K Item 코드 → 한국어 사안명 매핑."""
    with open(DATA_DIR / "sec_form_items.json", encoding="utf-8") as f:
        return json.load(f)["items"]
