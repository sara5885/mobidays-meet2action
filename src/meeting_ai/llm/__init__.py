"""LLM provider 추상화. config.LLM_PROVIDER 에 따라 구현체를 고른다."""
from __future__ import annotations

from .. import config
from .base import LLMProvider
from .mock import MockProvider


def get_provider() -> LLMProvider:
    if config.LLM_PROVIDER == "gemini":
        from .gemini import GeminiProvider
        return GeminiProvider()
    if config.LLM_PROVIDER == "ollama":
        from .ollama import OllamaProvider
        return OllamaProvider()
    return MockProvider()
