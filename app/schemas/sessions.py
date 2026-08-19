from pydantic import BaseModel, Field


class SessionResponse(BaseModel):
    created: bool  # 이번 호출에서 새로 발급됐는가 (기존 세션 재사용이면 False)
    authenticated: bool
    stocks: list[str]  # 등록 종목코드 (노출 순서대로)


class LoginRequest(BaseModel):
    id: str = Field(min_length=1)
    password: str = Field(min_length=1)


class LoginResponse(BaseModel):
    authenticated: bool


class ResetDemoResponse(BaseModel):
    stocks: list[str]  # 초기화 후 기본 종목
    deleted_saved_cards: int
