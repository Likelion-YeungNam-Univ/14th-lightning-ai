"""이슈 #98 — 정기보고서 재무 슬롯 테스트. DART는 respx 목킹(실 키 불요).

예상 문제 지점(팀 합의 방식)과 대응 테스트:
1. 기재정정 원본(다른 접수번호)에 최신 재무가 잘못 붙음 → rcept_no 불일치 시 미보강
2. 연결(CFS)이 있는데 개별(OFS) 수치가 붙음 → CFS 우선
3. 은행 등 매출액 없는 업종에서 크래시 → 있는 계정만 채움
4. 금액에 쉼표·음수·비숫자("삭제") → 포맷터가 조/억 변환, 비숫자는 None
5. 재무 API 실패(비정상 status·네트워크)가 공시 수집 전체를 막음 → 그룹 격리
6. 제목에 기간 "(YYYY.MM)"이 없음 → 조용히 건너뜀
7. 이미 보강된 카드 재호출로 API 낭비 → detail_json 있으면 대상 제외 (sync 통합에서 확인)
"""

import httpx
import respx

from app.collectors.dart import (
    _enrich_periodic_financials,
    _format_krw,
    _report_period,
)
from app.db import SessionLocal
from app.models import MARKET_DOMESTIC, SourceItem, StockMaster


def _fin_row(fs_div, account, amount, rcept="R1"):
    return {
        "rcept_no": rcept,
        "fs_div": fs_div,
        "sj_div": "IS",
        "account_nm": account,
        "thstrm_nm": "제 58 기반기",
        "thstrm_amount": amount,
    }


def _mk_item(db, key, title, doc_type="반기보고서"):
    item = SourceItem(
        tab="disclosure",
        market=MARKET_DOMESTIC,
        source_key=key,
        title=title,
        doc_type=doc_type,
    )
    db.add(item)
    db.flush()
    return item


def _stock(db):
    stock = db.get(StockMaster, "111110")
    stock.corp_code = stock.corp_code or "00000001"
    return stock


# ── 단위: 포맷터·기간 파싱 (예상 문제 4·6) ────────────────────────────


def test_format_krw_units():
    assert _format_krw("171,499,470,000,000") == "171.5조 원"
    assert _format_krw("440,300,000,000") == "4,403억 원"
    assert _format_krw("-1,234,500,000") == "-12억 원"
    assert _format_krw("123,456") == "123,456원"
    assert _format_krw("삭제") is None and _format_krw("") is None


def test_report_period_parsing():
    assert _report_period("반기보고서 (2026.06)") == ("2026", "11012")
    assert _report_period("[기재정정]사업보고서 (2025.12)") == ("2025", "11011")
    assert _report_period("분기보고서 (2026.09)") == ("2026", "11014")
    assert _report_period("기간 없는 제목") is None


# ── 보강 로직 (예상 문제 1·2·3·5) ─────────────────────────────────────


@respx.mock
def test_enrich_matches_rcept_and_prefers_cfs(client):
    respx.get(host="opendart.fss.or.kr", path="/api/fnlttSinglAcnt.json").mock(
        return_value=httpx.Response(
            200,
            json={
                "status": "000",
                "list": [
                    _fin_row("CFS", "매출액", "171,499,470,000,000"),
                    _fin_row("CFS", "영업이익", "89,492,412,000,000"),
                    _fin_row("CFS", "당기순이익(손실)", "71,624,461,000,000"),
                    _fin_row("CFS", "당기순이익(손실)", "999"),  # 중복 행 — 첫 값 우선
                    _fin_row("OFS", "매출액", "1,000"),  # 개별 — 무시돼야 함 (예상 문제 2)
                ],
            },
        )
    )
    with SessionLocal() as db:
        stock = _stock(db)
        latest = _mk_item(db, "R1", "[기재정정]반기보고서 (2026.06)")
        original = _mk_item(db, "R0", "반기보고서 (2026.06)")  # 정정 전 원본 — 접수번호 불일치
        _enrich_periodic_financials(db, stock, [latest, original])
        db.commit()

        assert latest.detail_json["slots"] == [
            {"label": "매출액", "value": "171.5조 원"},
            {"label": "영업이익", "value": "89.5조 원"},
            {"label": "당기순이익", "value": "71.6조 원"},
        ]
        assert "연결재무제표" in latest.content and "171.5조 원" in latest.content
        assert original.detail_json is None  # 예상 문제 1 — 오적재 방지


@respx.mock
def test_enrich_partial_accounts_and_failures(client):
    calls = {"n": 0}

    def responder(request):
        calls["n"] += 1
        if calls["n"] == 1:  # 은행 — 매출액 없음 (예상 문제 3)
            return httpx.Response(
                200,
                json={
                    "status": "000",
                    "list": [_fin_row("OFS", "영업이익", "520,300,000,000", "B1")],
                },
            )
        return httpx.Response(200, json={"status": "013", "message": "없음"})  # 예상 문제 5

    respx.get(host="opendart.fss.or.kr", path="/api/fnlttSinglAcnt.json").mock(
        side_effect=responder
    )
    with SessionLocal() as db:
        stock = _stock(db)
        bank = _mk_item(db, "B1", "반기보고서 (2026.06)")
        none = _mk_item(db, "N1", "사업보고서 (2025.12)", doc_type="사업보고서")
        _enrich_periodic_financials(db, stock, [bank, none])
        db.commit()
        assert bank.detail_json["slots"] == [{"label": "영업이익", "value": "5,203억 원"}]
        assert none.detail_json is None  # 013 — 제목 기반 요약으로 진행


@respx.mock
def test_network_error_isolated(client):
    respx.get(host="opendart.fss.or.kr", path="/api/fnlttSinglAcnt.json").mock(
        side_effect=httpx.ConnectError("boom")
    )
    with SessionLocal() as db:
        stock = _stock(db)
        item = _mk_item(db, "E1", "분기보고서 (2026.03)", doc_type="분기보고서")
        _enrich_periodic_financials(db, stock, [item])  # 예외가 새어 나오면 실패
        assert item.detail_json is None
