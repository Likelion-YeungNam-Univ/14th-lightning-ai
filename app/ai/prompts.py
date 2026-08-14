"""F-5 — 프롬프트·출력 스키마 상수 (CLAUDE.md: 프롬프트는 상수/템플릿으로 분리)."""

SYSTEM_COMMON = (
    "너는 20대 주식 초보 투자자를 돕는 금융 정보 도우미다. "
    "쉬운 한국어 존댓말로 쓰되, 정보 전달만 한다.\n"
    "절대 금지: 매수·매도 권유, 목표가·상승률 같은 수치 단정, 주가 방향 예측, "
    "입력에 없는 사실·숫자·금액 생성. 영어 원문 입력은 한국어로 요약한다."
)

SUMMARY_USER_TMPL = (
    "다음 자료를 요약하라.\n"
    "- summary_short: 목록 카드용 2~3문장. 무슨 일이 있었는지만.\n"
    "- summary_full: 상세 시트용 4~6문장. 이 자료가 무엇이고 왜 중요한지 초보자에게 설명.\n"
    "자료 종류: {kind}\n자료:\n{text}"
)

LABEL_GUIDE = (
    "\n추가로 이 자료가 해당 {unit}에 미치는 영향 라벨을 정하라.\n"
    "- label: positive/neutral/negative 중 하나. 조금이라도 애매하면 neutral.\n"
    "- label_reason: 판단 이유 1~2문장. 퍼센트(%) 표기 금지, 예측 표현 금지."
)

RETRY_SUFFIX = (
    "\n\n[재생성 요청] 직전 출력이 금지 표현을 포함했다: {reasons}. "
    "해당 표현 없이 사실 서술만으로 다시 작성하라."
)

LINK_SENTENCE_USER_TMPL = (
    "'{indicator_name}'이(가) 현재 {value}이고 최근 변동 방향은 '{direction}'이다.\n"
    "'{industry_name}' 업종({profile})을 가진 종목을 보는 초보 투자자에게, "
    "이 금리 수준이 이 업종과 어떤 관계가 있는지 1~2문장으로 설명하라.\n"
    "규칙: 특정 회사·종목 이름 금지, 주가 방향 예측 금지, 매수·매도 시사 금지, "
    "새로운 수치 생성 금지. '~하는 경향이 있어요'처럼 일반적 관계만 서술하라."
)

# structured output — strict 모드는 모든 필드 required + additionalProperties false 필요
SUMMARY_LABEL_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "summary_short": {"type": "string"},
        "summary_full": {"type": "string"},
        "label": {"type": "string", "enum": ["positive", "neutral", "negative"]},
        "label_reason": {"type": "string"},
    },
    "required": ["summary_short", "summary_full", "label", "label_reason"],
}

SUMMARY_ONLY_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "summary_short": {"type": "string"},
        "summary_full": {"type": "string"},
    },
    "required": ["summary_short", "summary_full"],
}

LINK_SENTENCE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {"sentence": {"type": "string"}},
    "required": ["sentence"],
}
