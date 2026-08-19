"""F-5.4 SSE 스트리밍 테스트 — GET /terms/explain/stream (#71). LLM은 페이크(실 키 불요).

예상 문제 지점(팀 합의 방식)과 대응 테스트:
1. 이벤트 순서·포맷이 계약(meta → delta* → done)과 다름 → 와이어 파싱으로 순서 단언
2. 스트림이 끝나도 캐시가 안 남아 다음 드래그가 또 LLM을 부름
   → 2번째 요청 meta.cached=true, LLM 호출 0
3. 가드레일 위반 문장이 화면에 그대로 흘러나감 → 위반 조각은 delta로 안 나가고 replace로 교체
4. 재생성도 위반 → replace.explanation null (F-5.5와 동일)
5. 검증·레이트리밋이 스트림 안에서 터져 JSON 에러 규격이 깨짐 → 스트림 열기 전 400/429 JSON
6. OpenAI 장애가 500으로 번짐 → event: error + 캐시 미저장
7. 동기 응답과 입력이 어긋남(근거·추론 설정) → 같은 프롬프트 근거 주입, reasoning minimal
"""

import json

from app.db import SessionLocal
from app.models import TermCache
from app.services import ratelimit
from tests.test_ai import FakeLLM
from tests.test_terms_rag import E_BASE, FakeRagLLM, _seed_chunks


class FakeStreamLLM(FakeRagLLM):
    """스트림 조각 목록을 순서대로 내는 페이크. 조각이 Exception이면 그 시점에 raise."""

    def __init__(self, outputs=(), chunks=(), embeddings=None):
        super().__init__(outputs, embeddings)
        self.chunks = list(chunks)
        self.stream_calls: list[str] = []
        self.stream_efforts: list[str | None] = []

    def stream_text(self, *, system, user, reasoning_effort=None):
        self.stream_calls.append(user)
        self.stream_efforts.append(reasoning_effort)
        for piece in self.chunks:
            if isinstance(piece, Exception):
                raise piece
            yield piece


def _parse_sse(text: str) -> list[tuple[str, dict]]:
    """와이어 포맷(event:/data: 줄 + 빈 줄)을 (event, data)로 — 프론트 EventSource가 하는 일."""
    events = []
    for block in text.strip().split("\n\n"):
        event, data = None, None
        for line in block.splitlines():
            if line.startswith("event:"):
                event = line[6:].strip()
            elif line.startswith("data:"):
                data = json.loads(line[5:].strip())
        events.append((event, data))
    return events


def _stream(client, term, tab="bok", context=None):
    params = {"term": term, "tab": tab}
    if context:
        params["context"] = context
    with client.stream("GET", "/terms/explain/stream", params=params) as r:
        return r.status_code, r.headers, "".join(r.iter_text())


def _fresh_db():
    with SessionLocal() as db:
        _seed_chunks(db)
        db.query(TermCache).delete()
        db.commit()


def test_stream_events_then_cache(client, monkeypatch):
    ratelimit.reset()
    _fresh_db()
    fake = FakeStreamLLM(
        chunks=["기준금리는 ", "중앙은행이 정하는 ", "정책금리예요."],
        embeddings={"기준금리": E_BASE},
    )
    monkeypatch.setattr("app.routers.terms.get_llm_client", lambda: fake)

    status, headers, body = _stream(client, "기준금리", context="기준금리 인상")
    assert status == 200
    assert headers["content-type"].startswith("text/event-stream")
    assert headers["x-accel-buffering"] == "no"  # nginx 버퍼링 해제 힌트
    events = _parse_sse(body)
    assert [e for e, _ in events] == ["meta", "delta", "delta", "delta", "done"]
    meta = events[0][1]
    assert meta["cached"] is False and meta["sources"][0]["term"] == "기준금리"
    assert (
        "".join(d["text"] for e, d in events if e == "delta")
        == "기준금리는 중앙은행이 정하는 정책금리예요."
    )
    assert events[-1][1]["explanation"] == "기준금리는 중앙은행이 정하는 정책금리예요."
    # 동기 경로와 같은 입력: 근거 주입 + 추론 최소화 (#69)
    assert "중앙은행이 결정하는 정책금리" in fake.stream_calls[0]
    assert fake.stream_efforts == ["minimal"]

    # 캐시 히트 — meta(cached) + done 두 이벤트, LLM 미호출
    status2, _, body2 = _stream(client, "기준금리")
    events2 = _parse_sse(body2)
    assert status2 == 200 and [e for e, _ in events2] == ["meta", "done"]
    assert events2[0][1]["cached"] is True
    assert events2[1][1]["explanation"] == "기준금리는 중앙은행이 정하는 정책금리예요."
    assert len(fake.stream_calls) == 1

    # 동기 POST도 같은 캐시를 본다 — 두 경로가 한 캐시를 공유
    r = client.post("/terms/explain", json={"term": "기준금리", "tab": "bok"})
    assert r.json()["cached"] is True


def test_stream_guardrail_replaces(client, monkeypatch):
    ratelimit.reset()
    _fresh_db()
    # 3번째 조각에서 "매수 기회" 완성 → 위반. 재생성(generate_json)은 정상 문장
    fake = FakeStreamLLM(
        outputs=[{"explanation": "유상증자는 새 주식을 발행해 자금을 모으는 거예요."}],
        chunks=["유상증자는 ", "지금이 ", "매수 기회", "예요."],
        embeddings={"유상증자": E_BASE},
    )
    monkeypatch.setattr("app.routers.terms.get_llm_client", lambda: fake)

    _, _, body = _stream(client, "유상증자", tab="disclosure")
    events = _parse_sse(body)
    assert [e for e, _ in events] == ["meta", "delta", "delta", "replace"]
    assert [d["text"] for e, d in events if e == "delta"] == [
        "유상증자는 ",
        "지금이 ",
    ]  # 위반 조각 미송출
    assert events[-1][1]["explanation"] == "유상증자는 새 주식을 발행해 자금을 모으는 거예요."
    assert len(fake.calls) == 1 and "재생성 요청" in fake.calls[0]  # 재생성 1회
    with SessionLocal() as db:  # 캐시엔 교체본만
        assert db.get(TermCache, ("유상증자", "disclosure")).explanation.startswith(
            "유상증자는 새 주식"
        )


def test_stream_guardrail_double_failure_not_cached(client, monkeypatch):
    ratelimit.reset()
    _fresh_db()
    fake = FakeStreamLLM(
        outputs=[{"explanation": "주가가 상승할 전망입니다."}],  # 재생성도 위반
        chunks=["지금이 매수 기회입니다."],
        embeddings={"감자": E_BASE},
    )
    monkeypatch.setattr("app.routers.terms.get_llm_client", lambda: fake)

    _, _, body = _stream(client, "감자", tab="disclosure")
    events = _parse_sse(body)
    assert [e for e, _ in events] == ["meta", "replace"]
    assert events[-1][1]["explanation"] is None
    with SessionLocal() as db:
        assert db.get(TermCache, ("감자", "disclosure")) is None  # 실패는 캐시하지 않는다


def test_stream_validation_and_rate_limit_are_plain_json(client, monkeypatch):
    ratelimit.reset()
    _fresh_db()
    fake = FakeStreamLLM(chunks=["설명이에요."], embeddings={})
    monkeypatch.setattr("app.routers.terms.get_llm_client", lambda: fake)

    r = client.get("/terms/explain/stream", params={"term": "   ", "tab": "bok"})
    assert (r.status_code, r.json()["code"]) == (400, "invalid_term")
    r = client.get("/terms/explain/stream", params={"term": "기준금리", "tab": "없는탭"})
    assert (r.status_code, r.json()["code"]) == (400, "invalid_tab")

    monkeypatch.setattr(ratelimit, "IP_LIMIT_PER_MINUTE", 1)
    assert _stream(client, "스트림용어1")[0] == 200
    r = client.get("/terms/explain/stream", params={"term": "스트림용어2", "tab": "bok"})
    assert (r.status_code, r.json()["code"]) == (429, "rate_limited")  # 스트림 열기 전 JSON 에러


def test_stream_llm_failure_emits_error_event(client, monkeypatch):
    from app.ai.llm_client import LLMError

    ratelimit.reset()
    _fresh_db()
    fake = FakeStreamLLM(
        chunks=["듀레이션은 ", LLMError("연결 끊김")], embeddings={"듀레이션": E_BASE}
    )
    monkeypatch.setattr("app.routers.terms.get_llm_client", lambda: fake)

    status, _, body = _stream(client, "듀레이션")
    events = _parse_sse(body)
    assert status == 200  # 헤더는 이미 나갔으므로 상태코드가 아니라 이벤트로 알린다
    assert [e for e, _ in events] == ["meta", "delta", "error"]
    assert events[-1][1]["code"] == "llm_unavailable"
    with SessionLocal() as db:
        assert db.get(TermCache, ("듀레이션", "bok")) is None


def test_stream_fake_matches_real_client_interface():
    """페이크가 실제 OpenAIClient의 메서드 이름을 그대로 따르는지 — 인터페이스 드리프트 방지."""
    from app.ai.llm_client import OpenAIClient

    for name in ("generate_json", "embed", "stream_text"):
        assert hasattr(OpenAIClient, name) and hasattr(FakeStreamLLM, name)
    assert issubclass(FakeStreamLLM, FakeLLM)
