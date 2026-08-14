"""F-8.2 지표 이벤트 테스트.

예상 문제 지점(팀 합의 방식)과 대응 테스트:
1. 정의 밖 이벤트명 적재 → 400 unknown_event (오타가 지표를 오염시키지 않게)
2. payload로 금액 등 임의 데이터 수집 (F-8.6) → 허용 키 외 저장 안 됨
3. 세션 없는 홈 도달 이벤트 거부 → session_id null로 적재 허용
4. 지표 산출이 이벤트와 안 맞음 → 퍼널 집계 검증
5. 검수 잠금 스크립트가 대상을 빠뜨리거나 남의 것을 잠금 → 범위 검증
"""

from app.db import SessionLocal
from app.models import EventLog, GeneratedContent
from scripts.lock_reviewed import set_locked
from scripts.report_metrics import build_report


def _login(client):
    client.post("/session")
    client.post("/auth/mock-login", json={"id": "demo", "password": "pw1234"})


def test_event_names_validated(client):
    client.post("/session")
    r = client.post("/events", json={"event_name": "made_up_event"})
    assert (r.status_code, r.json()["code"]) == (400, "unknown_event")
    r = client.post("/events", json={"event_name": "home_view"})
    assert r.status_code == 200 and r.json()["accepted"] is True


def test_payload_whitelist_blocks_forbidden_keys(client):
    client.post("/session")
    r = client.post(
        "/events",
        json={
            "event_name": "stock_add",
            "payload": {"stock_code": "005930", "avg_price": 70000, "quantity": 10},
        },
    )
    assert r.status_code == 200
    with SessionLocal() as db:
        row = (
            db.query(EventLog)
            .filter(EventLog.event_name == "stock_add")
            .order_by(EventLog.id.desc())
            .first()
        )
        assert row.payload_json == {"stock_code": "005930"}  # 평단가·수량은 저장 안 됨 (F-8.6)


def test_event_without_session_allowed(client):
    fresh = client.__class__(client.app)  # 세션 쿠키 없음 — 홈 도달은 세션 발급 전일 수 있다
    r = fresh.post("/events", json={"event_name": "home_view"})
    assert r.status_code == 200
    with SessionLocal() as db:
        row = db.query(EventLog).order_by(EventLog.id.desc()).first()
        assert row.session_id is None


def test_metrics_funnel(client):
    _login(client)
    for name in ("home_view", "stock_add", "sheet_open", "sheet_open", "origin_click"):
        client.post("/events", json={"event_name": name})
    with SessionLocal() as db:
        report = build_report(db)
    assert report["홈 도달 세션"] >= 1
    assert report["종목 추가 전환"].startswith("1/") or "1" in report["종목 추가 전환"]
    assert report["요약 시트 열람 수"] >= 2
    assert report["시트 대비 원문 이동률"].split("/")[0] >= "1"


def test_lock_scope(client):
    with SessionLocal() as db:
        from app.collectors.base import ensure_stock_link, upsert_source_item

        mine = upsert_source_item(
            db, tab="disclosure", market="domestic", source_key="lock-1", title="내 공시"
        )
        ensure_stock_link(db, mine.id, "111110")
        other = upsert_source_item(
            db, tab="disclosure", market="domestic", source_key="lock-2", title="남의 공시"
        )
        ensure_stock_link(db, other.id, "222220")
        db.add_all(
            [
                GeneratedContent(
                    source_item_id=mine.id, scope="stock", scope_key="111110", summary_short="요약"
                ),
                GeneratedContent(
                    source_item_id=other.id, scope="stock", scope_key="222220", summary_short="요약"
                ),
            ]
        )
        db.commit()

        stats = set_locked(db, ["111110"], locked=True)
        assert stats["stocks"] == 1 and stats["stock_rows"] >= 1

        locked_keys = {
            g.scope_key for g in db.query(GeneratedContent).filter(GeneratedContent.locked)
        }
        assert "111110" in locked_keys
        assert "222220" not in locked_keys  # 검수 안 한 종목은 잠기지 않는다

        set_locked(db, ["111110"], locked=False)  # 원복 — 다른 테스트 영향 방지
