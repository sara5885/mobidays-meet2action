"""Mock provider. 실제 API 없이 파이프라인 전체를 검증하기 위한 결정적 응답.

각 회의(meeting_id)별로 사람이 직접 정리한 '기대 액션아이템' 픽스처를 돌려준다.
provider 인터페이스(complete_json)는 그대로 두고, 프롬프트 헤더의 [meeting_id: ...]를
파싱해 해당 회의의 픽스처를 선택한다. 실제 LLM 교체 시 이 클래스만 GeminiProvider로 바뀐다.

설계 의도가 드러나는 케이스:
  - 흐릿하게 보류된 결정('일단 두고 봐요')은 액션아이템에서 제외 → 과추출 방지
  - 책임자 흔들린 항목('내가 받기로 했었나')은 confidence를 낮게
  - 잠정 결정은 owner 미상 + 낮은 confidence
이 픽스처는 precision/recall 측정 시 gold 참조로도 재사용 가능하다.
"""
from __future__ import annotations

import json
import re

from .base import LLMProvider

FIXTURES: dict[str, list[dict]] = {
    # === 제공된 실데이터 (37발화) ===
    "nova-2026-05-28": [
        {"title": "픽셀 이중 집계 보정 후 전환 데이터 재집계·공유", "owner_role": "퍼포먼스 마케터",
         "due": "내일 오전", "status": "open", "confidence": 0.92, "source_seg_ids": [8, 9, 29, 30],
         "source_quote": "보정해서 다시 돌리는 데 하루 안에는 가능할 것 같아요."},
        {"title": "비주얼 카드 순서 + 빈 슬롯 자리 카피 1차 정리", "owner_role": "콘텐츠 디자이너",
         "due": "내일 오전", "status": "open", "confidence": 0.85, "source_seg_ids": [17, 32],
         "source_quote": "비주얼 카드 순서랑 빈 슬롯 자리 카피는 일단 내일 오전까지 1차로 올려놓을게요."},
        {"title": "필수 카피 3곳(메인 헤드라인·랜딩 첫 화면·CTA) 시안 작업", "owner_role": "콘텐츠 디자이너",
         "due": "금요일", "status": "open", "confidence": 0.7, "source_seg_ids": [23, 32],
         "source_quote": "메인 헤드라인, 랜딩 첫 화면, CTA 버튼. 이 세 개는 진짜 무조건 손봐야 해요."},
        {"title": "캠페인 세트 분리 (광고주 컨펌 선행 필요)", "owner_role": "퍼포먼스 마케터",
         "due": "수요일 오전", "status": "open", "confidence": 0.82, "source_seg_ids": [18, 34],
         "source_quote": "캠페인 세트 분리는 제가 수요일 오전까지 해놓을게요."},
        {"title": "CTA 변경에 따른 전환 추적 이벤트 재점검 (픽셀 정리 후)", "owner_role": "퍼포먼스 마케터",
         "due": None, "status": "open", "confidence": 0.68, "source_seg_ids": [24, 25],
         "source_quote": "그건 제가 같이 챙길게요. 픽셀 정리 끝나고 나서가 깔끔하긴 해요."},
        {"title": "기존 A/B 종료 후 새 카피로 재세팅 + 보고 시 비교기간 단절 사전 고지", "owner_role": "퍼포먼스 마케터",
         "due": None, "status": "open", "confidence": 0.66, "source_seg_ids": [33],
         "source_quote": "기존 A/B는 한 번 닫고, 바뀐 카피로 다시 세팅해야 해요. 그건 제가 정리할게요."},
        {"title": "신제품 누끼 컷 광고주 담당자에게 재요청/푸쉬", "owner_role": "마케팅 팀장",
         "due": "오늘 중", "status": "open", "confidence": 0.75, "source_seg_ids": [28, 31],
         "source_quote": "그건 내가 담당자한테 한 번 더 푸쉬할게요."},
        {"title": "캠페인 세트 운영 변경 광고주 컨펌 받기", "owner_role": "마케팅 팀장",
         "due": None, "status": "open", "confidence": 0.58, "source_seg_ids": [19, 35],
         "source_quote": "광고주 컨펌은 내가 받기로 한 거 맞죠?"},
        {"title": "유튜브 2주차 옵션으로 제안서에 포함 (잠정 결정)", "owner_role": None,
         "due": None, "status": "open", "confidence": 0.45, "source_seg_ids": [16, 35],
         "source_quote": "유튜브는 2주차쯤 붙이는 걸로 잠정적으로."},
    ],
    # === 합성 회의 1: 노바드림 이전 주차 (픽셀 이슈 반복) ===
    "nova-2026-05-06": [
        {"title": "픽셀 이중 집계 원인 정리·공유", "owner_role": "퍼포먼스 마케터",
         "due": "내일", "status": "open", "confidence": 0.9, "source_seg_ids": [2, 3, 4],
         "source_quote": "픽셀 이벤트 중복 발화되는지 보고 내일까지 정리해서 공유드릴게요."},
        {"title": "5월 캠페인 예산 배분 최종본 작성", "owner_role": "콘텐츠 디자이너",
         "due": "금요일", "status": "open", "confidence": 0.85, "source_seg_ids": [5, 6],
         "source_quote": "예산안은 채린님이 이번 주 금요일까지 최종본 올려주시고."},
        {"title": "리타겟 타겟 세그먼트 누락 재설정", "owner_role": "퍼포먼스 마케터",
         "due": "다음 주 화요일", "status": "open", "confidence": 0.8, "source_seg_ids": [7, 8, 9],
         "source_quote": "리타겟 세그먼트 누락된 거 있어서 그것도 제가 다시 잡을게요."},
        {"title": "CTA 카피 공동 리뷰", "owner_role": None,
         "due": None, "status": "open", "confidence": 0.5, "source_seg_ids": [10, 11],
         "source_quote": "CTA 카피는 다 같이 한번 보는 걸로."},
    ],
    # === 합성 회의 2: 글로우코스메틱 (누끼/픽셀 이슈 반복) ===
    "glow-2026-05-13": [
        {"title": "신제품 누끼 컷 광고주 담당자에게 재요청/푸쉬", "owner_role": "마케팅 팀장",
         "due": "오늘 중", "status": "open", "confidence": 0.82, "source_seg_ids": [2, 3, 4, 5],
         "source_quote": "그건 내가 담당자한테 다시 푸쉬할게요. 오늘 중으로."},
        {"title": "인플루언서 협찬 리스트업·공유", "owner_role": "퍼포먼스 마케터",
         "due": "내일", "status": "open", "confidence": 0.85, "source_seg_ids": [6],
         "source_quote": "인플루언서 협찬 건은 제가 리스트업 해서 내일까지 공유할게요."},
        {"title": "전환 추적 픽셀 점검", "owner_role": "퍼포먼스 마케터",
         "due": "수요일", "status": "open", "confidence": 0.78, "source_seg_ids": [7, 8],
         "source_quote": "이번에도 픽셀 쪽을 한 번 점검해야 할 것 같아요. 제가 수요일까지 검수할게요."},
        {"title": "랜딩 페이지 카피 톤 시안 작업", "owner_role": "콘텐츠 디자이너",
         "due": "금요일", "status": "open", "confidence": 0.8, "source_seg_ids": [9, 10],
         "source_quote": "랜딩 카피 시안은 채린님이 금요일까지 잡아주세요."},
    ],
    # === 합성 회의 3: 페이나우 (카피/CTA/A-B 이슈) ===
    "paynow-2026-05-20": [
        {"title": "헤드라인 A/B 테스트 최종 결과 정리·공유", "owner_role": "퍼포먼스 마케터",
         "due": "다음 주 월요일", "status": "open", "confidence": 0.85, "source_seg_ids": [2, 3, 4],
         "source_quote": "조금만 더 돌려보고 다음 주 월요일에 제가 최종 결과 정리해서 공유할게요."},
        {"title": "CTA 버튼 카피 시안 작업", "owner_role": "콘텐츠 디자이너",
         "due": "수요일", "status": "open", "confidence": 0.82, "source_seg_ids": [5, 6],
         "source_quote": "CTA 카피 시안은 채린님이 수요일까지 잡아주시고."},
        {"title": "CTA 변경에 따른 전환 추적 이벤트 재점검", "owner_role": "퍼포먼스 마케터",
         "due": None, "status": "open", "confidence": 0.7, "source_seg_ids": [6, 7],
         "source_quote": "CTA 바꾸면 전환 추적 이벤트도 다시 봐야 해요. 그건 제가 같이 챙길게요."},
        {"title": "앱 프로모션 광고주 컨펌 받기", "owner_role": "마케팅 팀장",
         "due": "목요일", "status": "open", "confidence": 0.65, "source_seg_ids": [10, 11],
         "source_quote": "그건 내가 받을게요. 목요일 미팅 때 같이 정리하죠."},
    ],
}

_MID_RE = re.compile(r"\[meeting_id:\s*([^\]]+)\]")


class MockProvider(LLMProvider):
    def complete_json(self, system: str, user: str) -> str:
        m = _MID_RE.search(user)
        meeting_id = m.group(1).strip() if m else ""
        # 회의록 정리 요청이면 요약 형태로 응답 (결정사항은 해당 회의 액션아이템 제목 활용)
        if "[TASK: summary]" in user:
            items = FIXTURES.get(meeting_id, [])
            decisions = [it["title"] for it in items
                         if it.get("owner_role") and it.get("confidence", 0) >= 0.6][:5]
            return json.dumps({
                "summary": "광고 캠페인 사전 정렬 회의 (mock 요약).",
                "agenda": ["지난 성과 리뷰", "매체/예산 배분", "크리에이티브·카피"],
                "decisions": decisions or ["주요 결정사항은 액션아이템 참조"],
            }, ensure_ascii=False)
        items = FIXTURES.get(meeting_id, [])
        return json.dumps({"action_items": items}, ensure_ascii=False)
