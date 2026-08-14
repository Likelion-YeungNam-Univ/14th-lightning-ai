#!/usr/bin/env bash
# 프론트 로컬 연동용 — 시드 덤프를 Docker DB에 복원한다 (이슈 #24).
# 사용법:  bash scripts/restore_seed.sh            # seed/assit_seed.sql.gz 복원
# 재실행해도 안전(덤프가 기존 테이블을 지우고 다시 만든다).
set -euo pipefail
cd "$(dirname "$0")/.."

SEED_FILE="${1:-seed/assit_seed.sql.gz}"
if [ ! -f "$SEED_FILE" ]; then
  echo "시드 파일이 없습니다: $SEED_FILE" >&2
  exit 1
fi

echo "1/3 DB 컨테이너 기동 대기..."
docker compose up -d db
until docker compose exec -T db pg_isready -U assit -q; do sleep 1; done

echo "2/3 시드 복원 중 ($SEED_FILE)..."
gunzip -c "$SEED_FILE" | docker compose exec -T db psql -q -U assit -d assit -v ON_ERROR_STOP=1 >/dev/null

echo "3/3 앱 기동..."
docker compose up -d app

echo "완료 — http://localhost:8000/docs 에서 확인하세요."
echo "  예: GET /cards?tab=disclosure&stock_code=000660"
