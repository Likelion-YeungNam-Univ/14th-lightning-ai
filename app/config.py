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

    # 커뮤니티 탭 결제 (C-8.2.0) — 토스페이먼츠 테스트 키. 라이브 키는 붙이지 않는다
    toss_secret_key: str = ""
    toss_client_key: str = ""
    # 댓글 작성자 익명 태그(HMAC 키, 승래 리뷰 B-1) — session_id를 응답에 직접 싣지 않기 위함
    session_tag_secret: str = ""
    session_ttl_days: int = 30  # F-1.1
    enable_scheduler: bool = False  # 로컬 개발 기본 off, 배포 환경에서 true
    # 프론트 로컬 개발용 CORS 허용 출처(쉼표 구분). 배포는 Vercel rewrite 프록시(동일 출처)라 불필요
    cors_origins: str = "http://localhost:3000,http://127.0.0.1:3000"

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()
