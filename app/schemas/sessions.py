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


class LogoutResponse(BaseModel):
    logged_out: bool


class ResetDemoResponse(BaseModel):
    stocks: list[str]  # 초기화 후 기본 종목
    deleted_saved_cards: int


# ── 실계정 (#74) ──────────────────────────────────────────────────────


class SignupRequest(BaseModel):
    login_id: str = Field(min_length=4, max_length=20, pattern=r"^[a-z0-9_]+$")
    password: str = Field(min_length=8, max_length=64)
    nickname: str = Field(min_length=1, max_length=12)


class AccountLoginRequest(BaseModel):
    login_id: str = Field(min_length=1)
    password: str = Field(min_length=1)


class AccountResponse(BaseModel):
    login_id: str
    nickname: str
    authenticated: bool = True  # 기존 LoginResponse와 호환되는 신호
