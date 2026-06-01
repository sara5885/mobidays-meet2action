"""Mock provider. 실제 API 없이 파이프라인 전체를 검증하기 위한 결정적 응답.

샘플 transcript(노바드림)의 내용에 대응하는 그럴듯한 액션아이템을 돌려준다.
실제 LLM 교체 시 이 클래스만 GeminiProvider로 바뀐다.
"""
from __future__ import annotations

import json

from .base import LLMProvider


class MockProvider(LLMProvider):
    def complete_json(self, system: str, user: str) -> str:
        payload = {
            "action_items": [
                {
                    "title": "리타겟 예산안 초안 작성 및 공유",
                    "owner_role": "퍼포먼스 마케터",
                    "due": "이번 주",
                    "status": "open",
                    "confidence": 0.92,
                    "source_seg_ids": [2, 3],
                    "source_quote": "아 그건 제가 챙길게요. 이번 주 안으로 초안 만들어볼게요.",
                },
                {
                    "title": "리타겟용 A/B 크리에이티브 시안 제작 (CTA 문구 테스트)",
                    "owner_role": "콘텐츠 디자이너",
                    "due": "다음 주 수요일",
                    "status": "open",
                    "confidence": 0.88,
                    "source_seg_ids": [4, 5, 6],
                    "source_quote": "다음 주 수요일까지는 시안 뽑을 수 있을 것 같아요.",
                },
                {
                    "title": "캠페인 제안서 취합 및 배포",
                    "owner_role": "팀장",
                    "due": "다음주 금요일 미팅 전",
                    "status": "open",
                    "confidence": 0.81,
                    "source_seg_ids": [9],
                    "source_quote": "제안서는 제가 취합해서 다음주 금요일 미팅 전에 돌릴게요.",
                },
                {
                    "title": "CTA 문구 공동 리뷰",
                    "owner_role": None,
                    "due": None,
                    "status": "open",
                    "confidence": 0.55,
                    "source_seg_ids": [11, 12],
                    "source_quote": "CTA 문구는 저랑 디자이너님이랑 같이 한번 보면 좋을 것 같아요.",
                },
            ]
        }
        return json.dumps(payload, ensure_ascii=False)
