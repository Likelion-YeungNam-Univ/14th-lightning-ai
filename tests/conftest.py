"""테스트는 실 Postgres·외부 API·실 API 키 없이 돌아야 한다 (CLAUDE.md DoD 3).

app 모듈 import 전에 DATABASE_URL을 sqlite로 바꾼다 — conftest가 테스트 모듈보다 먼저 로드되는
pytest 동작에 의존하므로, 이 파일 상단의 os.environ 설정을 다른 위치로 옮기지 말 것.
"""

import os
import pathlib

_TEST_DB = pathlib.Path(__file__).parent / ".test_assit.db"
os.environ["DATABASE_URL"] = f"sqlite:///{_TEST_DB}"
# 실 키가 .env에 있어도 테스트가 실 API를 때리면 안 된다 — 가짜 키로 덮는다 (respx가 차단·목킹)
for _key in (
    "DART_API_KEY",
    "ECOS_API_KEY",
    "FRED_API_KEY",
    "YOUTUBE_API_KEY",
    "BRIEFING_API_KEY",
    "OPENAI_API_KEY",
):
    os.environ[_key] = "test-key"
os.environ["SEC_USER_AGENT"] = "assit-test test@example.com"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.config import settings  # noqa: E402
from app.main import app  # noqa: E402

# 국내: 시총 1위가 우선주(끝자리 5) — 기본 종목에서 빠져야 한다 (확정사항 4절)
DOMESTIC_SEED = [
    ("111115", "가나전자우", 900),
    ("111110", "가나전자", 800),
    ("222220", "다라화학", 700),
    ("333330", "마바은행", 600),
    ("444440", "사아건설", 500),
    ("555550", "자차식품", 400),
]
OVERSEAS_SEED = [
    ("TSLA", "테슬라", ["tesla", "테슬라"]),
    ("AAPL", "애플", ["apple", "애플"]),
    ("NVDA", "엔비디아", ["nvidia", "엔비디아"]),
    ("GOOGL", "알파벳(구글)", ["google", "구글", "알파벳"]),
    ("MSFT", "마이크로소프트", ["microsoft", "마이크로소프트"]),
]
DEFAULT_CODES = ["111110", "222220", "333330", "444440"]  # 시총 상위 4 보통주
OVERSEAS_DEFAULT_CODES = ["NVDA", "AAPL", "GOOGL", "MSFT"]  # 해외 고정 기본 종목


def _seed_stocks() -> None:
    from app.db import SessionLocal
    from app.models import MARKET_DOMESTIC, MARKET_OVERSEAS, StockMaster

    with SessionLocal() as db:
        for code, name, cap in DOMESTIC_SEED:
            if db.get(StockMaster, code) is None:
                db.add(
                    StockMaster(
                        stock_code=code,
                        market=MARKET_DOMESTIC,
                        name=name,
                        aliases=[],
                        exchange="KOSPI",
                        industry_code="etc",
                        market_cap=cap * 10**12,
                    )
                )
        for code, name, aliases in OVERSEAS_SEED:
            if db.get(StockMaster, code) is None:
                db.add(
                    StockMaster(
                        stock_code=code,
                        market=MARKET_OVERSEAS,
                        name=name,
                        aliases=aliases,
                        exchange="NASDAQ",
                        sic_code="3711",
                    )
                )
        db.commit()


@pytest.fixture()
def client():
    with TestClient(app) as c:  # lifespan → init_db(create_all)
        _seed_stocks()
        yield c


@pytest.fixture()
def login_env(monkeypatch):
    monkeypatch.setattr(settings, "mock_login_id", "demo")
    monkeypatch.setattr(settings, "mock_login_pw", "pw1234")
    monkeypatch.setattr(settings, "admin_token", "admin-secret")


@pytest.fixture(scope="session", autouse=True)
def _cleanup_test_db():
    yield
    _TEST_DB.unlink(missing_ok=True)
