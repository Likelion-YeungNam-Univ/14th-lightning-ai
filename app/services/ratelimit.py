"""F-8.3 — 인프로세스 레이트리밋 (슬라이딩 윈도). uvicorn 워커 1개 전제(불변식 3)라
프로세스 메모리로 충분하다. 대상: LLM 동반 엔드포인트(POST /terms/explain)."""

import time
from collections import defaultdict, deque

WINDOW_SECONDS = 60
SESSION_LIMIT_PER_MINUTE = 20  # F-8.3 제안값 채택
IP_LIMIT_PER_MINUTE = 60  # 공유 IP(학교망 등) 고려해 세션 한도의 3배

_hits: dict[str, deque] = defaultdict(deque)


def allow(key: str, limit: int, now: float | None = None) -> bool:
    """윈도 내 호출 수가 한도 미만이면 기록 후 True."""
    now = time.monotonic() if now is None else now
    window = _hits[key]
    while window and now - window[0] >= WINDOW_SECONDS:
        window.popleft()
    if len(window) >= limit:
        return False
    window.append(now)
    return True


def reset() -> None:
    """테스트·데모 리셋용."""
    _hits.clear()
