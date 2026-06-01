"""Slack용 메시지 페이로드(JSON) 생성. 3.3 산출물 요구사항."""
from __future__ import annotations

from .schemas import ActionItem


def build_slack_payload(meeting_title: str, items: list[ActionItem]) -> dict:
    blocks = [
        {"type": "header", "text": {"type": "plain_text",
                                    "text": f"📋 회의 액션아이템 · {meeting_title}"}},
    ]
    for a in items:
        owner = a.owner_role or "❓미정"
        due = a.due or "기한 미정"
        flag = " ⚠️확인필요" if a.confidence < 0.6 else ""
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn",
                     "text": f"*{a.title}*\n담당: {owner} · 기한: {due} · "
                             f"신뢰도: {a.confidence:.2f}{flag}"},
        })
    return {"text": f"{meeting_title} 액션아이템 {len(items)}건", "blocks": blocks}
