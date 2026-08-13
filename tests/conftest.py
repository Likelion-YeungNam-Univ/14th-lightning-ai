"""테스트는 실 Postgres·외부 API·실 API 키 없이 돌아야 한다 (CLAUDE.md DoD 3).

app 모듈 import 전에 DATABASE_URL을 sqlite로 바꾼다 — conftest가 테스트 모듈보다 먼저 로드되는
pytest 동작에 의존하므로, 이 파일 상단의 os.environ 설정을 다른 위치로 옮기지 말 것.
"""

import os
import pathlib

_TEST_DB = pathlib.Path(__file__).parent / ".test_assit.db"
os.environ["DATABASE_URL"] = f"sqlite:///{_TEST_DB}"

import pytest  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def _cleanup_test_db():
    yield
    _TEST_DB.unlink(missing_ok=True)
