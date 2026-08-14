# assit 백엔드

초보 투자자가 **본인이 보유한 종목에 걸리는 정보(유튜브·공시·규제·한국은행·Fed)**를
국내/해외 구분 · 출처별 탭 한 화면에서 확인할 수 있는 투자 정보 통합 대시보드.
구현 기준 문서와 팀 규칙은 [`docs/assit_요구사항명세서_v3.md`](docs/assit_요구사항명세서_v3.md)와
[`.claude/CLAUDE.md`](.claude/CLAUDE.md) 참조.

## BE Team Members
| 류승래 | 장문경 |
|:------:|:------:|
| <img src="https://avatars.githubusercontent.com/u/138495924?v=4" width="150"> | <img src="https://avatars.githubusercontent.com/u/169876583?v=4" width="150"> |
| [GitHub](https://github.com/ryu2293) | [GitHub](https://github.com/mujang3) |

## Key Features
- **세션 · 로그인 (F-1)** — 비로그인으로 전체 열람, 종목 추가·카드 저장 두 지점만 모의 로그인 요구
- **국내 / 해외 구분 (F-2)** — 구분에 따라 탭 구성이 달라짐(국내 5탭 / 해외 4탭), 구분별 마지막 조회 종목 기억
- **종목 관리 (F-3)** — 국내·해외 각각 검색·최대 30개 등록·순서 변경·삭제, 국내는 기본 종목 4개 자동 등록
- **외부 데이터 수집 (F-4)** — 국내(DART 공시·정책브리핑 규제·ECOS 한국은행)/해외(SEC EDGAR 공시·Federal Register 규제·FRED)+유튜브, 매일 06:00 배치
- **AI 가공 (F-5)** — Claude API로 공시·규제·금리 2단 요약, 긍정/중립/부정 라벨+판단 이유, "내 종목엔" 연결 문장, 용어 풀이(RAG), 매수·매도 권유 등 금지 표현 가드레일
- **콘텐츠 조회 (F-6)** — 탭×종목 카드 목록, 공통 스키마로 프론트 렌더링 단순화
- **카드 저장 (F-7)** — 모든 탭 카드 저장, 저장 시점 스냅샷 고정, 국내/해외 구분 없이 통합 조회
- **공통·비기능 (F-8)** — 에러 응답 규격, 전환율 이벤트 로깅, 레이트리밋, 시크릿 환경변수 관리

## 시작하기

```bash
# 1. DB (Postgres 16 + pgvector)
docker compose up -d db

# 2. 파이썬 환경
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

# 3. 환경 변수
cp .env.example .env   # 키 값 채우기 (키 없이도 서버 기동·종목 동기화는 가능)

# 4. 종목 마스터 적재 (KRX — 키 불필요)
.venv/bin/python -m scripts.sync_stock_master

# 5. DART 고유번호 매핑 (DART_API_KEY 필요)
.venv/bin/python -m scripts.sync_corp_codes

# 6. 개발 서버 (워커 1개 고정 — CLAUDE.md 불변식 3)
.venv/bin/uvicorn app.main:app --reload
```

확인: http://localhost:8000/health , API 문서: http://localhost:8000/docs

## 테스트 · 린트 (머지 전 필수)

```bash
.venv/bin/pytest
.venv/bin/ruff check .
```

테스트는 실 DB·외부 API·API 키 없이 돈다(sqlite + 목킹).

## 구조

```
app/        FastAPI 앱 — routers(엔드포인트) / services(로직) / collectors(외부 수집) / ai(LLM·RAG)
data/       업종 분류·부처 매핑 등 코드가 아닌 데이터 (수정 시 배포 불필요)
scripts/    1회성·수동 실행 스크립트 (마스터 동기화, 지식베이스 적재 등)
docs/       명세·PRD·개발계획서 (문서가 코드에 우선한다)
```
