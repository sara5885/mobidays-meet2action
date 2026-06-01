"""Mock provider. 실제 API 없이 파이프라인 전체를 검증하기 위한 결정적 응답.

제공된 실제 회의(노바드림 사전 정렬, 37발화)를 사람이 직접 읽고 정리한 '기대 액션아이템'을
돌려준다. 이 값은 precision/recall 측정 시 gold 참조로도 재사용할 수 있다.

실제 LLM 교체 시 이 클래스만 GeminiProvider로 바뀐다 (provider 추상화).
설계 의도가 드러나는 케이스:
  - 흐릿하게 보류된 결정(비주얼 톤은 '두고 봐요')은 액션아이템에서 제외 → 과추출 방지
  - 책임자 흔들린 항목(광고주 컨펌 '내가 받기로 했었나')은 confidence를 낮게
  - 잠정 결정(유튜브 2주차)은 owner 미상 + 낮은 confidence
"""
from __future__ import annotations

import json

from .base import LLMProvider

_MEETING_ACTION_ITEMS = [
    {
        "title": "픽셀 이중 집계 보정 후 전환 데이터 재집계·공유",
        "owner_role": "퍼포먼스 마케터", "due": "내일 오전", "status": "open",
        "confidence": 0.92, "source_seg_ids": [8, 9, 29, 30],
        "source_quote": "원인은 거의 본 것 같은데... 보정해서 다시 돌리는 데 하루 안에는 가능할 것 같아요.",
    },
    {
        "title": "비주얼 카드 순서 + 빈 슬롯 자리 카피 1차 정리",
        "owner_role": "콘텐츠 디자이너", "due": "내일 오전", "status": "open",
        "confidence": 0.85, "source_seg_ids": [17, 32],
        "source_quote": "비주얼 카드 순서랑 빈 슬롯 자리 카피는 일단 내일 오전까지 1차로 올려놓을게요.",
    },
    {
        "title": "필수 카피 3곳(메인 헤드라인·랜딩 첫 화면·CTA) 시안 작업",
        "owner_role": "콘텐츠 디자이너", "due": "금요일", "status": "open",
        "confidence": 0.7, "source_seg_ids": [23, 32],
        "source_quote": "메인 헤드라인, 랜딩 첫 화면, CTA 버튼. 이 세 개는 진짜 무조건 손봐야 해요.",
    },
    {
        "title": "캠페인 세트 분리 (광고주 컨펌 선행 필요)",
        "owner_role": "퍼포먼스 마케터", "due": "수요일 오전", "status": "open",
        "confidence": 0.82, "source_seg_ids": [18, 34],
        "source_quote": "캠페인 세트 분리는 제가 수요일 오전까지 해놓을게요.",
    },
    {
        "title": "CTA 변경에 따른 전환 추적 이벤트 재점검 (픽셀 정리 후)",
        "owner_role": "퍼포먼스 마케터", "due": None, "status": "open",
        "confidence": 0.68, "source_seg_ids": [24, 25],
        "source_quote": "그건 제가 같이 챙길게요. 근데 그것도 결국 픽셀 정리 끝나고 나서가 깔끔하긴 해요.",
    },
    {
        "title": "기존 A/B 종료 후 새 카피로 재세팅 + 광고주 보고 시 비교기간 단절 사전 고지",
        "owner_role": "퍼포먼스 마케터", "due": None, "status": "open",
        "confidence": 0.66, "source_seg_ids": [33],
        "source_quote": "기존 A/B는 한 번 닫고, 바뀐 카피로 다시 세팅해야 해요. 그건 제가 정리할게요.",
    },
    {
        "title": "신제품 누끼 컷 광고주 담당자에게 재요청/푸쉬 (미수령 시 임시 컷)",
        "owner_role": "마케팅 팀장", "due": "오늘 중", "status": "open",
        "confidence": 0.75, "source_seg_ids": [28, 31],
        "source_quote": "그건 내가 담당자한테 한 번 더 푸쉬할게요. 오늘 안에 안 오면 임시 컷으로라도 가야 하니까.",
    },
    {
        "title": "캠페인 세트 운영 변경 광고주 컨펌 받기",
        "owner_role": "마케팅 팀장", "due": None, "status": "open",
        "confidence": 0.58, "source_seg_ids": [19, 35],
        "source_quote": "광고주 컨펌은 내가 받기로 한 거 맞죠? 아까 그렇게 갔죠?",
    },
    {
        "title": "유튜브 2주차 옵션으로 제안서에 포함 (잠정 결정)",
        "owner_role": None, "due": None, "status": "open",
        "confidence": 0.45, "source_seg_ids": [16, 35],
        "source_quote": "유튜브는 2주차쯤 붙이는 걸로 잠정적으로, 광고주한테는 옵션으로 같이 들고 가는 걸로 합시다.",
    },
]


class MockProvider(LLMProvider):
    def complete_json(self, system: str, user: str) -> str:
        return json.dumps({"action_items": _MEETING_ACTION_ITEMS}, ensure_ascii=False)
