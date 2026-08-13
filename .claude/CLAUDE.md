# assit — 백엔드 (FastAPI 단일 서버)

멋쟁이사자처럼 해커톤 프로젝트. 20대 초보 투자자가 **내 종목에 걸리는 정보(유튜브·공시·규제·한국은행·Fed)를 출처별 탭 한 화면에서** 확인하는 서비스의 백엔드 + AI. 시연용 프로덕트이므로 "발표 날 확실히 돌아가는 단순한 구현"이 "확장 가능한 복잡한 구현"보다 항상 우선한다.

## 문서가 코드에 우선한다 (Source of Truth)

1. `docs/assit_요구사항명세서_v2.1.md` — **구현 기준 명세.** 모든 기능은 이 문서의 F-번호(F-1.1 ~ F-7.7)로 식별한다. 구현 전 반드시 해당 F-항목의 "세부 사항"을 읽고, 그 범위를 벗어나는 기능을 임의로 추가하지 않는다.
2. `docs/개발계획서.md` — 기술 스택·아키텍처 결정 근거·개발 순서(P0~P6).
3. `docs/assit_PRD_v2.md` — PM의 PRD. UX 의도가 궁금할 때 참조.
4. `docs/assit_요구사항명세서_v2.md` — PM 원본(구버전). **v2.1과 다르면 v2.1이 항상 우선.**

명세와 다르게 구현해야 할 사정이 생기면 코드를 먼저 바꾸지 말고, v2.1 문서를 수정하고 커밋 메시지에 근거를 남긴 뒤 구현한다.

## 기술 스택 (고정 — 임의 변경 금지)

- Python 3.12 / FastAPI + Uvicorn(**워커 1개 고정**) / Pydantic v2
- PostgreSQL 16 + **pgvector** / SQLAlchemy 2.0 **동기** 세션
- APScheduler(프로세스 내 배치) / httpx(수집기) / FinanceDataReader(종목 마스터 — 실측 결과 Marcap 직접 제공, pykrx 불필요)
- LLM: Anthropic Claude API, 모델 ID `claude-opus-5` (structured output 사용)
- 임베딩: OpenAI `text-embedding-3-small` (RAG, F-4.8)
- 배포: Docker 단일 이미지. 프론트는 Vercel에서 `/api/*` rewrite 프록시로 접속(F-7.7)

## 아키텍처 불변식 — 위반하는 코드를 작성하지 마라

1. **사용자 요청 경로에서 외부 API·LLM을 호출하지 않는다.** 수집(F-3)과 생성(F-4)은 배치/온디맨드에서 끝내고 DB에 적재하며, 조회 API는 DB만 읽는다. 유일한 예외는 `POST /terms/explain`(F-4.4, LLM 호출 + 레이트리밋 필수).
2. **DB를 만지는 엔드포인트는 일반 `def`로 작성한다** (FastAPI가 스레드풀에서 실행). `async def` 안에서 동기 SQLAlchemy를 호출해 이벤트 루프를 막는 코드 금지. `async`는 외부 API를 병렬 호출하는 collectors 내부에서만 쓴다.
3. **uvicorn 워커는 1개.** APScheduler가 프로세스 안에서 돌므로 워커를 늘리면 배치가 중복 실행된다. Dockerfile·실행 스크립트에서 워커 수를 바꾸지 마라.
4. **제목(title)은 LLM 생성 대상이 아니다** (F-4.1.2). 원문 그대로 저장·반환. `generated_content`에 제목 컬럼을 만들지 마라.
5. **라벨을 집계·정렬에 쓸 수 없게 만든다** (F-4.2.2, F-5.1.3). 응답에 라벨 카운트/비율 필드 금지, 정렬 파라미터로 라벨 값 수신 금지.
6. **평단가·수량 등 금액 정보는 어떤 스키마·파라미터로도 받지 않는다** (F-7.6).
7. **시크릿은 환경 변수로만.** 키·계정값·토큰을 코드/커밋/로그에 남기지 않는다.
8. **저장된 카드는 `snapshot_json`만으로 렌더 가능해야 한다** (F-6.3). `saved_card.source_item_id`는 nullable — 원자료가 삭제돼도 저장 카드는 살아 있다.
9. **LLM 생성물은 가드레일 후처리(F-4.5)를 통과한 것만 DB에 저장한다.** 매수·매도 권유, 수치 단정, 방향 예측 표현은 재생성 1회 후 실패 시 필드를 비운다. `locked=true`인 생성물은 어떤 배치도 덮어쓰지 않는다(F-4.7).
10. **유튜브 API 호출 전 반드시 `quota_usage` 카운터를 확인한다** (F-3.5.1). 80% 도달 시 신규 검색 중단. 수집은 세션이 아니라 **고유 종목 단위**로 중복 제거.

## 디렉터리 구조 (이 골격을 따른다)

```
app/
  main.py           # FastAPI 앱 생성, 라우터 등록, 스케줄러 기동
  config.py         # pydantic-settings 로 환경 변수 로드
  db.py             # 엔진·세션·get_db 의존성
  models.py         # SQLAlchemy 모델 (명세 부록 1의 테이블 13개)
  schemas/          # Pydantic 요청/응답 스키마 (공통 카드 스키마 포함)
  routers/          # session, auth, stocks, cards, terms, saved, events, admin
  services/         # 비즈니스 로직 (라우터는 얇게, 로직은 여기로)
  collectors/       # krx, dart, briefing, ecos, fred, youtube — 외부 API별 1파일
  ai/               # llm_client, summarize, label, link_sentence, term_explain,
                    # guardrail, rag(임베딩·검색)
  scheduler.py      # APScheduler 잡 정의 (일 06:00 / 주 1회 / 온디맨드)
scripts/            # seed_knowledge.py(700선 적재), seed_rate_text.py(금통위 요지),
                    # reset_demo.py 등 1회성 스크립트
tests/              # pytest
docs/               # 명세·PRD·계획서 (위 참조)
```

## 코딩 규칙

- 타입 힌트 필수. 포매터/린터는 ruff (`ruff check` + `ruff format`).
- 에러 응답은 전 엔드포인트 공통 `{code, message, details}` (F-7.1). HTTPException을 직접 흩뿌리지 말고 공통 예외 핸들러를 거친다.
- 빈 결과와 오류를 구분한다: 자료 없음은 `200 + 빈 배열 + reason: no_data`, 수집 실패는 `reason: fetch_failed` (F-5.4). 404로 뭉개지 마라.
- 인증 게이트는 두 곳뿐: `POST /me/stocks`, `POST/DELETE /me/saved-cards` → 미로그인 401. 나머지는 세션 쿠키만 확인하거나 무인증 (F-1.3).
- 업종·부처 매핑, 해외 종목 별칭 같은 대응표는 **코드가 아니라 데이터**(DB 테이블 또는 `data/*.json`)로 관리한다 (F-3.2.1).
- LLM 호출은 `app/ai/` 밖에서 하지 않는다. 프롬프트는 상수/템플릿으로 분리하고, 응답은 structured output 스키마로 강제한 뒤 가드레일을 거친다.
- 외부 API 수집기는 지수 백오프 3회 재시도(F-3.7), 실패 시 예외를 삼키지 말고 수집 상태를 기록한다.

## 협업 컨벤션 (브랜치 · 커밋 · PR)

### 브랜치

```
main     ─ 시연/배포 브랜치. 직접 커밋 금지. develop에서 PR로만 머지. main 머지 = 가비아 재배포
develop  ─ 통합 브랜치. 모든 기능 브랜치의 머지 대상. 항상 실행 가능한 상태 유지
feature/f{대분류번호}-{요약}  ─ 기능 개발.  예: feature/f2-stock-crud, feature/f4-summarize
fix/{요약}      ─ 버그 수정.       예: fix/session-cookie-expire
docs/{요약}     ─ 문서만 변경.     예: docs/spec-c16
chore/{요약}    ─ 설정·빌드·의존성. 예: chore/docker-compose
```

- 브랜치는 항상 **develop에서** 딴다. 기능 하나(F-번호 하나 내외)당 브랜치 하나 — 브랜치 수명은 1~2일을 넘기지 않는다.
- Claude 세션에서 작업을 시작할 때 현재 브랜치를 확인하고, develop이나 main 위에서 직접 기능 코드를 작성하지 않는다.

### 커밋

```
{type}: {요약} ({F-번호})        예: feat: 종목 벌크 등록 API (F-2.5)
```

- type: `feat` `fix` `docs` `refactor` `test` `chore` 중 하나. 요약은 한국어.
- 커밋은 작게 — "테스트가 통과하는 하나의 변경" 단위. 스키마 변경과 기능 구현은 커밋을 분리한다.

### PR (feature → develop)

- 제목은 커밋 컨벤션과 동일. 본문에 ① 구현한 F-번호 ② 확인 방법(실행/테스트 명령) ③ 명세와 다르게 한 것(있다면 근거)을 적는다.
- 머지 전 로컬에서 `pytest`와 `ruff check`를 통과해야 한다.
- 2인 팀 규칙: 상대가 온라인이면 리뷰 요청, 아니면 셀프 머지 후 머지 사실을 공유한다. 단 **스키마(models/schemas)·공통 모듈(db, config, 에러 핸들러) 변경은 반드시 상대 확인 후 머지** — 여기가 두 사람의 충돌 지점이다.

### 코드 네이밍

- 파일·모듈·함수·변수: `snake_case`. 클래스: `PascalCase`. 상수: `UPPER_SNAKE`.
- 라우터 파일명 = 리소스명 (`stocks.py`, `cards.py`). 서비스 함수는 동사로 시작 (`register_stocks`, `build_card_list`).
- collectors 함수는 `fetch_*`(외부 호출만) / `sync_*`(적재까지) 접두사. ai 모듈은 `generate_*` 접두사.
- Pydantic 스키마: `{리소스}{동작}Request` / `{리소스}Response` (예: `StockAddRequest`, `CardListResponse`).
- SQLAlchemy 모델은 단수 PascalCase(`SavedCard`), 테이블명은 명세 부록 1의 snake_case를 그대로 쓴다.

## 테스트·완료 기준 (Definition of Done)

기능 하나가 "끝났다"의 기준:

1. 명세 v2.1 해당 F-항목의 세부 사항을 모두 충족
2. 정상 경로 + 에러 경로(401/400/빈 상태) 동작 확인
3. 서비스 로직에 pytest 테스트 존재 — 외부 API는 respx로 목킹, LLM 호출은 페이크 클라이언트로 대체 (테스트가 실 API 키를 요구하면 안 된다)
4. `ruff check` 통과
5. 실행해서 확인한 결과를 근거와 함께 보고 (테스트가 실패하면 실패했다고 그대로 보고할 것 — 통과한 척 금지)

## 환경 변수 (.env — 커밋 금지, .env.example만 커밋)

```
DATABASE_URL=postgresql+psycopg://assit:assit@localhost:5432/assit
DART_API_KEY=            # 공시
ECOS_API_KEY=            # 한국은행
FRED_API_KEY=            # 미국 Fed
YOUTUBE_API_KEY=         # 유튜브
BRIEFING_API_KEY=        # 정책브리핑(공공데이터포털)
ANTHROPIC_API_KEY=       # LLM
OPENAI_API_KEY=          # 임베딩(RAG)
MOCK_LOGIN_ID=           # 모의 로그인 계정 (F-1.2)
MOCK_LOGIN_PW=
ADMIN_TOKEN=             # /admin/reset-demo 보호 (F-1.5)
```

## 실행

```bash
docker compose up -d db          # Postgres(pgvector)
pip install -r requirements.txt
uvicorn app.main:app --reload    # 개발 서버 (워커 1)
pytest                           # 테스트
```

## Claude 세션 작업 지침

- 작업을 시작하면 먼저 명세 v2.1에서 해당 F-항목을 읽고, **그 항목의 범위로 구현을 한정**한다. PRD 부록 E(범위 밖 목록: 해외 종목, 공시 본문 파싱, 커뮤니티, 히스토리 등)의 기능은 요청받아도 만들지 않고 범위 밖임을 알린다.
- 시니어처럼 구현한다는 것: 요구된 것만, 가장 단순한 구조로, 에러 경로까지. 추측성 추상화·헬퍼·미래 대비 옵션을 추가하지 않는다. 불변식(위 10개)과 충돌하는 요청을 받으면 구현 전에 지적한다.
- 개발 순서는 계획서의 P0~P6을 따른다. 다른 단계의 파일을 건드릴 때는 커밋을 분리한다.
- 응답·문서·커밋 메시지는 한국어로 작성한다.
