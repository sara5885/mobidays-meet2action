"""발화 정제 + 의미 단위 청크 분리.

처리 단계 (3.2 요구사항):
  1) 머뭇거림/필러 제거 (어…, 음…, 아 그게)
  2) 광고·마케팅 약어 사전 주입 (LLM이 약어를 오해하지 않도록 컨텍스트 제공)
  3) 인접 중복/메아리 발화 정리
  4) 의미 단위 청크 분리 (화자 라벨 부착)

주의: 원문 텍스트(utterances.text)는 보존하고, 정제는 '청크 생성 시점'에만 적용한다.
→ 추적성(어떤 원문에서 나왔는지)을 잃지 않기 위함.
"""
from __future__ import annotations

import re

from .schemas import Chunk, Utterance

# 광고/마케팅 약어 사전. LLM 프롬프트 컨텍스트로도 재사용된다(prompts.py).
ABBREV: dict[str, str] = {
    "CPM": "노출 1000회당 비용(CPM)",
    "ROAS": "광고 수익률(ROAS)",
    "CTR": "클릭률(CTR)",
    "CTA": "행동 유도 문구/버튼(CTA)",
    "CVR": "전환율(CVR)",
    "A/B": "A/B 테스트",
    "GA": "구글 애널리틱스(GA)",
    "메타": "메타(페이스북·인스타그램 광고 플랫폼)",
    "픽셀": "전환 추적 픽셀(Meta Pixel)",
    "누끼": "배경 제거 제품 컷(누끼 컷)",
    "랜딩": "랜딩 페이지",
    "세그먼트": "타겟 세그먼트",
    "리드타임": "제작 소요 기간(리드타임)",
    "컨펌": "광고주 승인(컨펌)",
}

# 머뭇거림/필러: 단독으로 쓰인 감탄/주저 표현
_FILLER = re.compile(
    r"(^|\s)(음+|어+|아+|에+|그+|저기|뭐|자)(\s*[…\.]{1,})?(?=\s|$|,)"
)
_MULTI_DOTS = re.compile(r"[…\.]{2,}")
_SPACES = re.compile(r"\s+")


def clean_text(text: str) -> str:
    t = _MULTI_DOTS.sub(" ", text)
    t = _FILLER.sub(" ", t)
    t = _SPACES.sub(" ", t).strip()
    return t


def dedup_consecutive(utterances: list[Utterance]) -> list[Utterance]:
    """같은 화자가 거의 동일한 말을 연달아 한 메아리 발화 제거."""
    out: list[Utterance] = []
    for u in utterances:
        if out and out[-1].speaker_code == u.speaker_code:
            a, b = clean_text(out[-1].text), clean_text(u.text)
            if a and (a == b):
                continue
        out.append(u)
    return out


def abbrev_glossary(utterances: list[Utterance]) -> dict[str, str]:
    """이 회의에 실제 등장한 약어만 추려 용어집을 만든다(프롬프트 주입용)."""
    joined = " ".join(u.text for u in utterances)
    return {k: v for k, v in ABBREV.items() if k in joined}


def to_chunks(utterances: list[Utterance], max_chars: int = 4000) -> list[Chunk]:
    """화자 라벨을 붙여 의미 단위 청크로 묶는다.

    짧은 회의(<max_chars)는 1청크로 → R&R 핑퐁/흐릿한 결정이 청크 경계로 잘리지 않게.
    긴 회의는 max_chars 단위로 분리(긴 transcript 대응).
    """
    utterances = dedup_consecutive(utterances)
    if not utterances:
        return []
    meeting_id = utterances[0].meeting_id
    chunks: list[Chunk] = []
    buf_lines: list[str] = []
    buf_ids: list[int] = []
    cid = 0
    for u in utterances:
        cleaned = clean_text(u.text)
        if not cleaned:
            continue
        line = f"#{u.seg_id} [{u.speaker_role}] {cleaned}"
        if buf_lines and len("\n".join(buf_lines + [line])) > max_chars:
            chunks.append(Chunk(meeting_id=meeting_id, chunk_id=cid,
                                seg_ids=buf_ids, text="\n".join(buf_lines)))
            cid += 1
            buf_lines, buf_ids = [], []
        buf_lines.append(line)
        buf_ids.append(u.seg_id)
    if buf_lines:
        chunks.append(Chunk(meeting_id=meeting_id, chunk_id=cid,
                            seg_ids=buf_ids, text="\n".join(buf_lines)))
    return chunks
