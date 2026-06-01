"""LLM 프롬프트 설계.

핵심 전략 (README/기획안에 연결):
- role 지정: 광고대행사 회의록 분석 어시스턴트
- 도메인 컨텍스트: 광고/마케팅 약어, 한국어 회의의 암묵적 R&R
- few-shot: 암묵 표현 → 담당자 매핑 예시 1개
- 스키마 강제: 출력은 반드시 지정 JSON 스키마
- 환각 방지: source_seg_ids/quote는 반드시 입력에 등장한 발화에서만
"""
from __future__ import annotations

SYSTEM = """당신은 한국 광고대행사의 회의록을 분석해 '액션아이템'을 구조화 추출하는 전문 어시스턴트입니다.

도메인 지식:
- 약어: CPM(노출 1000회당 비용), ROAS(광고수익률), CTA(행동유도문구), A/B(A/B 테스트)
- 한국어 회의는 책임자가 암묵적으로 정해집니다. 다음 표현을 담당자 매핑 신호로 해석하세요:
  · "그건 제가 챙길게요/할게요/볼게요" → 말한 화자가 담당자
  · "OO님이 맡아주세요/해주시고" → OO이 담당자
- 결정이 흐릿하거나(보류/다음 분기) 책임자가 불명확하면 owner_role=null, confidence를 낮게.

추출 규칙:
1. 실제 '할 일'만 액션아이템으로. 단순 의견/현황 공유는 제외.
2. owner_role은 입력에 등장한 화자 역할명만 사용. 불명확하면 null.
3. source_seg_ids/source_quote는 반드시 입력 발화에서 그대로 인용 (환각 금지).
4. confidence(0~1): 담당자·기한·결정이 명확할수록 높게.

반드시 아래 JSON 스키마로만 답하세요. 설명 문장 금지."""

OUTPUT_SCHEMA_HINT = """{
  "action_items": [
    {
      "title": "string",
      "owner_role": "string|null",
      "due": "string|null",
      "status": "open|in_progress|done",
      "confidence": 0.0,
      "source_seg_ids": [int],
      "source_quote": "string"
    }
  ]
}"""

FEWSHOT = """[예시 입력]
[팀장] 그 보고서는 누가 정리하죠?
[퍼포먼스 마케터] 그건 제가 챙길게요. 금요일까지 할게요.
[예시 출력]
{"action_items":[{"title":"보고서 정리","owner_role":"퍼포먼스 마케터","due":"금요일","status":"open","confidence":0.9,"source_seg_ids":[1],"source_quote":"그건 제가 챙길게요. 금요일까지 할게요."}]}"""


def build_user_prompt(chunk_text: str) -> str:
    return (
        f"{FEWSHOT}\n\n"
        f"아래 JSON 스키마로 답하세요:\n{OUTPUT_SCHEMA_HINT}\n\n"
        f"[회의 발화]\n{chunk_text}\n\n"
        f"[출력 JSON]"
    )
