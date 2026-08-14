FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8000

# 워커 1개 고정(불변식 3 — APScheduler 중복 방지). proxy-headers: Vercel rewrite 뒤에서
# 실제 클라이언트 IP를 레이트리밋(F-8.3)·로그가 보도록 X-Forwarded-* 헤더를 신뢰한다
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--proxy-headers"]
