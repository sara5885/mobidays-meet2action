"""pydantic 데이터 모델. 파이프라인 전 구간의 '계약(contract)' 역할.

설계 의도:
- LLM 출력은 본질적으로 신뢰 불가 → ActionItem을 pydantic으로 강제 검증하면
  스키마 위반/타입 오류/허용값 위반을 적재 전에 거를 수 있다.
- confidence + source_utterance_ids 로 '왜 이 액션아이템이 나왔는지' 추적 가능.
"""
from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, field_validator


class Utterance(BaseModel):
    """화자 분리된 발화 단위 (transcript 1세그먼트)."""
    meeting_id: str
    seg_id: int
    speaker_code: str          # 예: SPK_1
    speaker_role: str          # 정규화된 역할: 팀장 / 퍼포먼스 마케터 ...
    start: float
    end: float
    text: str


class Chunk(BaseModel):
    """의미 단위로 묶인 발화 묶음. LLM 입력 단위."""
    meeting_id: str
    chunk_id: int
    seg_ids: list[int]
    text: str                  # 화자 라벨 포함된 합쳐진 텍스트


class ActionStatus(str, Enum):
    open = "open"
    in_progress = "in_progress"
    done = "done"
    blocked = "blocked"  # 지연/막힘 (delay_reason과 함께 사용)


class ActionItem(BaseModel):
    """구조화된 액션아이템. LLM 출력을 이 스키마로 강제한다."""
    meeting_id: str
    title: str = Field(..., description="액션아이템 한 줄 요약")
    owner_role: Optional[str] = Field(
        None, description="담당자 역할. 암묵 R&R('제가 챙길게요')도 매핑. 불명확하면 null"
    )
    due: Optional[str] = Field(None, description="기한 텍스트. 예: '이번 주', '다음주 수요일'")
    status: ActionStatus = ActionStatus.open
    confidence: float = Field(..., ge=0.0, le=1.0, description="추출 신뢰도 0~1")
    source_seg_ids: list[int] = Field(
        default_factory=list, description="근거가 된 원본 발화 seg_id (환각 방지/추적용)"
    )
    source_quote: str = Field("", description="근거 발화 인용")

    @field_validator("title")
    @classmethod
    def title_not_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("title은 비어 있을 수 없음")
        return v.strip()

    @field_validator("source_quote", mode="before")
    @classmethod
    def coerce_quote(cls, v):
        # LLM이 인용을 리스트로 주는 경우가 있어(특히 소형 로컬 모델) 문자열로 보정.
        if isinstance(v, list):
            return " ".join(str(x) for x in v)
        return "" if v is None else str(v)

    @field_validator("source_seg_ids", mode="before")
    @classmethod
    def coerce_seg_ids(cls, v):
        # "1,2" / [ "1", 2 ] / 단일 int 등 다양한 형태를 int 리스트로 보정.
        if v is None:
            return []
        if isinstance(v, (int, str)):
            v = str(v).replace(",", " ").split()
        out = []
        for x in v:
            try:
                out.append(int(x))
            except (ValueError, TypeError):
                continue
        return out

    @field_validator("owner_role", mode="before")
    @classmethod
    def empty_owner_to_none(cls, v):
        if v is None:
            return None
        s = str(v).strip()
        return s or None


class ExtractionResult(BaseModel):
    """LLM 1회 호출 결과 컨테이너 (스키마 강제 대상)."""
    action_items: list[ActionItem] = Field(default_factory=list)


class MeetingSummary(BaseModel):
    """회의록 정리 결과: 한 줄 요약 + 안건 + 결정사항."""
    summary: str = Field("", description="회의 한 줄 요약")
    agenda: list[str] = Field(default_factory=list, description="다룬 주요 안건")
    decisions: list[str] = Field(default_factory=list, description="최종 합의된 결정사항")

    @field_validator("summary", mode="before")
    @classmethod
    def _coerce_summary(cls, v):
        if isinstance(v, list):
            return " ".join(str(x) for x in v)
        return "" if v is None else str(v)

    @field_validator("agenda", "decisions", mode="before")
    @classmethod
    def _coerce_list(cls, v):
        if v is None:
            return []
        if isinstance(v, str):
            return [v] if v.strip() else []
        return [str(x).strip() for x in v if str(x).strip()]
