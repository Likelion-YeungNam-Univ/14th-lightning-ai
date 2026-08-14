# API 키 발급 가이드 (5종 + SEC 이메일)

전부 **무료·개인 계정**으로 발급 가능. 발급 후 `.env`에 채우면 끝 (`cp .env.example .env` 먼저).
팀 공용 이메일 하나로 가입하면 키 관리가 편하다 — 특히 SEC User-Agent와 재발급 대비.

## 1. DART (전자공시) → `DART_API_KEY`

1. https://opendart.fss.or.kr 접속 → 우측 상단 **[인증키 신청/관리]**
2. 회원가입(이메일 인증) → 로그인 → **[인증키 신청]** — 사용 목적 간단히 기재
3. **즉시 발급** → [인증키 관리]에서 40자리 키 복사
- 한도: 일 20,000건 (충분)

## 2. ECOS (한국은행) → `ECOS_API_KEY`

1. https://ecos.bok.or.kr/api 접속 → **[인증키 신청]**
2. 회원가입 → 신청서에 이용 목적 기재 (예: "해커톤 서비스 개발 — 기준금리 조회")
3. **즉시 발급** — 마이페이지에서 키 확인
- 우리가 쓰는 통계표: `722Y001` (한국은행 기준금리)

## 3. FRED (미국 연준 데이터) → `FRED_API_KEY`

1. https://fred.stlouisfed.org → 우측 상단 **My Account** → 가입 (이메일만)
2. 로그인 → My Account → **API Keys** → [Request API Key] — 용도 한 줄 입력
3. **즉시 발급** (32자리 키)
- 우리가 쓰는 시리즈: `DFEDTARU` (연방기금 목표금리 상단)

## 4. YouTube Data API v3 → `YOUTUBE_API_KEY`

1. https://console.cloud.google.com → 구글 계정 로그인 → 상단 프로젝트 선택 → **[새 프로젝트]** (이름: assit 등)
2. 좌측 메뉴 **[API 및 서비스] → [라이브러리]** → "YouTube Data API v3" 검색 → **[사용 설정]**
3. **[API 및 서비스] → [사용자 인증 정보] → [사용자 인증 정보 만들기] → [API 키]** → 즉시 발급
4. (권장) 키 클릭 → **API 제한사항 → YouTube Data API v3만 허용** — 유출 시 피해 최소화
- **신용카드 불필요.** 기본 쿼터 일 10,000유닛(검색 100회) — 우리 설계 전제와 동일

## 5. 정책브리핑 정책뉴스 (공공데이터포털) → `BRIEFING_API_KEY`

1. https://www.data.go.kr → 회원가입 → 로그인
2. 검색창에 **"정책브리핑 정책뉴스"** 검색 (문화체육관광부, 등록번호 15095335) → 상세 페이지
3. **[활용신청]** — 활용 목적 "앱 개발(웹/앱 서비스)" 선택, 내용 간단 기재 → **자동승인**
4. **마이페이지 → 데이터 활용 → Open API → 인증키** 에서 **일반 인증키(Decoding)** 복사
- 신청 직후 키 활성화까지 **최대 1시간** 걸릴 수 있음 (호출 시 등록되지 않은 키 오류가 나면 잠시 후 재시도)
- Encoding/Decoding 키 두 개가 보이면 **Decoding 키**를 `.env`에 넣는다 (코드에서 URL 인코딩 처리)

## 6. SEC User-Agent (키 아님) → `SEC_USER_AGENT`

발급 절차 없음. 팀 대표 이메일만 정해서 `.env`에:
```
SEC_USER_AGENT=assit-hackathon your-team@example.com
```
SEC 공정 접근 정책상 자동화 요청에 연락처를 요구하며, 없으면 403 차단된다.

---

## 발급 후 확인

```bash
cp .env.example .env   # 이미 했다면 생략, 키 값 채우기
docker compose up -d db
.venv/bin/uvicorn app.main:app --reload   # 서버 기동 확인
```
수집기(#4)가 머지되면 각 키의 실제 호출 검증 스크립트가 추가된다.
```
주의: .env는 절대 커밋하지 않는다(.gitignore에 있음). 키를 카톡 등으로 공유할 때도 저장소 이슈·PR에는 붙여넣지 말 것.
```
