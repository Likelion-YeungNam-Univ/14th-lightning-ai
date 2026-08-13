"""SQLAlchemy 모델 — 명세 v2.1 부록 1의 테이블 13개.

명세와 컬럼이 어긋나면 명세를 먼저 고친 뒤 여기를 수정한다 (CLAUDE.md 참조).
"""

from datetime import date, datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base

EMBEDDING_DIM = 1536  # OpenAI text-embedding-3-small


class UserSession(Base):
    """F-1.1 — 데이터 귀속 단위. 계정 테이블은 없다(모의 로그인)."""

    __tablename__ = "session"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    authenticated: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class StockMaster(Base):
    """F-2.1 — KRX 배치가 적재하는 기준 데이터."""

    __tablename__ = "stock_master"

    stock_code: Mapped[str] = mapped_column(String(12), primary_key=True)
    name: Mapped[str] = mapped_column(String(80))
    market: Mapped[str] = mapped_column(String(16))  # KOSPI / KOSDAQ
    industry_code: Mapped[str] = mapped_column(String(32), default="etc", index=True)
    market_cap: Mapped[int | None] = mapped_column(BigInteger)
    corp_code: Mapped[str | None] = mapped_column(String(8))  # DART 고유번호 (F-2.1.1)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )


class IndustryMinistry(Base):
    """F-3.2.1 — 업종 → 소관 부처 매핑 + 업종 성격(C7). 코드가 아닌 데이터."""

    __tablename__ = "industry_ministry"

    industry_code: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(64))
    ministries: Mapped[list] = mapped_column(JSON)  # 소관 부처명 리스트
    keywords: Mapped[list] = mapped_column(JSON)  # 규제 동향 2차 필터 키워드
    profile: Mapped[str] = mapped_column(Text)  # 업종 성격 서술 — F-4.3 연결 문장 입력


class SessionStock(Base):
    """F-2.4~2.8 — 종목은 세션에 귀속."""

    __tablename__ = "session_stock"

    session_id: Mapped[str] = mapped_column(
        ForeignKey("session.id", ondelete="CASCADE"), primary_key=True
    )
    stock_code: Mapped[str] = mapped_column(ForeignKey("stock_master.stock_code"), primary_key=True)
    display_order: Mapped[int] = mapped_column(Integer, default=0)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)  # 전환율 지표용
    added_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class SourceItem(Base):
    """F-3 — 탭별 원자료. 해당 없는 컬럼은 null (부록 3 대조표)."""

    __tablename__ = "source_item"
    __table_args__ = (UniqueConstraint("tab", "source_key", name="uq_source_item_tab_key"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tab: Mapped[str] = mapped_column(String(16), index=True)  # youtube/dart/briefing/bok/fed
    source_key: Mapped[str] = mapped_column(String(255))  # 접수번호·영상ID 등 출처 고유키
    title: Mapped[str] = mapped_column(Text)  # 원문 제목 그대로 (F-4.1.2)
    doc_type: Mapped[str | None] = mapped_column(String(64))  # 공시 유형 등
    published_at: Mapped[datetime | None] = mapped_column(DateTime)
    origin_url: Mapped[str | None] = mapped_column(Text)
    thumbnail_url: Mapped[str | None] = mapped_column(Text)  # 유튜브 전용
    channel_name: Mapped[str | None] = mapped_column(String(120))  # 유튜브 전용
    view_count: Mapped[int | None] = mapped_column(BigInteger)  # 유튜브 전용
    indicator_value: Mapped[str | None] = mapped_column(String(32))  # 금리 탭 전용
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class SourceItemStock(Base):
    """공시·유튜브 — 자료:종목 1:1 연결."""

    __tablename__ = "source_item_stock"

    source_item_id: Mapped[int] = mapped_column(
        ForeignKey("source_item.id", ondelete="CASCADE"), primary_key=True
    )
    stock_code: Mapped[str] = mapped_column(String(12), primary_key=True, index=True)


class SourceItemIndustry(Base):
    """규제 동향 — 자료:업종 1:N 연결."""

    __tablename__ = "source_item_industry"

    source_item_id: Mapped[int] = mapped_column(
        ForeignKey("source_item.id", ondelete="CASCADE"), primary_key=True
    )
    industry_code: Mapped[str] = mapped_column(String(32), primary_key=True, index=True)


class GeneratedContent(Base):
    """F-4 — LLM 생성물. 제목 컬럼 없음(F-4.1.2). locked는 검수 잠금(F-4.7)."""

    __tablename__ = "generated_content"
    __table_args__ = (
        UniqueConstraint("source_item_id", "scope", "scope_key", name="uq_generated_scope"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source_item_id: Mapped[int] = mapped_column(
        ForeignKey("source_item.id", ondelete="CASCADE"), index=True
    )
    scope: Mapped[str] = mapped_column(String(8))  # stock | industry
    scope_key: Mapped[str] = mapped_column(String(32))  # 종목코드 또는 업종코드
    summary_short: Mapped[str | None] = mapped_column(Text)
    summary_full: Mapped[str | None] = mapped_column(Text)
    label: Mapped[str | None] = mapped_column(String(8))  # positive | neutral | negative
    label_reason: Mapped[str | None] = mapped_column(Text)
    locked: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class RateLinkSentence(Base):
    """F-4.3.2 — "내 종목엔" 문장 캐시. 키 = 탭 + 업종 + 지표 스냅샷 (C11)."""

    __tablename__ = "rate_link_sentence"

    tab: Mapped[str] = mapped_column(String(16), primary_key=True)  # bok | fed
    industry_code: Mapped[str] = mapped_column(String(32), primary_key=True)
    indicator_version: Mapped[str] = mapped_column(String(64), primary_key=True)
    sentence: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class SavedCard(Base):
    """F-6 — 저장 카드. 표시엔 snapshot_json만 쓴다. source_item 삭제 대비 FK nullable."""

    __tablename__ = "saved_card"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(
        ForeignKey("session.id", ondelete="CASCADE"), index=True
    )
    source_item_id: Mapped[int | None] = mapped_column(
        ForeignKey("source_item.id", ondelete="SET NULL")
    )
    tab: Mapped[str] = mapped_column(String(16))  # 저장 당시 탭 (배지용)
    stock_code: Mapped[str] = mapped_column(String(12))  # 저장 당시 종목 (배지·필터용)
    snapshot_json: Mapped[dict] = mapped_column(JSON)
    saved_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class QuotaUsage(Base):
    """F-3.5.1 — 유튜브 쿼터 자체 카운터."""

    __tablename__ = "quota_usage"

    api_name: Mapped[str] = mapped_column(String(32), primary_key=True)
    usage_date: Mapped[date] = mapped_column(Date, primary_key=True)
    units_used: Mapped[int] = mapped_column(Integer, default=0)


class KnowledgeChunk(Base):
    """F-4.8 — RAG 지식베이스 (경제금융용어 700선 + DART 공시유형 해설)."""

    __tablename__ = "knowledge_chunk"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source: Mapped[str] = mapped_column(String(32))  # bok_700 | dart_doctype
    term: Mapped[str] = mapped_column(String(128), index=True)
    content: Mapped[str] = mapped_column(Text)
    embedding: Mapped[list | None] = mapped_column(Vector(EMBEDDING_DIM))


class TermCache(Base):
    """F-4.4 — 용어 풀이 캐시. 키 = 용어 + 탭."""

    __tablename__ = "term_cache"

    term: Mapped[str] = mapped_column(String(64), primary_key=True)
    tab: Mapped[str] = mapped_column(String(16), primary_key=True)
    explanation: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class EventLog(Base):
    """F-7.2 — 지표 이벤트 (부록 D 산출용)."""

    __tablename__ = "event_log"
    __table_args__ = (Index("ix_event_log_name_created", "event_name", "created_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[str | None] = mapped_column(String(64))
    event_name: Mapped[str] = mapped_column(String(64))
    payload_json: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
