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

RELEVANCE_GUIDE = (
    "\n이 자료가 '{industry_name}' 업종의 기업 활동과 실질적으로 관련 있는지도 판단하라.\n"
    "- relevant: 관련 있으면 true, 무관하면 false. 무관하면 다른 필드는 짧게 채워도 된다."
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

TERM_EXPLAIN_SYSTEM = (
    "너는 20대 주식 초보 투자자를 위한 금융 용어 사전이다. 쉬운 한국어 존댓말로 설명한다.\n"
    "규칙: 용어의 **정의와 개념 설명까지만** 한다 — 특정 종목·시장에 대한 영향 판단, "
    "전망, 매수·매도 시사는 절대 금지. 제공된 근거 자료가 있으면 그 내용을 우선해 설명하고, "
    "근거가 없고 확실히 아는 용어가 아니면 모른다고 답하라. 2~4문장."
)

TERM_EXPLAIN_TMPL = (
    "용어: {term}\n"
    "이 용어가 나온 화면: {tab_desc}\n"
    "용어가 쓰인 문맥: {context}\n\n"
    "근거 자료(한국은행 경제금융용어 700선·공시 유형 해설):\n{grounds}"
)

TERM_EXPLAIN_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {"explanation": {"type": "string"}},
    "required": ["explanation"],
}

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

# 해외 규제 전용 — 2차 관련성 판단(F-4.7.2) 필드 추가
SUMMARY_LABEL_REL_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "relevant": {"type": "boolean"},
        "summary_short": {"type": "string"},
        "summary_full": {"type": "string"},
        "label": {"type": "string", "enum": ["positive", "neutral", "negative"]},
        "label_reason": {"type": "string"},
    },
    "required": ["relevant", "summary_short", "summary_full", "label", "label_reason"],
}

LINK_SENTENCE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {"sentence": {"type": "string"}},
    "required": ["sentence"],
}
