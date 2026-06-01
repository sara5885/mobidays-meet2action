"""provider 인터페이스. 입력=프롬프트, 출력=원시 JSON 문자열."""
from __future__ import annotations

from abc import ABC, abstractmethod


class LLMProvider(ABC):
    @abstractmethod
    def complete_json(self, system: str, user: str) -> str:
        """JSON 형식의 응답 '문자열'을 반환한다. 파싱/검증은 호출측 책임."""
        raise NotImplementedError
