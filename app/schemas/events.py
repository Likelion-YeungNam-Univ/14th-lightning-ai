"""F-8.2 — 지표 이벤트 요청/응답 스키마."""

from pydantic import BaseModel

# 필수 이벤트 11종 (F-8.2) — 이 외 이름은 400
EVENT_NAMES = frozenset(
    {
        "home_view",  # 홈 도달
        "market_switch",  # 구분 전환
        "first_tab_view",  # 첫 종목 탭 진입
        "tab_view",  # 탭별 진입
        "youtube_to_other_tab",  # 유튜브 → 타 탭 이동
        "sheet_open",  # 요약 시트 열람
        "origin_click",  # 시트 → 원문 이동
        "stock_add",  # 종목 추가
        "login",  # 로그인 전환
        "card_save",  # 카드 저장
        "revisit",  # 재방문
    }
)

# payload 허용 키 — 구조적으로 이 외 데이터(평단가 등 금액 정보)는 저장하지 않는다 (F-8.6)
ALLOWED_PAYLOAD_KEYS = frozenset(
    {"tab", "market", "stock_code", "card_id", "from_tab", "to_tab", "is_default"}
)


class EventRequest(BaseModel):
    event_name: str
    payload: dict | None = None


class EventResponse(BaseModel):
    accepted: bool
