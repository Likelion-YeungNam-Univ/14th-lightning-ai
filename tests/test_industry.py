from app.services.industry import (
    ETC_CODE,
    classify_industry,
    load_industry_ministries,
    load_industry_rules,
)


def test_rules_reference_valid_codes():
    codes = {i["industry_code"] for i in load_industry_ministries()}
    for rule in load_industry_rules():
        assert rule["industry_code"] in codes, rule["industry_code"]


def test_ministry_data_complete():
    industries = load_industry_ministries()
    assert len(industries) == 13  # 12분류 + etc
    for ind in industries:
        assert ind["profile"], f"{ind['industry_code']}에 profile(C7) 누락"
        if ind["industry_code"] != ETC_CODE:
            assert ind["ministries"], f"{ind['industry_code']}에 소관 부처 누락"


def test_classify_known_krx_industries():
    # 실측(2026-08-13) KRX Industry 문자열 기준
    assert classify_industry("통신 및 방송 장비 제조업") == "semiconductor"  # 삼성전자
    assert classify_industry("반도체 제조업") == "semiconductor"  # SK하이닉스
    assert classify_industry("전기 통신업") == "telecom"  # semiconductor 규칙에 안 먹혀야 함
    assert classify_industry("의약품 제조업") == "bio_pharma"
    assert classify_industry("은행 및 저축기관") == "finance"
    assert classify_industry("소프트웨어 개발 및 공급업") == "platform_game"
    assert classify_industry("자동차 신품 부품 제조업") == "auto_battery"
    assert classify_industry("1차 철강 제조업") == "steel_material"


def test_classify_fallbacks():
    assert classify_industry(None) == ETC_CODE
    assert classify_industry("") == ETC_CODE
    assert classify_industry("일반 교습 학원") == ETC_CODE  # 12분류에 없는 업종은 etc가 정답
    # Industry가 비어도 Products로 재시도하는 수집기 경로 (krx.py)
    assert classify_industry(None, "반도체 웨이퍼 캐리어") == "semiconductor"
