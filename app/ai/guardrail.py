"""F-5.5 — 출력 가드레일. 생성물 전체 공통 후처리 (규칙 기반, 결정적).

차단: 매수·매도 권유 / 목표가·상승률 등 수치 단정 / 미래 방향 예측 /
입력에 없는 금액·수량 숫자(F-5.1.3, 날짜 제외).
위반 시 호출부가 재생성 1회 → 그래도 위반이면 해당 필드를 비운다.
"""

import re

# (사유, 패턴) — "자사주 매수 결정" 같은 사실 서술은 통과시키고 권유형만 잡는다
_FORBIDDEN: list[tuple[str, re.Pattern[str]]] = [
    (
        "매수·매도 권유",
        re.compile(
            r"(매수|매도|매집)\s*(추천|권유|기회|타이밍|적기)"
            r"|(매수|매도)\s*하(세요|시길|는\s*것이\s*좋|는\s*게\s*좋)"
            r"|(사|파)세요"
            r"|(사|팔)아야\s*(할|합니다)"
            r"|담아\s*(두세요|보세요|가세요)"
            r"|비중\s*(확대|축소)"
        ),
    ),
    (
        "수치 단정",
        re.compile(
            r"목표\s*(주)?가"
            r"|\d+(\.\d+)?\s*%\s*(까지\s*)?(상승|하락|오를|내릴|급등|급락)"
            r"|(상승|하락)\s*여력"
        ),
    ),
    (
        "방향 예측",
        re.compile(
            r"(오를|내릴|상승할|하락할|반등할|급등할|급락할)\s*(것으로|것이다|것입니다|전망|가능성이\s*(높|큽))"
            r"|(상승|하락|반등|급등|급락)(이|가)?\s*(예상|기대|전망)됩"
            r"|(상승세|하락세)를\s*(보일|이어갈)\s*(것|전망)"
        ),
    ),
]

# 금액·수량형 숫자 토큰만 검사(단위 동반) — 날짜·회차 같은 맨숫자는 제외 (확정사항 4절)
_NUMBER_TOKEN = re.compile(r"([\d,]+(?:\.\d+)?)\s*(조|억|만|천만)?\s*(원|주|달러|%|퍼센트|배)")


def find_violations(text: str | None) -> list[str]:
    if not text:
        return []
    return [reason for reason, pattern in _FORBIDDEN if pattern.search(text)]


def find_unsourced_numbers(text: str | None, source: str) -> list[str]:
    """F-5.1.3 — 생성문의 금액·수량 숫자가 입력에 없으면 위반. 입력은 콤마 제거 후 대조."""
    if not text:
        return []
    normalized_source = source.replace(",", "")
    bad: list[str] = []
    for m in _NUMBER_TOKEN.finditer(text):
        digits = m.group(1).replace(",", "")
        if digits not in normalized_source:
            bad.append(f"입력에 없는 숫자 {m.group(0).strip()}")
    return bad


def find_stock_names(text: str | None, names: list[str]) -> list[str]:
    """F-5.3.1 — 연결 문장은 업종 단위: 종목명이 나오면 위반."""
    if not text:
        return []
    return [f"종목명 사용({n})" for n in names if len(n) >= 2 and n in text]
