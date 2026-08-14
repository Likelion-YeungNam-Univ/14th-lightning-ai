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
EMBEDDINGS_URL = "https://api.openai.com/v1/embeddings"
EMBEDDING_MODEL = "text-embedding-3-small"  # 확정사항 1절 — RAG 임베딩


class LLMError(Exception):
    """LLM 호출 실패 — 생성 단위별로 격리해 처리한다."""


class OpenAIClient:
    def __init__(self, api_key: str, model: str, timeout: float = 60.0) -> None:
        self._api_key = api_key
        self.model = model
        self.timeout = timeout

    def generate_json(self, *, system: str, user: str, schema: dict, name: str = "output") -> dict:
        """스키마 강제 생성. gpt-5 계열은 temperature 미지원 — 보내지 않는다."""
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
