"""Gemini 연결 점검. 키/모델/JSON 출력이 정상 동작하는지 빠르게 확인한다.

실행: PYTHONPATH=src python scripts/check_gemini.py
(.env 에 LLM_PROVIDER=gemini, GEMINI_API_KEY=... 설정 필요)
"""
from __future__ import annotations

import json

from meeting_ai import config
from meeting_ai.llm.gemini import GeminiProvider

SYSTEM = "너는 JSON만 출력하는 어시스턴트다."
USER = ('다음을 JSON으로 답하라. 스키마: {"ok": true, "echo": string}. '
        'echo에는 "연결성공"을 넣어라.')


def main() -> None:
    print(f"모델: {config.GEMINI_MODEL}")
    print(f"키 설정 여부: {'O' if config.GEMINI_API_KEY else 'X (미설정)'}")
    provider = GeminiProvider()
    raw = provider.complete_json(SYSTEM, USER)
    print("원시 응답:", raw)
    data = json.loads(raw)
    assert data.get("ok") is True
    print("✅ Gemini 연결 정상 — JSON 출력 확인됨")


if __name__ == "__main__":
    main()
