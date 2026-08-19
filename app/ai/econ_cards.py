"""E-2·E-3·E-5 — 경제 상식 카드 생성·자동검증·회전.

파이프라인: 웹 검색 도구로 생성(E-2) → 자동 반려 필터(E-3.1) → 표본 검수 대기(filtered)
→ 사람이 배치 단위로 승인/반려(E-3.2) → 2시간 회전 노출(E-5.2).

사용자 요청 경로에서 LLM을 부르지 않는다(불변식 1) — 전부 스크립트/배치에서 호출한다.
"""

from __future__ import annotations

import json
import logging
import math
import re
import uuid
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

import httpx
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.ai.guardrail import find_absolute_claims, find_any_numbers, find_violations
from app.ai.llm_client import LLMError, OpenAIClient, get_llm_client
from app.ai.prompts import ECON_CARD_SCHEMA, ECON_CARD_SYSTEM, ECON_CARD_USER_TMPL
from app.models import EconCard, EconRotation

logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"

MAX_SEARCHES_PER_CARD = 5  # E-2.1a-2
SAMPLE_MIN = 2  # E-3.2 — 최소 2건
SAMPLE_RATE = 0.10  # E-3.2 — 10%
ROTATION_SIZE = 10  # E-5.2
RECENT_TITLES_LIMIT = 30  # E-2.2.1 프롬프트에 넣을 최근 제목 개수

_FOOTNOTE = re.compile(r"\((\d+)\)")


def _allowed_domains() -> list[str]:
    with open(DATA_DIR / "econ_allowed_domains.json", encoding="utf-8") as f:
        return json.load(f)["domains"]


def _domain_allowed(url: str, allowed: list[str]) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return any(host == d or host.endswith(f".{d}") for d in allowed)


def _link_alive(url: str) -> bool:
    """E-3.1.6 — 없는 URL을 지어낸 경우를 잡는다. 네트워크 실패도 반려로 취급."""
    try:
        resp = httpx.head(url, timeout=5.0, follow_redirects=True)
        return resp.status_code == 200
    except httpx.HTTPError:
        return False


def _recent_titles(db: Session, limit: int = RECENT_TITLES_LIMIT) -> list[str]:
    rows = (
        db.query(EconCard.title)
        .filter(EconCard.status.in_(["filtered", "approved"]))
        .order_by(EconCard.created_at.desc())
        .limit(limit)
        .all()
    )
    return [t for (t,) in rows]


def auto_filter(card: dict) -> list[str]:
    """E-3.1 — 하나라도 걸리면 반려. 순서는 세부사항과 무관, 전부 모아서 반환한다."""
    reasons: list[str] = []
    sources = card.get("sources") or []
    body = card.get("body") or ""

    if not sources:  # E-3.1.1
        reasons.append("출처 없음")

    allowed = _allowed_domains()
    for s in sources:  # E-3.1.2
        url = s.get("url", "")
        if not url or not _domain_allowed(url, allowed):
            reasons.append(f"허용 도메인 밖: {url}")

    if find_any_numbers(body):  # E-3.1.3
        reasons.append("본문에 숫자 포함")

    if find_absolute_claims(body):  # E-3.1.4
        reasons.append("단정적 인과 표현")

    guardrail_hits = find_violations(body)  # E-3.1.5 (F-5.5 재사용)
    reasons.extend(guardrail_hits)

    if sources and not any(_link_alive(s.get("url", "")) for s in sources):
        # 전부 죽은 링크면 반려. 일부만 죽은 건 표본 검수 단계에서 사람이 판단(과도한 반려 방지)
        if all(not _link_alive(s.get("url", "")) for s in sources):
            reasons.append("출처 링크 전부 확인 불가")

    footnote_numbers = {int(n) for n in _FOOTNOTE.findall(body)}
    source_numbers = {s.get("number") for s in sources}
    if footnote_numbers != source_numbers:  # E-3.1.7
        reasons.append(f"각주 불일치: 본문 {footnote_numbers} vs sources {source_numbers}")

    return reasons


def generate_one(db: Session, client: OpenAIClient, batch_id: str) -> EconCard | None:
    """카드 1건 생성 + 자동 필터.

    검색 상한은 요청 자체(`max_tool_calls`)에 걸어 과금을 원천 차단하고(승래 리뷰),
    아래 카운트 확인은 방어적으로 남겨둔다 — API가 상한을 안 지켰을 경우의 안전망이다.
    """
    system = ECON_CARD_SYSTEM.format(domains=", ".join(_allowed_domains()))
    user = ECON_CARD_USER_TMPL.format(recent_titles="\n".join(_recent_titles(db)) or "(없음)")
    try:
        data, search_count = client.generate_with_search(
            system=system,
            user=user,
            schema=ECON_CARD_SCHEMA,
            name="econ_card",
            max_tool_calls=MAX_SEARCHES_PER_CARD,
        )
    except LLMError as e:
        logger.warning("경제카드 생성 실패: %s", e)
        return None

    if search_count > MAX_SEARCHES_PER_CARD:
        logger.info("검색 %d회 초과로 폐기", search_count)
        return None

    reasons = auto_filter(data)
    card = EconCard(
        title=(data.get("title") or "")[:80],
        body=data.get("body") or "",
        sources=data.get("sources") or [],
        batch_id=batch_id,
        status="rejected" if reasons else "filtered",
        reject_reason="; ".join(reasons) if reasons else None,
    )
    db.add(card)
    db.commit()
    return card


def generate_batch(
    db: Session, client: OpenAIClient, count: int, batch_id: str | None = None
) -> dict:
    """E-5.3·E-5.3a — 초기 적재(120)와 보충(5~10) 공용. 실패 1건이 배치를 막지 않는다."""
    batch_id = batch_id or str(uuid.uuid4())
    stats = {"batch_id": batch_id, "requested": count, "filtered": 0, "rejected": 0, "discarded": 0}
    for _ in range(count):
        card = generate_one(db, client, batch_id)
        if card is None:
            stats["discarded"] += 1
        elif card.status == "filtered":
            stats["filtered"] += 1
        else:
            stats["rejected"] += 1
    logger.info("econ card batch %s: %s", batch_id, stats)
    return stats


def generate_batch_background(count: int, batch_id: str) -> None:
    """BackgroundTasks 진입점(E-7, 승래 리뷰) — 요청 세션과 분리된 자체 DB 세션을 쓴다.

    120건 생성은 수 분 걸릴 수 있어 관리자 요청을 오래 붙잡지 않는다. 결과는 반환하지
    않고 로그로만 남긴다 — 호출부는 202 + batch_id만 즉시 받는다.
    """
    from app.db import SessionLocal

    with SessionLocal() as db:
        try:
            client = get_llm_client()
            generate_batch(db, client, count, batch_id=batch_id)
        except LLMError as e:
            logger.warning("econ card batch %s 실패: %s", batch_id, e)


def review_sample(db: Session, batch_id: str) -> list[EconCard]:
    """E-3.2 — 자동 필터 통과분 중 10%·최소 2건. 배치가 20건 미만이어도 2건은 본다."""
    filtered = (
        db.query(EconCard)
        .filter(EconCard.batch_id == batch_id, EconCard.status == "filtered")
        .all()
    )
    if not filtered:
        return []
    n = max(SAMPLE_MIN, math.ceil(len(filtered) * SAMPLE_RATE))
    return filtered[: min(n, len(filtered))]


def apply_batch_review(db: Session, batch_id: str, *, passed: bool, reviewer: str) -> dict:
    """E-3.2.1 — 표본 결과를 배치 전체에 적용. 통과 시 전부 승인+잠금(E-3.3), 실패 시 전부 반려."""
    filtered = db.query(EconCard).filter(
        EconCard.batch_id == batch_id, EconCard.status == "filtered"
    )
    now = datetime.now()
    count = 0
    for card in filtered.all():
        if passed:
            card.status = "approved"
            card.locked = True
            card.approved_by = reviewer
            card.approved_at = now
        else:
            card.status = "rejected"
            card.reject_reason = "표본 검수 실패 — 배치 전체 반려"
        count += 1
    db.commit()
    return {"batch_id": batch_id, "passed": passed, "affected": count}


def rotate(db: Session) -> list[int]:
    """E-5.2 — 승인 풀에서 10장 무작위 추출.

    직전 세트 회피(E-5.2.1), 풀 부족 시 있는 만큼(E-5.2.2).
    """
    import random

    approved_ids = [
        id_ for (id_,) in db.query(EconCard.id).filter(EconCard.status == "approved").all()
    ]
    last = db.query(EconRotation).order_by(EconRotation.rotated_at.desc()).first()
    previous_ids = set(last.card_ids) if last else set()

    candidates = [i for i in approved_ids if i not in previous_ids]
    if len(candidates) < ROTATION_SIZE:
        candidates = approved_ids  # 풀이 작으면 직전 세트 회피를 포기하고 있는 만큼 채운다

    picked = random.sample(candidates, k=min(ROTATION_SIZE, len(candidates)))
    db.add(EconRotation(card_ids=picked))
    db.commit()
    return picked


def approved_pool_size(db: Session) -> int:
    return (
        db.query(func.count())
        .select_from(EconCard)
        .filter(EconCard.status == "approved")
        .scalar()
    )
