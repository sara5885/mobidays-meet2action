"""Gemini provider (선택/가산점). structured output(JSON) 강제.

GEMINI_API_KEY 가 있을 때만 동작. 키가 없으면 명확히 에러를 던진다.
주의: 외부 API이므로 실데이터(광고주 기밀) 전송 금지. 본 PoC는 가상 광고주
시나리오 검증 목적으로만 사용한다 (README/AI_USAGE.md 참고).
"""
from __future__ import annotations

from .. import config
from .base import LLMProvider


class GeminiProvider(LLMProvider):
    def __init__(self) -> None:
        if not config.GEMINI_API_KEY:
            raise RuntimeError("GEMINI_API_KEY 가 설정되지 않았습니다 (.env 확인).")
        import google.generativeai as genai

        genai.configure(api_key=config.GEMINI_API_KEY)
        # JSON 강제: response_mime_type=application/json
        self._model = genai.GenerativeModel(
            config.GEMINI_MODEL,
            generation_config={"response_mime_type": "application/json",
                               "temperature": 0.2},
        )

    def complete_json(self, system: str, user: str) -> str:
        resp = self._model.generate_content([system, user])
        return resp.text
