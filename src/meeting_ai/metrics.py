"""LLM 호출 계측 — 호출 수 · 소요 시간 · 토큰 수 누적.

trade-off를 추측이 아니라 수치로 보여주기 위한 도구.
provider가 last_usage(토큰 사용량)를 노출하면 함께 집계한다.
주의: 시간(초)은 하드웨어 의존적이라 비교는 '호출 수·토큰 수'를 우선 본다.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field


@dataclass
class Metrics:
    calls: int = 0
    seconds: float = 0.0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    by_stage: dict = field(default_factory=dict)  # {"extract": {...}, "summary": {...}}

    def record(self, stage: str, elapsed: float, usage: dict | None) -> None:
        self.calls += 1
        self.seconds += elapsed
        pt = int((usage or {}).get("prompt", 0) or 0)
        ct = int((usage or {}).get("completion", 0) or 0)
        self.prompt_tokens += pt
        self.completion_tokens += ct
        s = self.by_stage.setdefault(stage, {"calls": 0, "seconds": 0.0,
                                              "prompt_tokens": 0, "completion_tokens": 0})
        s["calls"] += 1
        s["seconds"] += elapsed
        s["prompt_tokens"] += pt
        s["completion_tokens"] += ct

    def as_dict(self) -> dict:
        return {"calls": self.calls, "seconds": round(self.seconds, 1),
                "prompt_tokens": self.prompt_tokens,
                "completion_tokens": self.completion_tokens,
                "total_tokens": self.prompt_tokens + self.completion_tokens,
                "by_stage": self.by_stage}


# 현재 실행의 전역 누적기 (pipeline이 reset 후 사용)
CURRENT = Metrics()


def reset() -> None:
    global CURRENT
    CURRENT = Metrics()


class timer:
    """with timer('extract', provider): ... 형태로 호출 1건을 계측."""
    def __init__(self, stage: str, provider=None):
        self.stage = stage
        self.provider = provider

    def __enter__(self):
        self._t = time.perf_counter()
        return self

    def __exit__(self, *exc):
        elapsed = time.perf_counter() - self._t
        usage = getattr(self.provider, "last_usage", None) if self.provider else None
        CURRENT.record(self.stage, elapsed, usage)
        return False
