"""transcript JSON → Utterance 리스트.

제공 transcript의 정확한 필드명은 아직 확인 전이므로, 흔한 키들을 모두 흡수하도록
방어적으로 파싱한다. 데이터 수령 후 _SPEAKER_KEYS 등만 맞추면 된다.
"""
from __future__ import annotations

import json
from pathlib import Path

from .schemas import Utterance

_SEG_KEYS = ("segments", "utterances", "results")
_TEXT_KEYS = ("text", "transcript", "content")
_SPEAKER_KEYS = ("speaker", "speaker_id", "spk")
_START_KEYS = ("start", "start_time", "begin")
_END_KEYS = ("end", "end_time", "stop")


def _pick(d: dict, keys, default=None):
    for k in keys:
        if k in d:
            return d[k]
    return default


def load_transcript(path: str | Path) -> tuple[dict, list[Utterance]]:
    """returns (meta, utterances). meta는 meeting 메타데이터."""
    raw = json.loads(Path(path).read_text(encoding="utf-8"))

    meeting_id = raw.get("meeting_id") or Path(path).stem
    speakers = raw.get("speakers", {})  # {"SPK_1": "팀장", ...}

    segs = _pick(raw, _SEG_KEYS, [])
    utterances: list[Utterance] = []
    for i, s in enumerate(segs):
        spk = str(_pick(s, _SPEAKER_KEYS, "UNK"))
        utterances.append(
            Utterance(
                meeting_id=meeting_id,
                seg_id=int(s.get("id", i)),
                speaker_code=spk,
                speaker_role=speakers.get(spk, spk),  # 화자 정규화: 코드 → 역할명
                start=float(_pick(s, _START_KEYS, 0.0) or 0.0),
                end=float(_pick(s, _END_KEYS, 0.0) or 0.0),
                text=str(_pick(s, _TEXT_KEYS, "")).strip(),
            )
        )

    meta = {
        "meeting_id": meeting_id,
        "title": raw.get("title", ""),
        "advertiser": raw.get("advertiser", ""),
        "date": raw.get("date", ""),
        "duration_sec": raw.get("duration_sec", 0),
    }
    return meta, utterances
