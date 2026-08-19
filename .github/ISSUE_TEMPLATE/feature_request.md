---
name: "Feature Request"
about: "새 기능 개발 작업을 등록합니다."
title: "[FEAT] "
labels: feature
assignees: ""
---

## Description (기능 설명)
<!-- 무엇을 만드는지, 어떤 명세 항목(F-번호)에 해당하는지 적어주세요. -->

**명세**: `docs/assit_요구사항명세서_v3.md` F-x.x + `docs/명세확정사항.md`

## Tasks (작업 내용)
<!-- 구현 단위로 쪼갠 체크리스트 -->
- [ ]

## Definition of Done (완료 조건)
<!-- CLAUDE.md DoD 기준 -->
- [ ] 명세 세부 사항 충족
- [ ] 정상 경로 + 에러 경로(401/400/빈 상태) 동작 확인
- [ ] pytest 테스트 존재 (외부 API·LLM은 목킹, 실 키 불요)
- [ ] `ruff check` 통과

## Notes (참고)
<!-- 의존하는 이슈, 열려 있는 결정, 링크 -->
