"""화자 매핑(diarization) + 같은 화자 인접 발화 병합.

설계 메모:
- 텍스트만으로 '조각 하나하나'에 화자를 맞히는 건 어렵다(단서 부족). 그래서 같은 화자가 이어 말한
  조각을 먼저 병합해 덩어리를 키운 뒤 매핑하면 정확도가 오른다 (가설 → eval_diarization.py로 검증).
- 화자를 모르는 상태에서 '같은 화자 추정'은 (1) 타임스탬프 gap, (2) 직전 조각의 문장 미완결(연결어미)로 판단.
- 매핑 자체는 LLM에 'id→화자' 매핑만 시켜 출력을 작게 유지(속도·원문보존).
"""
from __future__ import annotations

import json

from .extract import _complete_with_backoff, _strip_code_fence
from .llm.base import LLMProvider

DIAR_SYSTEM = (
    "너는 한국어 회의 받아쓰기의 화자 분리 전문가다. "
    "참석자 명단과 말투·문맥을 근거로 각 발화 번호를 말한 사람에게 매핑한다. JSON으로만 답한다."
)

# 직전 조각이 이런 연결어미로 끝나면 '아직 안 끝남' → 다음 조각과 같은 화자로 이어진다고 본다.
_CONNECTIVES = ("고", "서", "며", "면", "는데", "ㄴ데", "지만", "다가", "랑", "에", "구",
                "어", "아", "여", "하고", "거나", "든지", "라서", "면서", "려고")
# 종결로 끝나면 문장이 끝난 것 → 화자 전환 가능.
_ENDINGS = ("요", "다", "죠", "까", "네", "함", "음", "임", "죠.", "요.", "다.")


def is_unfinished(text: str) -> bool:
    """직전 조각이 문장 미완결(연결어미)인지. 애매하면 병합 쪽(True)으로."""
    t = text.rstrip(" .,!?…")
    if not t:
        return False
    if t.endswith(_ENDINGS):
        return False
    if t.endswith(_CONNECTIVES):
        return True
    return False  # 종결/연결 모두 아니면 보수적으로 끊는다


def merge_segments(segments: list[dict], max_gap: float = 1.5) -> list[dict]:
    """같은 화자가 이어 말한 조각 병합. segments: [{start,end,text}] (start/end 없으면 gap 무시).

    병합 조건: (앞 조각이 미완결) AND (gap < max_gap).  화자 정보는 사용하지 않는다.
    returns: [{start,end,text,seg_ids:[원본 인덱스...]}]
    """
    out: list[dict] = []
    for i, s in enumerate(segments):
        text = (s.get("text") or "").strip()
        start = s.get("start")
        end = s.get("end", start)
        if out:
            prev = out[-1]
            gap = (start - prev["end"]) if (start is not None and prev["end"] is not None) else 0.0
            if is_unfinished(prev["text"]) and gap < max_gap:
                prev["text"] = (prev["text"] + " " + text).strip()
                prev["end"] = end
                prev["seg_ids"].append(i)
                continue
        out.append({"start": start, "end": end, "text": text, "seg_ids": [i]})
    return out


def diarize_texts(texts: list[str], roster: list[dict],
                  provider: LLMProvider) -> list[str]:
    """발화 텍스트 리스트 → 각 발화의 화자 이름 리스트. roster=[{name,role}] (명단=닫힌 후보)."""
    if not texts:
        return []
    names = [r["name"] for r in roster] if roster else []
    numbered = "\n".join(f"#{i+1} {t}" for i, t in enumerate(texts))
    guide = (f"참석자: {', '.join(names)}\n각 발화 id를 말한 사람 이름으로 매핑하라. "
             f"speaker는 반드시 참석자 중 하나.\n" if names else
             "말투·문맥으로 화자를 구분해 '화자1','화자2'…로 매핑하라.\n")
    prompt = (guide + '아래 JSON으로만 답하라(텍스트 재출력 금지): '
              '{"assignments":[{"id":1,"speaker":"이름"}]}\n\n[발화]\n' + numbered)
    raw = _complete_with_backoff(provider, DIAR_SYSTEM, prompt)
    data = json.loads(_strip_code_fence(raw))
    spk = {int(a["id"]): str(a.get("speaker", "")).strip()
           for a in data.get("assignments", []) if "id" in a}
    default = names[0] if names else "화자1"
    return [spk.get(i + 1) or default for i in range(len(texts))]
