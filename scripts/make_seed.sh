#!/usr/bin/env bash
# 백엔드용 — 현재 DB를 프론트 배포용 시드 덤프로 만든다 (이슈 #24).
# 세션·저장카드·이벤트 등 사용자 데이터는 비우고(테이블 구조만),
# 종목·원자료·AI 생성물·RAG 지식베이스만 담는다.
# 사용법: bash scripts/make_seed.sh
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p seed

docker compose exec -T db pg_dump -U assit --clean --if-exists \
  --exclude-table-data=session \
  --exclude-table-data=session_stock \
  --exclude-table-data=saved_card \
  --exclude-table-data=event_log \
  assit | gzip -9 > seed/assit_seed.sql.gz

ls -lh seed/assit_seed.sql.gz
echo "완료 — 프론트는 scripts/restore_seed.sh 로 복원합니다."
