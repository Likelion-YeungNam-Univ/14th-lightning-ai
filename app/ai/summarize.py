"""F-5.1·F-5.2 — 요약 2단 + 영향 라벨을 한 번의 호출로 생성.

- 대상 탭: 공시·규제·한국은행·Fed (유튜브 제외 — F-5.1). 입력 범위는 탭별 고정.
- 라벨: 공시(자료×종목)·규제(자료×업종)만. 금리 탭은 요약만 (F-5.2, F-5.2.1).
- locked=true 행은 어떤 배치도 덮어쓰지 않는다 (F-5.7).
- 이미 생성된 행은 건너뛴다(멱등). 원자료 갱신 시 무효화(F-5.6)는 새 source_key로
  들어오는 것이 일반적이라 별도 감지는 두지 않는다.
"""

import logging

from sqlalchemy.orm import Session

from app.ai.guardrail import find_unsourced_numbers, find_violations
from app.ai.llm_client import OpenAIClient
from app.ai.prompts import (
    LABEL_GUIDE,
    RETRY_SUFFIX,
    SUMMARY_LABEL_SCHEMA,
    SUMMARY_ONLY_SCHEMA,
    SUMMARY_USER_TMPL,
    SYSTEM_COMMON,
)
from app.models import (
    GeneratedContent,
    SourceItem,
    SourceItemIndustry,
    SourceItemStock,
    StockMaster,
)

logger = logging.getLogger(__name__)

TARGET_TABS = ("disclosure", "regulation", "bok", "fed")
LABEL_TABS = ("disclosure", "regulation")
KIND_BY_TAB = {
    "disclosure": "기업 공시(회사가 의무적으로 알리는 공식 발표)",
    "regulation": "정부 부처의 규제·정책 발표",
    "bok": "한국은행 기준금리 결정",
    "fed": "미국 기준금리(FOMC) 결정",
}


def _input_text(item: SourceItem, unit_name: str | None) -> str:
    """탭별 고정 입력 (F-5.1) — 다른 탭 자료를 섞지 않는다."""
    if item.tab == "disclosure":
        parts = [f"종목: {unit_name}" if unit_name else "", f"제목: {item.title}"]
        if item.doc_type:
            parts.append(f"공시유형: {item.doc_type}")
        return " / ".join(p for p in parts if p)
    if item.tab == "regulation":
        return f"제목: {item.title}\n본문: {(item.content or '')[:4000]}"
    # bok / fed — 지표 + 결정 요지
    parts = [f"제목: {item.title}"]
    if item.indicator_value:
        parts.append(f"현재 금리: {item.indicator_value} (최근 변동: {item.doc_type or '동결'})")
    if item.content:
        parts.append(f"결정 요지: {item.content[:2000]}")
    return "\n".join(parts)


def _units(db: Session, item: SourceItem) -> list[tuple[str, str, str | None]]:
    """생성 단위 (F-5.2.1): (scope, scope_key, 표시명)."""
    if item.tab == "disclosure":
        rows = db.query(SourceItemStock).filter_by(source_item_id=item.id).all()
        names = {
            s.stock_code: s.name
            for s in db.query(StockMaster).filter(
                StockMaster.stock_code.in_([r.stock_code for r in rows])
            )
        }
        return [("stock", r.stock_code, names.get(r.stock_code)) for r in rows]
    if item.tab == "regulation":
        rows = db.query(SourceItemIndustry).filter_by(source_item_id=item.id).all()
        return [("industry", r.industry_key, None) for r in rows]
    return [("global", "global", None)]


def _guarded_generate(
    client: OpenAIClient, *, user: str, schema: dict, source_text: str, with_label: bool
) -> dict:
    """생성 → 가드레일 → 위반 시 재생성 1회 → 그래도 위반이면 해당 필드 비움 (F-5.5)."""
    out = client.generate_json(system=SYSTEM_COMMON, user=user, schema=schema)
    for attempt in (1, 2):
        summary_bad = (
            find_violations(out.get("summary_short"))
            + find_violations(out.get("summary_full"))
            + find_unsourced_numbers(out.get("summary_short"), source_text)
            + find_unsourced_numbers(out.get("summary_full"), source_text)
        )
        label_bad = find_violations(out.get("label_reason")) if with_label else []
        if with_label and "%" in (out.get("label_reason") or ""):
            label_bad.append("라벨 이유 퍼센트 표기")  # F-5.2 규칙
        if not summary_bad and not label_bad:
            return out
        if attempt == 1:
            reasons = ", ".join(dict.fromkeys(summary_bad + label_bad))
            logger.info("가드레일 위반 재생성: %s", reasons)
            retry_user = user + RETRY_SUFFIX.format(reasons=reasons)
            out = client.generate_json(system=SYSTEM_COMMON, user=retry_user, schema=schema)
        else:  # 재생성도 위반 — 위반 필드만 비운다
            if summary_bad:
                out["summary_short"] = out["summary_full"] = None
            if label_bad:
                out["label"] = out["label_reason"] = None
    return out


def generate_summaries(
    db: Session,
    client: OpenAIClient,
    items: list[SourceItem] | None = None,
    limit: int | None = None,
) -> dict:
    """생성 단위별 실패 격리. 반환: 통계."""
    if items is None:
        items = (
            db.query(SourceItem)
            .filter(SourceItem.tab.in_(TARGET_TABS))
            .order_by(SourceItem.published_at.desc().nullslast())
            .all()
        )
    stats = {"generated": 0, "skipped": 0, "locked": 0, "emptied": 0, "failed": 0}

    for item in items:
        if item.tab not in TARGET_TABS:
            stats["skipped"] += 1
            continue
        with_label = item.tab in LABEL_TABS
        for scope, scope_key, unit_name in _units(db, item):
            if limit is not None and stats["generated"] >= limit:
                return stats
            existing = (
                db.query(GeneratedContent)
                .filter_by(source_item_id=item.id, scope=scope, scope_key=scope_key)
                .one_or_none()
            )
            if existing is not None and existing.locked:
                stats["locked"] += 1
                continue
            if existing is not None and existing.summary_short:
                stats["skipped"] += 1
                continue

            source_text = _input_text(item, unit_name)
            user = SUMMARY_USER_TMPL.format(kind=KIND_BY_TAB[item.tab], text=source_text)
            if with_label:
                unit = "종목" if scope == "stock" else "업종"
                user += LABEL_GUIDE.format(unit=unit)
            try:
                out = _guarded_generate(
                    client,
                    user=user,
                    schema=SUMMARY_LABEL_SCHEMA if with_label else SUMMARY_ONLY_SCHEMA,
                    source_text=source_text,
                    with_label=with_label,
                )
                if existing is None:
                    existing = GeneratedContent(
                        source_item_id=item.id, scope=scope, scope_key=scope_key
                    )
                    db.add(existing)
                existing.summary_short = out.get("summary_short")
                existing.summary_full = out.get("summary_full")
                existing.label = out.get("label") if with_label else None
                existing.label_reason = out.get("label_reason") if with_label else None
                db.commit()
                if existing.summary_short is None:
                    stats["emptied"] += 1
                stats["generated"] += 1
            except Exception as e:  # 생성 단위 격리 — 한 건 실패가 배치를 막지 않는다
                db.rollback()
                stats["failed"] += 1
                logger.warning("생성 실패 item=%s %s/%s: %s", item.id, scope, scope_key, e)
    return stats
