"""Gemini provider (선택/가산점). structured output(JSON) 강제.

GEMINI_API_KEY 가 있을 때만 동작. 키가 없으면 명확히 에러를 던진다.
주의: 외부 API이므로 실데이터(광고주 기밀) 전송 금지. 본 PoC는 가상 광고주
시나리오 검증 목적으로만 사용한다 (README/AI_USAGE.md 참고).

신뢰성 장치(상위 extract.py와 함께):
  - system_instruction 으로 role 고정
  - response_mime_type=application/json 으로 JSON 출력 강제
  - temperature 낮춤(0.2) → 추출 안정성
  - 출력 검증/재시도/환각필터는 extract.py 가 담당 (provider는 '문자열' 반환만 책임)
"""
from __future__ import annotations

from .. import config
from .base import LLMProvider


class GeminiProvider(LLMProvider):
    def __init__(self) -> None:
        if not config.GEMINI_API_KEY:
            raise RuntimeError(
                "GEMINI_API_KEY 가 설정되지 않았습니다. .env 에 LLM_PROVIDER=gemini 와 "
                "GEMINI_API_KEY=... 를 설정하세요 (https://aistudio.google.com 에서 발급)."
            )
        try:
            import google.generativeai as genai
        except ImportError as e:  # pragma: no cover
            raise RuntimeError(
                "google-generativeai 미설치. `pip install -r requirements.txt`"
            ) from e

        genai.configure(api_key=config.GEMINI_API_KEY)
        self._genai = genai
        self._model_name = config.GEMINI_MODEL
        self._cfg = {"response_mime_type": "application/json", "temperature": 0.2}

    def complete_json(self, system: str, user: str) -> str:
        model = self._genai.GenerativeModel(
            self._model_name,
            system_instruction=system,
            generation_config=self._cfg,
        )
        resp = model.generate_content(user)
        return resp.text
