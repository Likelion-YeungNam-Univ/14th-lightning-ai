"""SQLAlchemy 모델 — 명세 v3 부록 1 + 확정사항 6절.

명세와 컬럼이 어긋나면 명세확정사항.md에 근거를 남긴 뒤 여기를 수정한다 (CLAUDE.md 참조).
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

# 국내/해외 구분(F-2) — 소문자 통일 (확정사항 6절)
MARKET_DOMESTIC = "domestic"
MARKET_OVERSEAS = "overseas"


class UserSession(Base):
    """F-1.1 — 데이터 귀속 단위. 계정 테이블은 없다(모의 로그인)."""

    __tablename__ = "session"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    authenticated: Mapped[bool] = mapped_column(Boolean, default=False)
    # F-2.2 — 구분별 마지막 본 종목 (구분 전환 시 복귀 지점)
    last_stock_domestic: Mapped[str | None] = mapped_column(String(12))
    last_stock_overseas: Mapped[str | None] = mapped_column(String(12))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class StockMaster(Base):
    """F-3.1(국내 KRX 배치) + F-3.2(해외 화이트리스트) — 한 테이블, market으로 구분."""

    __tablename__ = "stock_master"

    stock_code: Mapped[str] = mapped_column(String(12), primary_key=True)  # 국내 6자리 / 해외 티커
    market: Mapped[str] = mapped_column(String(12), index=True)  # domestic | overseas
    name: Mapped[str] = mapped_column(String(80))
    aliases: Mapped[list] = mapped_column(JSON, default=list)  # 한글·영문 별칭 (검색용, F-3.3)
    exchange: Mapped[str | None] = mapped_column(String(16))  # KOSPI/KOSDAQ 등 시장구분
    industry_code: Mapped[str | None] = mapped_column(String(32), index=True)  # 국내 12분류
    sic_code: Mapped[str | None] = mapped_column(String(8))  # 해외 SIC (F-3.2)
    market_cap: Mapped[int | None] = mapped_column(BigInteger)  # 해외는 받지 않음 → None
    corp_code: Mapped[str | None] = mapped_column(String(8))  # DART 고유번호 (F-3.1.1)
    cik: Mapped[str | None] = mapped_column(String(10))  # SEC CIK (F-3.2.1)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )


class IndustryAgency(Base):
    """F-4.2.1(국내 부처) + F-4.7.1(미국 기관) — market으로 구분해 한 테이블.

    profile은 업종 성격 서술로 F-5.3 '내 종목엔' 문장의 입력 (확정사항 3·6절).
    """

    __tablename__ = "industry_agency"

    market: Mapped[str] = mapped_column(String(12), primary_key=True)
    industry_key: Mapped[str] = mapped_column(String(32), primary_key=True)  # 12분류 코드 / SIC
    name: Mapped[str] = mapped_column(String(64))
    agencies: Mapped[list] = mapped_column(JSON)  # 소관 부처명 / 연방기관명 리스트
    keywords: Mapped[list] = mapped_column(JSON)  # 규제 동향 2차 필터 키워드
    profile: Mapped[str] = mapped_column(Text)


class DisclosureFormType(Base):
    """F-4.6.1 — 공시 유형 해설. 국내 공시 유형과 미국 폼 코드를 market으로 구분."""

    __tablename__ = "disclosure_form_type"

    market: Mapped[str] = mapped_column(String(12), primary_key=True)
    form_code: Mapped[str] = mapped_column(String(32), primary_key=True)  # 예: 유상증자결정 / 8-K
    description: Mapped[str] = mapped_column(Text)  # 요약 생성 입력 + RAG 소스


class SessionStock(Base):
    """F-3.4~3.8 — 종목은 세션에 귀속. 순서·칩 한도는 구분별로 계산."""

    __tablename__ = "session_stock"

    session_id: Mapped[str] = mapped_column(
        ForeignKey("session.id", ondelete="CASCADE"), primary_key=True
    )
    stock_code: Mapped[str] = mapped_column(ForeignKey("stock_master.stock_code"), primary_key=True)
    display_order: Mapped[int] = mapped_column(Integer, default=0)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)  # 전환율 지표용
    added_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class SourceItem(Base):
    """F-4 — 탭별 원자료. 해당 없는 컬럼은 null."""

    __tablename__ = "source_item"
    __table_args__ = (
        UniqueConstraint("tab", "market", "source_key", name="uq_source_item_tab_key"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tab: Mapped[str] = mapped_column(
        String(16), index=True
    )  # youtube/disclosure/regulation/bok/fed
    market: Mapped[str] = mapped_column(String(12), index=True)  # domestic | overseas
    source_key: Mapped[str] = mapped_column(String(255))  # 접수번호·영상ID·문서번호 등
    title: Mapped[str] = mapped_column(Text)  # 원문 제목 그대로, 영문도 미번역 (F-5.1.2)
    doc_type: Mapped[str | None] = mapped_column(String(64))  # 공시 유형 / 폼 코드
    published_at: Mapped[datetime | None] = mapped_column(DateTime)
    origin_url: Mapped[str | None] = mapped_column(Text)
    # 규제 본문·금리 결정 요지·공시 정형 필드 텍스트 — 요약(F-5.1) 입력 전용 (확정사항 6절)
    content: Mapped[str | None] = mapped_column(Text)
    # 공시 정형 API 슬롯(이슈 #18) — {"slots": [{"label", "value"}]}. 카드·스냅샷에 노출
    detail_json: Mapped[dict | None] = mapped_column(JSON)
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
    """규제 동향 — 자료:업종 1:N 연결. 국내는 12분류 코드, 해외는 SIC (F-5.2.1)."""

    __tablename__ = "source_item_industry"

    source_item_id: Mapped[int] = mapped_column(
        ForeignKey("source_item.id", ondelete="CASCADE"), primary_key=True
    )
    market: Mapped[str] = mapped_column(String(12), primary_key=True)
    industry_key: Mapped[str] = mapped_column(String(32), primary_key=True, index=True)


class GeneratedContent(Base):
    """F-5 — LLM 생성물. 제목 컬럼 없음(F-5.1.2). locked는 검수 잠금(F-5.7)."""

    __tablename__ = "generated_content"
    __table_args__ = (
        UniqueConstraint("source_item_id", "scope", "scope_key", name="uq_generated_scope"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source_item_id: Mapped[int] = mapped_column(
        ForeignKey("source_item.id", ondelete="CASCADE"), index=True
    )
    scope: Mapped[str] = mapped_column(String(8))  # stock | industry
    scope_key: Mapped[str] = mapped_column(String(32))  # 종목코드 / 12분류 코드 / SIC
    summary_short: Mapped[str | None] = mapped_column(Text)
    summary_full: Mapped[str | None] = mapped_column(Text)
    label: Mapped[str | None] = mapped_column(String(8))  # positive | neutral | negative
    label_reason: Mapped[str | None] = mapped_column(Text)
    locked: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class RateLinkSentence(Base):
    """F-5.3.2 — "내 종목엔" 문장 캐시.

    v3 키(market+industry_key+indicator_version)에 tab을 추가한다 — 국내는 한국은행·Fed
    두 탭에 문장이 각각 있어 tab 없이는 충돌한다 (확정사항 6절).
    """

    __tablename__ = "rate_link_sentence"

    market: Mapped[str] = mapped_column(String(12), primary_key=True)
    tab: Mapped[str] = mapped_column(String(16), primary_key=True)  # bok | fed
    industry_key: Mapped[str] = mapped_column(String(32), primary_key=True)
    indicator_version: Mapped[str] = mapped_column(String(64), primary_key=True)
    sentence: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class SavedCard(Base):
    """F-7 — 저장 카드. 구분 컬럼 없음(F-7.4 — 저장됨은 국내·해외 통합).

    표시엔 snapshot_json만 쓴다. source_item 삭제 대비 FK nullable (확정사항 4절).
    """

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


class CollectStatus(Base):
    """F-4.9·F-6.4 — 수집 단위별 최종 상태. '자료 없음'과 '불러오지 못함'을 구분하는 근거."""

    __tablename__ = "collect_status"

    tab: Mapped[str] = mapped_column(String(16), primary_key=True)
    scope_key: Mapped[str] = mapped_column(String(32), primary_key=True)  # 종목코드/업종/global
    status: Mapped[str] = mapped_column(String(8))  # ok | failed
    detail: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )


class QuotaUsage(Base):
    """F-4.5.1 — 유튜브 쿼터 자체 카운터. 국내·해외 공용."""

    __tablename__ = "quota_usage"

    api_name: Mapped[str] = mapped_column(String(32), primary_key=True)
    usage_date: Mapped[date] = mapped_column(Date, primary_key=True)
    units_used: Mapped[int] = mapped_column(Integer, default=0)


class KnowledgeChunk(Base):
    """확정사항 5절 — RAG 지식베이스 (700선 + 국내 공시유형 해설 + 미국 폼 해설)."""

    __tablename__ = "knowledge_chunk"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source: Mapped[str] = mapped_column(String(32))  # bok_700 | dart_doctype | sec_formtype
    term: Mapped[str] = mapped_column(String(128), index=True)
    content: Mapped[str] = mapped_column(Text)
    embedding: Mapped[list | None] = mapped_column(Vector(EMBEDDING_DIM))


class TermCache(Base):
    """F-5.4 — 용어 풀이 캐시. 키 = 용어 + 탭."""

    __tablename__ = "term_cache"

    term: Mapped[str] = mapped_column(String(64), primary_key=True)
    tab: Mapped[str] = mapped_column(String(16), primary_key=True)
    explanation: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class EconCard(Base):
    """E-6 — 경제 상식 카드. status/locked로 검수 절차(E-3.3)가 동작한다.

    target_tab 컬럼 없음 — 탭 바로가기 버튼을 없애고 본문 문장으로만 안내한다(E-4.4).
    """

    __tablename__ = "econ_card"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(String(80))  # 질문형, 40자 이내(E-2.4.1)
    body: Mapped[str] = mapped_column(Text)  # 문장 단위 각주(¹²³) 포함
    # [{"number":1,"org":"한국은행","doc_title":"...","url":"..."}] — 각주번호·기관명·문서제목·URL
    sources: Mapped[list] = mapped_column(JSON)
    batch_id: Mapped[str] = mapped_column(String(36), index=True)
    # draft(생성 직후) -> filtered(자동필터 통과) -> approved(표본검수/배치승인) -> rejected
    status: Mapped[str] = mapped_column(String(8), default="draft", index=True)
    reject_reason: Mapped[str | None] = mapped_column(Text)
    locked: Mapped[bool] = mapped_column(Boolean, default=False)  # 승인본 보호(E-3.3)
    approved_by: Mapped[str | None] = mapped_column(String(64))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class EconRotation(Base):
    """E-5.2 — 매시 노출 세트 기록. 직전 세트 회피(E-5.2.1)에 쓴다."""

    __tablename__ = "econ_rotation"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    rotated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    card_ids: Mapped[list] = mapped_column(JSON)


class BettingRoom(Base):
    """C-4, C-10 — 커뮤니티 탭 베팅방. market 컬럼 없음 — stock_code가 구분을 결정한다."""

    __tablename__ = "betting_room"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    stock_code: Mapped[str] = mapped_column(ForeignKey("stock_master.stock_code"), index=True)
    creator_session_id: Mapped[str] = mapped_column(ForeignKey("session.id"))
    title: Mapped[str] = mapped_column(String(120))
    target_price: Mapped[int] = mapped_column(Integer)  # 1,000원 단위 (C-4.1.4)
    judge_date: Mapped[date] = mapped_column(Date)
    body: Mapped[str | None] = mapped_column(Text)
    # open(판가름 전) | pending(정산 재시도 중) | closed(정산 완료) | void(무효)
    status: Mapped[str] = mapped_column(String(8), default="open", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    settled_at: Mapped[datetime | None] = mapped_column(DateTime)
    result_side: Mapped[str | None] = mapped_column(String(8))  # up | down
    settle_attempts: Mapped[int] = mapped_column(Integer, default=0)  # C-6.2.6 재시도 카운터
    settle_close_price: Mapped[int | None] = mapped_column(Integer)  # 정산 근거 (C-12)


class BettingRoomAttachment(Base):
    """C-4.2 — 방 본문 첨부(사진 여러 장 또는 저장 카드 1개)."""

    __tablename__ = "betting_room_attachment"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    room_id: Mapped[int] = mapped_column(
        ForeignKey("betting_room.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[str] = mapped_column(String(8))  # image | card
    image_url: Mapped[str | None] = mapped_column(Text)
    saved_card_id: Mapped[int | None] = mapped_column(
        ForeignKey("saved_card.id", ondelete="SET NULL")
    )


class BettingEntry(Base):
    """C-6.1 — 베팅 참여. 1인 1회(room_id+session_id 유니크, C-6.1.2)."""

    __tablename__ = "betting_entry"
    __table_args__ = (
        UniqueConstraint("room_id", "session_id", name="uq_betting_entry_room_session"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    room_id: Mapped[int] = mapped_column(
        ForeignKey("betting_room.id", ondelete="CASCADE"), index=True
    )
    session_id: Mapped[str] = mapped_column(ForeignKey("session.id"))
    side: Mapped[str] = mapped_column(String(8))  # up(간다) | down(안 간다)
    amount: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class RoomComment(Base):
    """C-7 — 베팅방 댓글."""

    __tablename__ = "room_comment"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    room_id: Mapped[int] = mapped_column(
        ForeignKey("betting_room.id", ondelete="CASCADE"), index=True
    )
    session_id: Mapped[str] = mapped_column(ForeignKey("session.id"))
    body: Mapped[str] = mapped_column(String(300))
    saved_card_id: Mapped[int | None] = mapped_column(
        ForeignKey("saved_card.id", ondelete="SET NULL")
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class PointLedger(Base):
    """C-8.3 — 포인트 원장. 잔액은 항상 이 테이블의 합계로 계산한다(컬럼으로 들고 있지 않음)."""

    __tablename__ = "point_ledger"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(ForeignKey("session.id"), index=True)
    kind: Mapped[str] = mapped_column(String(16))  # charge/bet/win/refund/exchange
    amount: Mapped[int] = mapped_column(Integer)  # 적립은 양수, 차감은 음수
    ref_type: Mapped[str | None] = mapped_column(String(16))  # room/gifticon 등
    ref_id: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class GifticonOrder(Base):
    """C-9 — 기프티콘 교환 이력. 시연 범위는 발급 완료 화면까지(C-9.1.5, 더미 코드)."""

    __tablename__ = "gifticon_order"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(ForeignKey("session.id"), index=True)
    points_used: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16))  # issued | failed
    issued_code: Mapped[str | None] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class EventLog(Base):
    """F-8.2 — 지표 이벤트 (부록 D 산출용)."""

    __tablename__ = "event_log"
    __table_args__ = (Index("ix_event_log_name_created", "event_name", "created_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[str | None] = mapped_column(String(64))
    event_name: Mapped[str] = mapped_column(String(64))
    payload_json: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
