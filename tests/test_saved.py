"""F-7 카드 저장 테스트.

예상 문제 지점(팀 합의 방식)과 대응 테스트:
1. 미로그인 저장/해제 → 401 login_required, 세션 없는 목록 조회 → 401 no_session
2. 없는 card_id / 없는 종목코드 → 404 / 400
3. 중복 저장으로 행 늘어남·스냅샷 갱신 → 멱등(1행 유지, 처음 스냅샷 유지)
4. 유튜브 스냅샷에 30일 정책 위반 데이터(썸네일 등) 포함 → 제목·링크만
5. 원자료·생성물이 갱신되면 저장본도 바뀜 → 스냅샷 불변 (F-7.3)
6. 원자료 정리(purge) 후 저장 카드 소실 → source_item_id NULL로 생존, 스냅샷 렌더
7. 저장됨이 구분별로 쪼개짐 → 국내·해외 통합 + 종목 필터 + 최근 저장 순 (F-7.4·7.5)
8. 해제 후에도 목록에 남음 / 재해제 크래시 → 삭제 + 멱등
9. 카드 목록의 is_saved 미반영 → 저장 후 true, 해제 후 false
"""

from app.collectors.base import ensure_stock_link, upsert_source_item
from app.db import SessionLocal
from app.deps import utcnow
from app.models import MARKET_DOMESTIC, MARKET_OVERSEAS, GeneratedContent, SavedCard


def _login(client):
    client.post("/session")
    client.post("/auth/mock-login", json={"id": "demo", "password": "pw1234"})


def _seed_items(db):
    """저장 대상 카드 2종 — 공시(요약·정형 배지 포함) + 유튜브."""
    dis = upsert_source_item(
        db,
        tab="disclosure",
        market=MARKET_DOMESTIC,
        source_key="save-d1",
        title="주요사항보고서(자기주식취득결정)",
        doc_type="자기주식취득결정",
        published_at=utcnow(),
        origin_url="https://dart.fss.or.kr/save-d1",
        detail_json={"slots": [{"label": "취득 예정 금액", "value": "1,000원"}]},
    )
    ensure_stock_link(db, dis.id, "111110")
    if db.query(GeneratedContent).filter_by(source_item_id=dis.id).one_or_none() is None:
        db.add(
            GeneratedContent(
                source_item_id=dis.id,
                scope="stock",
                scope_key="111110",
                summary_short="자기주식을 사기로 했어요.",
                summary_full="회사가 자기주식 취득을 결정했어요.",
                label="positive",
                label_reason="주주 환원 신호예요.",
            )
        )
    yt = upsert_source_item(
        db,
        tab="youtube",
        market=MARKET_OVERSEAS,
        source_key="save-y1",
        title="테슬라 분석 영상",
        published_at=utcnow(),
        origin_url="https://youtube.com/watch?v=save-y1",
        thumbnail_url="https://i.ytimg.com/save-y1.jpg",
        channel_name="채널",
        view_count=10,
    )
    ensure_stock_link(db, yt.id, "TSLA")
    db.commit()
    return dis.id, yt.id


def test_auth_gates(client):
    client.post("/session")  # 세션은 있지만 비로그인
    r = client.post("/me/saved-cards", json={"card_id": 1, "stock_code": "111110"})
    assert (r.status_code, r.json()["code"]) == (401, "login_required")
    r = client.delete("/me/saved-cards/1")
    assert (r.status_code, r.json()["code"]) == (401, "login_required")

    fresh = client.__class__(client.app)  # 세션 자체가 없는 클라이언트
    r = fresh.get("/me/saved-cards")
    assert (r.status_code, r.json()["code"]) == (401, "no_session")


def test_save_validations(client, login_env):
    _login(client)
    r = client.post("/me/saved-cards", json={"card_id": 99_999_999, "stock_code": "111110"})
    assert (r.status_code, r.json()["code"]) == (404, "unknown_card")
    with SessionLocal() as db:
        dis_id, _ = _seed_items(db)
    r = client.post("/me/saved-cards", json={"card_id": dis_id, "stock_code": "000000"})
    assert (r.status_code, r.json()["code"]) == (400, "unknown_stock")


def test_save_snapshot_idempotent_and_immutable(client, login_env):
    _login(client)
    with SessionLocal() as db:
        dis_id, yt_id = _seed_items(db)

    r = client.post("/me/saved-cards", json={"card_id": dis_id, "stock_code": "111110"})
    assert r.status_code == 200 and r.json()["already_saved"] is False
    snap = r.json()["item"]["snapshot"]
    assert snap["title"] == "주요사항보고서(자기주식취득결정)"
    assert snap["summary_short"] and snap["label"] == "positive"
    assert snap["details"] == [{"label": "취득 예정 금액", "value": "1,000원"}]

    # 중복 저장 — 멱등 (F-7.1)
    r2 = client.post("/me/saved-cards", json={"card_id": dis_id, "stock_code": "111110"})
    assert r2.json()["already_saved"] is True
    with SessionLocal() as db:
        assert db.query(SavedCard).filter_by(source_item_id=dis_id).count() == 1

    # 유튜브 스냅샷 — 제목·링크만 (확정사항 4절, YouTube 30일 정책)
    r3 = client.post("/me/saved-cards", json={"card_id": yt_id, "stock_code": "TSLA"})
    yt_snap = r3.json()["item"]["snapshot"]
    assert set(yt_snap) == {"title", "origin_url", "source_name"}
    assert "thumbnail" not in str(yt_snap)

    # 원자료·생성물 갱신 → 저장본 불변 (F-7.3)
    with SessionLocal() as db:
        gen = db.query(GeneratedContent).filter_by(scope_key="111110").first()
        gen.summary_short = "완전히 새로 생성된 요약"
        db.commit()
    listed = client.get("/me/saved-cards").json()["items"]
    saved_snap = next(i for i in listed if i["card_id"] == dis_id)["snapshot"]
    assert saved_snap["summary_short"] == "자기주식을 사기로 했어요."


def test_list_unified_filtered_and_badges(client, login_env):
    _login(client)
    with SessionLocal() as db:
        dis_id, yt_id = _seed_items(db)
    client.post("/me/saved-cards", json={"card_id": dis_id, "stock_code": "111110"})
    client.post("/me/saved-cards", json={"card_id": yt_id, "stock_code": "TSLA"})

    items = client.get("/me/saved-cards").json()["items"]
    # 국내·해외 통합 + 최근 저장 순 (F-7.4·7.5)
    assert [i["card_id"] for i in items[:2]] == [yt_id, dis_id]
    badge = items[1]
    assert badge["tab"] == "disclosure" and badge["stock_code"] == "111110"
    assert badge["stock_name"] == "가나전자"

    # 종목 필터
    only_tsla = client.get("/me/saved-cards", params={"stock_code": "TSLA"}).json()["items"]
    assert [i["card_id"] for i in only_tsla] == [yt_id]

    # 카드 목록 is_saved 반영 (F-6)
    dis_cards = client.get("/cards", params={"tab": "disclosure", "stock_code": "111110"}).json()
    assert next(c for c in dis_cards["items"] if c["card_id"] == dis_id)["is_saved"] is True


def test_unsave_and_purge_survival(client, login_env):
    _login(client)
    with SessionLocal() as db:
        dis_id, yt_id = _seed_items(db)
    client.post("/me/saved-cards", json={"card_id": dis_id, "stock_code": "111110"})
    client.post("/me/saved-cards", json={"card_id": yt_id, "stock_code": "TSLA"})

    # 해제 + 재해제 멱등 (F-7.2)
    assert client.delete(f"/me/saved-cards/{dis_id}").json()["removed"] is True
    assert client.delete(f"/me/saved-cards/{dis_id}").json()["removed"] is False
    ids = [i["card_id"] for i in client.get("/me/saved-cards").json()["items"]]
    assert dis_id not in ids
    # is_saved도 해제 반영
    dis_cards = client.get("/cards", params={"tab": "disclosure", "stock_code": "111110"}).json()
    assert next(c for c in dis_cards["items"] if c["card_id"] == dis_id)["is_saved"] is False

    # 원자료 삭제(purge와 동일 경로) → 저장 카드는 스냅샷으로 생존 (F-7.3)
    with SessionLocal() as db:
        from app.models import SourceItem

        db.delete(db.query(SourceItem).filter_by(id=yt_id).one())
        db.commit()
    survivors = client.get("/me/saved-cards").json()["items"]
    orphan = next(i for i in survivors if i["tab"] == "youtube" and i["stock_code"] == "TSLA")
    assert orphan["card_id"] is None  # SET NULL
    assert orphan["snapshot"]["title"] == "테슬라 분석 영상"
