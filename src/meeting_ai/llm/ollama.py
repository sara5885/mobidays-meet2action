"""Ollama 로컬 LLM provider (무료·무제한·온프레미스).

장점:
  - rate limit 없음 (로컬 실행)
  - 데이터가 외부로 나가지 않음 → 과제의 '외부 유출 금지' 제약을 완전히 충족
    (기획안의 '실서비스는 사내 LLM 전제'를 실제로 구현)

사전 준비:
  1) https://ollama.com 에서 Ollama 설치 (또는 `brew install ollama`)
  2) 모델 받기: `ollama pull qwen2.5:7b`  (한국어·JSON에 강한 편)
  3) .env: LLM_PROVIDER=ollama, OLLAMA_MODEL=qwen2.5:7b

의존성 추가 없이 표준 라이브러리(urllib)로 HTTP 호출한다.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request

from .. import config
from .base import LLMProvider


class OllamaProvider(LLMProvider):
    def __init__(self) -> None:
        self._url = config.OLLAMA_HOST.rstrip("/") + "/api/generate"
        self._model = config.OLLAMA_MODEL

    def complete_json(self, system: str, user: str) -> str:
        payload = {
            "model": self._model,
            "system": system,
            "prompt": user,
            "format": "json",      # JSON 출력 강제
            "stream": False,
            "options": {"temperature": 0.2},
        }
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            self._url, data=data, headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=180) as resp:
                body = json.loads(resp.read().decode("utf-8"))
        except urllib.error.URLError as e:
            raise RuntimeError(
                f"Ollama 호출 실패({e}). `ollama serve` 가 떠 있는지, "
                f"모델({self._model})을 `ollama pull` 했는지 확인하세요."
            ) from e
        return body.get("response", "")
