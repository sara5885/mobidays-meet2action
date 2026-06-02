"""transcript JSON → Utterance 리스트.

두 가지 transcript 포맷을 모두 흡수한다 (방어적 파싱):
  (A) 제공 실데이터: segment에 speaker(이름)+role 내장, speakers=[{name,role}], timestamp 없음
  (B) 일반 STT/diarization: speakers={code:role}, start/end 타임스탬프 존재
이 덕분에 로컬 Whisper 출력(가산점)으로 교체해도 같은 로더로 처리된다.
"""
from __future__ import annotations

import json
from pathlib import Path

from .schemas import Utterance

_SEG_KEYS = ("segments", "utterances", "results")
_TEXT_KEYS = ("text", "transcript", "content")
_SPEAKER_KEYS = ("speaker", "speaker_id", "spk", "speaker_name")
_ROLE_KEYS = ("role", "speaker_role")
_START_KEYS = ("start", "start_time", "begin")
_END_KEYS = ("end", "end_time", "stop")


def _pick(d: dict, keys, default=None):
    for k in keys:
        if k in d:
            return d[k]
    return default


def _build_speaker_role_map(speakers) -> dict[str, str]:
    """speakers 필드를 {화자키: 역할} 맵으로 정규화.

    - dict 형태: {"SPK_1": "팀장"}            → 그대로
    - list[dict] 형태: [{"name":"지훈","role":"마케팅 팀장"}] → {name: role}
    """
    mapping: dict[str, str] = {}
    if isinstance(speakers, dict):
        mapping = {str(k): str(v) for k, v in speakers.items()}
    elif isinstance(speakers, list):
        for sp in speakers:
            if isinstance(sp, dict):
                name = sp.get("name") or sp.get("id") or sp.get("speaker")
                role = sp.get("role") or name
                if name:
                    mapping[str(name)] = str(role)
    return mapping


def load_transcript(path: str | Path) -> tuple[dict, list[Utterance]]:
    """returns (meta, utterances)."""
    raw = json.loads(Path(path).read_text(encoding="utf-8"))

    meeting_id = raw.get("meeting_id") or Path(path).stem
    role_map = _build_speaker_role_map(raw.get("speakers", {}))

    segs = _pick(raw, _SEG_KEYS, [])
    utterances: list[Utterance] = []
    for i, s in enumerate(segs):
        spk = str(_pick(s, _SPEAKER_KEYS, "UNK"))
        # 역할 우선순위: 세그먼트 내장 role → speakers 맵 → 화자키 그대로
        role = _pick(s, _ROLE_KEYS) or role_map.get(spk) or spk
        utterances.append(
            Utterance(
                meeting_id=meeting_id,
                seg_id=int(s.get("id", s.get("line_no", i))),
                speaker_code=spk,            # 이름 또는 SPK_1
                speaker_role=str(role),       # 정규화된 역할명
                start=float(_pick(s, _START_KEYS, 0.0) or 0.0),
                end=float(_pick(s, _END_KEYS, 0.0) or 0.0),
                text=str(_pick(s, _TEXT_KEYS, "")).strip(),
            )
        )

    # 참석자 명단(회의 전 알려진 입력) — speakers 필드에서 정규화
    participants = [{"name": k, "role": v} for k, v in role_map.items()]

    meta = {
        "meeting_id": meeting_id,
        "title": raw.get("title", ""),
        "advertiser": raw.get("advertiser", ""),
        "date": raw.get("date", ""),
        "duration_sec": raw.get("duration_sec", 0),
        "language": raw.get("language", "ko"),
        "participants": participants,
    }
    return meta, utterances
