FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8000

# 워커 1개 고정(불변식 3 — APScheduler 중복 방지). proxy-headers: 리버스 프록시(Vercel
# rewrite 또는 Nginx) 뒤에서 실제 클라이언트 IP를 레이트리밋(F-8.3)·로그가 보도록
# X-Forwarded-* 헤더를 신뢰한다. ROOT_PATH는 `/api` 서브패스 뒤에 물릴 때만 .env로
# 설정한다(미설정 시 빈 문자열 — 로컬 docker-compose는 그대로 루트에서 서비스).
# 셸 폼(JSON 배열 아님)이라 컨테이너 시작 시 환경변수가 치환된다.
CMD uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 1 --proxy-headers --root-path "${ROOT_PATH:-}"
