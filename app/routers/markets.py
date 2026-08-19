from fastapi import APIRouter

from app.deps import DbDep, OptionalSession
from app.schemas.cards import MarketsResponse
from app.services.markets import markets_overview

router = APIRouter(tags=["markets"])


@router.get("/markets", response_model=MarketsResponse)
def markets(db: DbDep, session: OptionalSession) -> MarketsResponse:
    """F-2.1~2.3 — 구분별 탭 구성·마지막 본 종목·종목 유무.

    인증 불필요(확정사항 7절) — 세션이 있으면 활용한다. 프론트는 이 응답으로
    탭을 그린다(구분 값으로 추론 금지).
    """
    return MarketsResponse(markets=markets_overview(db, session))
