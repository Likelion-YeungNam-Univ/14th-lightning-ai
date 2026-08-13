# assit 백엔드

내 종목에 걸리는 정보(유튜브·공시·규제·한국은행·Fed)를 출처별 탭으로 정리해 보여주는 서비스.
구현 기준 문서와 팀 규칙은 [`docs/assit_요구사항명세서_v2.1.md`](docs/assit_요구사항명세서_v2.1.md)와 [`.claude/CLAUDE.md`](.claude/CLAUDE.md) 참조.

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
data/       업종 12분류·부처 매핑 등 코드가 아닌 데이터 (수정 시 배포 불필요)
scripts/    1회성·수동 실행 스크립트 (마스터 동기화, 지식베이스 적재 등)
docs/       명세·PRD·개발계획서 (문서가 코드에 우선한다)
```
