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


SUMMARY_SYSTEM = """당신은 한국 광고대행사의 회의록을 정리하는 전문 어시스턴트입니다.
회의 발화에서 '안건(다룬 주제)'과 '결정사항(최종 합의된 결론)'을 구분해 뽑습니다.

규칙:
- agenda: 이번 회의에서 다룬 주요 주제(논의 대상). 명사구로 간결하게.
- decisions: 논의 끝에 '확정된' 결론만. 흐릿하게 보류된 것('일단 두고 봐요', '잠정')은 결정에서 제외.
- summary: 회의 전체를 1~2문장으로.
- 발화에 없는 내용을 지어내지 말 것. 반드시 JSON으로만 답할 것."""

SUMMARY_SCHEMA = '{"summary": "string", "agenda": ["string"], "decisions": ["string"]}'


def build_summary_prompt(chunk_text: str, meeting_id: str | None = None) -> str:
    header = f"[meeting_id: {meeting_id}]\n[TASK: summary]\n" if meeting_id else "[TASK: summary]\n"
    return (
        f"{header}"
        f"아래 JSON 스키마로만 답하세요:\n{SUMMARY_SCHEMA}\n\n"
        f"[회의 발화]\n{chunk_text}\n\n[출력 JSON]"
    )


def build_user_prompt(
    chunk_text: str,
    glossary: dict[str, str] | None = None,
    meeting_id: str | None = None,
    roster: list[dict] | None = None,
) -> str:
    gloss = ""
    if glossary:
        lines = "\n".join(f"- {k}: {v}" for k, v in glossary.items())
        gloss = f"[이 회의에 등장한 약어 용어집]\n{lines}\n\n"
    header = f"[meeting_id: {meeting_id}]\n" if meeting_id else ""

    # 참석자 명단을 '닫힌 후보 집합'으로 명시 → owner를 이 중에서만 고르게(환각↓·정확도↑)
    roster_block = ""
    if roster:
        names = ", ".join(
            f"{s.get('role') or s.get('name')}" for s in roster if (s.get('role') or s.get('name'))
        )
        if names:
            roster_block = (
                f"[회의 참석자(담당자 후보)]\n{names}\n"
                f"※ owner_role은 반드시 위 후보 중 하나의 역할명으로만 지정하세요. "
                f"발화로 담당자를 특정할 수 없으면 null. 후보에 없는 이름을 만들지 마세요.\n\n"
            )

    return (
        f"{header}"
        f"{roster_block}"
        f"{gloss}"
        f"{FEWSHOT}\n\n"
        f"아래 JSON 스키마로 답하세요:\n{OUTPUT_SCHEMA_HINT}\n\n"
        f"발화 앞의 #숫자는 seg_id입니다. source_seg_ids에는 근거가 된 발화의 "
        f"이 #숫자를 그대로 넣으세요.\n\n"
        f"[회의 발화]\n{chunk_text}\n\n"
        f"[출력 JSON]"
    )
