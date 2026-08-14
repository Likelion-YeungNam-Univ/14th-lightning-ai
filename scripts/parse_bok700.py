"""한국은행 「경제금융용어 700선」(2020, PDF) → data/bok_700.json 1회성 파서.

실행 전: pip install pypdf (서버 런타임 의존성 아님 — requirements에 넣지 않는다)
사용법: .venv/bin/python -m scripts.parse_bok700 <PDF 경로>

구조(실측): 앞부분 목차(용어 ・・・ 페이지) → 본문(용어 제목 줄 + 정의 문단 + 연관검색어).
목차에서 용어 목록을 뽑고, 본문에서 제목 줄을 순서대로 찾아 정의를 분할한다.
"""

import json
import re
import sys
from pathlib import Path

from pypdf import PdfReader

OUT_PATH = Path(__file__).resolve().parent.parent / "data" / "bok_700.json"
DOTS_RE = re.compile(r"^(.+?)\s*(?:・\s*)+(\d+)$")  # 점선은 공백이 섞여 추출되기도 한다
NOISE_RE = re.compile(r"^(\d+|[ivxlc]+|경제금융용어 700선|찾아보기.*|[ㄱ-ㅎ])$")


def _norm(s: str) -> str:
    return re.sub(r"[\s・･·]+", "", s)  # 공백·가운뎃점 이형까지 제거해 비교


def _clean_term(term: str) -> str:
    term = re.sub(r"^경제금융용어 700선\s*", "", term)  # 목차 머리글이 섞여 붙는 경우
    return term.strip()


def parse_index(reader: PdfReader) -> list[str]:
    """목차 페이지에서 용어 목록(수록 순서대로)을 뽑는다. 줄바꿈된 항목은 이어 붙인다."""
    terms: list[str] = []
    buffer = ""
    for page in reader.pages[:16]:  # 목차는 앞 16페이지 안에 있다 (실측)
        for raw in (page.extract_text() or "").splitlines():
            line = raw.strip()
            if not line or NOISE_RE.match(_norm(line)):
                continue
            m = DOTS_RE.match(buffer + " " + line if buffer else line)
            if m:
                terms.append(_clean_term(m.group(1)))
                buffer = ""
            elif "・" not in line:  # 페이지 번호 없는 줄 — 다음 줄과 이어지는 용어명
                buffer = (buffer + " " + line).strip()
    return terms


def parse_body(reader: PdfReader, terms: list[str]) -> list[dict]:
    lines: list[str] = []
    for page in reader.pages[16:]:
        for raw in (page.extract_text() or "").splitlines():
            line = raw.strip()
            if not line or NOISE_RE.match(_norm(line)) or line.endswith("∙"):
                continue  # 머리글·바닥글 제거
            lines.append(line)

    # 용어 제목 줄 위치 찾기 — 공백 제거 후 완전 일치(짧은 제목 줄 전제)
    norm_terms = [_norm(t) for t in terms]
    boundaries: list[tuple[int, int]] = []  # (용어 idx, 줄 idx)
    cursor = 0
    for ti, nt in enumerate(norm_terms):
        for li in range(cursor, len(lines)):
            cand = _norm(lines[li])
            if cand == nt or (
                len(cand) < 45 and li + 1 < len(lines) and cand + _norm(lines[li + 1]) == nt
            ):
                boundaries.append((ti, li))
                cursor = li + 1
                break

    entries: list[dict] = []
    for bi, (ti, li) in enumerate(boundaries):
        end = boundaries[bi + 1][1] if bi + 1 < len(boundaries) else len(lines)
        start = li + 1
        if _norm(lines[li]) != norm_terms[ti]:  # 두 줄짜리 제목이었으면 한 줄 더 스킵
            start += 1
        content = " ".join(lines[start:end]).strip()
        content = content.replace("경제금융용어 700선", " ")  # 본문에 섞여 붙는 페이지 머리글
        content = re.sub(r"\s+", " ", content)
        if len(content) >= 50:  # 파싱 실패(빈 정의) 방어
            entries.append({"term": terms[ti], "content": content[:3000]})
    return entries


def main() -> None:
    pdf_path = sys.argv[1]
    reader = PdfReader(pdf_path)
    terms = parse_index(reader)
    print(f"목차 용어 수: {len(terms)}")
    entries = parse_body(reader, terms)
    print(f"본문 매칭 성공: {len(entries)}")
    missed = set(terms) - {e["term"] for e in entries}
    if missed:
        print(f"미매칭 {len(missed)}건 예시: {sorted(missed)[:10]}")
    OUT_PATH.write_text(
        json.dumps(
            {"source": "한국은행 경제금융용어 700선(2020)", "entries": entries},
            ensure_ascii=False,
            indent=1,
        ),
        encoding="utf-8",
    )
    print(f"저장: {OUT_PATH} ({len(entries)}건)")


if __name__ == "__main__":
    main()
