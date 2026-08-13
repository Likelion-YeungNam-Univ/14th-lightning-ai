from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_url: str = "postgresql+psycopg://assit:assit@localhost:5432/assit"

    # 외부 데이터 API (F-8.4)
    dart_api_key: str = ""
    ecos_api_key: str = ""
    fred_api_key: str = ""
    youtube_api_key: str = ""
    briefing_api_key: str = ""
    sec_user_agent: str = ""  # SEC EDGAR 필수 헤더 (F-4.6): "assit-hackathon <이메일>"

    # AI — OpenAI 하나로 생성 + 임베딩 (확정사항 1절)
    openai_api_key: str = ""
    openai_model: str = "gpt-5-mini"  # 생성 모델 — 교체 시 env만 변경

    # 앱
    mock_login_id: str = ""
    mock_login_pw: str = ""
    admin_token: str = ""
    session_ttl_days: int = 30  # F-1.1
    enable_scheduler: bool = False  # 로컬 개발 기본 off, 배포 환경에서 true

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()
