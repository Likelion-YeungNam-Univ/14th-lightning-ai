from fastapi.testclient import TestClient

from app.main import app


def test_health_and_error_format():
    # TestClient 컨텍스트 진입 시 lifespan(init_db) 실행 — 모델 13개 create_all 스모크 겸용
    with TestClient(app) as client:
        r = client.get("/health")
        assert r.status_code == 200
        assert r.json() == {"status": "ok", "db": "ok"}

        # F-7.1 — 에러도 {code, message, details} 규격
        r = client.get("/no-such-path")
        assert r.status_code == 404
        body = r.json()
        assert set(body.keys()) == {"code", "message", "details"}
