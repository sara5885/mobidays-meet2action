"""LLM 출력 → 검증된 ActionItem. 신뢰성 엔지니어링의 핵심 모듈.

흐름:
  프롬프트 → provider 호출 → JSON 파싱 → pydantic 검증 → (실패 시 재시도)
  → 환각 필터(source_seg_ids 가 입력에 존재하는지) → confidence 보정.
"""
from __future__ import annotations

import json

from . import config
from .llm import get_provider
from .llm.base import LLMProvider
from .prompts import SYSTEM, build_user_prompt
from .schemas import ActionItem, Chunk, ExtractionResult


def _strip_code_fence(s: str) -> str:
    s = s.strip()
    if s.startswith("```"):
        s = s.split("```", 2)[1] if "```" in s[3:] else s[3:]
        s = s.lstrip("json").strip()
        if s.endswith("```"):
            s = s[:-3]
    return s.strip()


def _parse_and_validate(raw: str, meeting_id: str) -> ExtractionResult:
    data = json.loads(_strip_code_fence(raw))
    # meeting_id는 LLM이 모르므로 주입
    for it in data.get("action_items", []):
        it["meeting_id"] = meeting_id
    return ExtractionResult(**data)


def extract_from_chunk(
    chunk: Chunk,
    valid_seg_ids: set[int],
    provider: LLMProvider | None = None,
    glossary: dict[str, str] | None = None,
) -> list[ActionItem]:
    provider = provider or get_provider()
    base = build_user_prompt(chunk.text, glossary, chunk.meeting_id)
    user = base

    last_err: Exception | None = None
    for attempt in range(config.MAX_LLM_RETRIES + 1):
        try:
            raw = provider.complete_json(SYSTEM, user)
            result = _parse_and_validate(raw, chunk.meeting_id)
            return _postprocess(result.action_items, valid_seg_ids)
        except Exception as e:  # JSON 파싱 실패 / 스키마 위반
            last_err = e
            # 재시도 시 더 강하게 JSON만 요구
            user = (
                base
                + "\n\n[중요] 이전 출력이 유효한 JSON이 아니었습니다. "
                "오직 유효한 JSON만, 코드펜스 없이 출력하세요."
            )
    print(f"[extract] chunk {chunk.chunk_id} 추출 실패 (재시도 소진): {last_err}")
    return []


def _postprocess(items: list[ActionItem], valid_seg_ids: set[int]) -> list[ActionItem]:
    """환각 방지 + confidence 보정."""
    cleaned: list[ActionItem] = []
    for a in items:
        # 환각 필터: 존재하지 않는 seg_id 인용 제거
        bad = [s for s in a.source_seg_ids if s not in valid_seg_ids]
        if bad:
            a.source_seg_ids = [s for s in a.source_seg_ids if s in valid_seg_ids]
            a.confidence = min(a.confidence, 0.5)  # 근거 약화 → 신뢰도 하향
        # 담당자 불명확하면 신뢰도 상한
        if a.owner_role is None:
            a.confidence = min(a.confidence, 0.6)
        cleaned.append(a)
    return cleaned
