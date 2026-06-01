"""발화 정제 + 청크 분리.

1단계(뼈대)에서는 최소 구현:
- 빈 발화 제거 / 머뭇거림 토큰 가벼운 정리
- 화자 라벨 붙여 청크로 묶기 (지금은 전체를 1청크로; 2단계에서 의미단위 분리 강화)
약어 사전/중복 제거 강화는 2단계에서 확장한다.
"""
from __future__ import annotations

import re

from .schemas import Chunk, Utterance

# 2단계에서 확장할 약어 사전 (현재는 시드)
ABBREV = {
    "CPM": "노출 1000회당 비용(CPM)",
    "ROAS": "광고 수익률(ROAS)",
    "CTA": "행동 유도 문구(CTA)",
    "A/B": "A/B 테스트",
}

_FILLER = re.compile(r"(^|\s)(음+|어+|아+|그+|저기)(\.{2,})?(?=\s|$)")


def clean_text(text: str) -> str:
    t = _FILLER.sub(" ", text)
    t = re.sub(r"\s+", " ", t).strip()
    return t


def to_chunks(utterances: list[Utterance], max_chars: int = 1200) -> list[Chunk]:
    """화자 라벨을 붙여 청크로 묶는다. max_chars 넘으면 분리."""
    if not utterances:
        return []
    meeting_id = utterances[0].meeting_id
    chunks: list[Chunk] = []
    buf_lines: list[str] = []
    buf_ids: list[int] = []
    cid = 0
    for u in utterances:
        line = f"[{u.speaker_role}] {clean_text(u.text)}".strip()
        if not clean_text(u.text):
            continue
        prospective = "\n".join(buf_lines + [line])
        if buf_lines and len(prospective) > max_chars:
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
