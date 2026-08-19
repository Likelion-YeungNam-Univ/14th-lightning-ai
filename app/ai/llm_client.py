"""OpenAI 호출 래퍼 — LLM 호출은 app/ai/ 밖에서 하지 않는다 (CLAUDE.md).

structured output(JSON 스키마 강제)만 사용한다. 테스트는 같은 인터페이스의
페이크 클라이언트를 주입한다(실 키 불요, DoD 3).
"""

import json
import logging

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

API_URL = "https://api.openai.com/v1/chat/completions"
RESPONSES_URL = "https://api.openai.com/v1/responses"  # 웹 검색 도구는 Responses API 전용 (E-2.1a)
EMBEDDINGS_URL = "https://api.openai.com/v1/embeddings"
EMBEDDING_MODEL = "text-embedding-3-small"  # 확정사항 1절 — RAG 임베딩


class LLMError(Exception):
    """LLM 호출 실패 — 생성 단위별로 격리해 처리한다."""


class OpenAIClient:
    def __init__(self, api_key: str, model: str, timeout: float = 120.0) -> None:
        # 실측(2026-08-16): 긴 규제 본문 요약은 60초를 넘기는 건이 있어 120초로 상향
        self._api_key = api_key
        self.model = model
        self.timeout = timeout

    def generate_json(
        self,
        *,
        system: str,
        user: str,
        schema: dict,
        name: str = "output",
        reasoning_effort: str | None = None,
    ) -> dict:
        """스키마 강제 생성. gpt-5 계열은 temperature 미지원 — 보내지 않는다.

        reasoning_effort: 지정 시에만 본문에 실린다. 사용자 요청 경로(용어 풀이, F-5.4)는
        "minimal" — 실측 평균 6.8s → 2.3s(#69). 배치 요약은 품질 우선이라 기본값(None).
        """
        body = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": name, "strict": True, "schema": schema},
            },
        }
        if reasoning_effort:
            body["reasoning_effort"] = reasoning_effort
        try:
            resp = httpx.post(
                API_URL,
                json=body,
                headers={"Authorization": f"Bearer {self._api_key}"},
                timeout=self.timeout,
            )
        except httpx.HTTPError as e:
            raise LLMError(f"OpenAI 요청 실패: {e}") from e
        if resp.status_code >= 400:
            raise LLMError(f"OpenAI HTTP {resp.status_code}: {resp.text[:200]}")
        try:
            return json.loads(resp.json()["choices"][0]["message"]["content"])
        except (KeyError, IndexError, json.JSONDecodeError) as e:
            raise LLMError(f"OpenAI 응답 파싱 실패: {e}") from e

    def generate_with_search(
        self,
        *,
        system: str,
        user: str,
        schema: dict,
        name: str = "output",
        max_tool_calls: int = 5,
    ) -> tuple[dict, int]:
        """경제 상식 카드 전용(E-2.1a) — 웹 검색 도구 + 스키마 강제, Responses API.

        별도 검색 API를 계약하지 않고 OpenAI 웹 검색 도구를 그대로 쓴다. `max_tool_calls`는
        요청 자체에 상한을 걸어 과금을 원천 차단한다 — 응답을 받은 뒤 검색 횟수를 세어
        폐기하는 것만으로는 이미 발생한 호출 비용을 막지 못한다(승래 리뷰 반영, E-2.1a-2).
        반환값의 두 번째 원소는 실제로 실행된 검색 횟수(web_search_call 개수).
        """
        body = {
            "model": self.model,
            "input": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "tools": [{"type": "web_search"}],
            "max_tool_calls": max_tool_calls,
            "text": {
                "format": {"type": "json_schema", "name": name, "strict": True, "schema": schema}
            },
        }
        try:
            resp = httpx.post(
                RESPONSES_URL,
                json=body,
                headers={"Authorization": f"Bearer {self._api_key}"},
                timeout=self.timeout,
            )
        except httpx.HTTPError as e:
            raise LLMError(f"OpenAI 요청 실패: {e}") from e
        if resp.status_code >= 400:
            raise LLMError(f"OpenAI HTTP {resp.status_code}: {resp.text[:200]}")
        try:
            data = resp.json()
        except json.JSONDecodeError as e:
            raise LLMError(f"OpenAI 응답이 JSON이 아님: {e}") from e
        output = data.get("output", [])
        search_count = sum(1 for item in output if item.get("type") == "web_search_call")
        message = next((item for item in output if item.get("type") == "message"), None)
        if message is None:
            raise LLMError("OpenAI 응답에 message 없음(검색만 하다 끝났을 수 있음)")
        try:
            text = next(
                c["text"] for c in message["content"] if c.get("type") == "output_text"
            )
            return json.loads(text), search_count
        except (KeyError, StopIteration, json.JSONDecodeError) as e:
            raise LLMError(f"OpenAI 응답 파싱 실패: {e}") from e

    def embed(self, texts: list[str]) -> list[list[float]]:
        """text-embedding-3-small 임베딩 — RAG 적재·검색 공용 (확정사항 5절)."""
        try:
            resp = httpx.post(
                EMBEDDINGS_URL,
                json={"model": EMBEDDING_MODEL, "input": texts},
                headers={"Authorization": f"Bearer {self._api_key}"},
                timeout=self.timeout,
            )
        except httpx.HTTPError as e:
            raise LLMError(f"임베딩 요청 실패: {e}") from e
        if resp.status_code >= 400:
            raise LLMError(f"임베딩 HTTP {resp.status_code}: {resp.text[:200]}")
        data = sorted(resp.json()["data"], key=lambda d: d["index"])
        return [d["embedding"] for d in data]


def get_llm_client() -> OpenAIClient:
    if not settings.openai_api_key:
        raise LLMError("OPENAI_API_KEY 미설정")
    return OpenAIClient(settings.openai_api_key, settings.openai_model)
