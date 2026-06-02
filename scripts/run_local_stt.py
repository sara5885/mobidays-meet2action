"""로컬 Whisper STT + (LLM 보조) 화자 정규화 → 파이프라인이 먹는 transcript JSON 생성.
(가산점: 음성에 직접 STT 적용 + 화자 분리 자체 처리)

흐름:
  mp3 ─► Whisper(local, 무료) ─► 타임스탬프 발화 ─► LLM 화자 매핑 ─► transcript JSON

설계 메모:
  - Whisper는 받아쓰기만 하고 화자 분리는 안 한다. 정식 diarization(pyannote 등)은
    설치가 무겁고 본 PoC 범위를 넘어, '경량 화자 매핑'을 LLM으로 처리한다.
  - 참석자 명단을 주면 그 명단에 매핑하고(--speakers), 없으면 LLM이 발화 수·말투로
    화자 수와 역할을 추론한다(이름 불명 시 '화자1/화자2…').
  - 출력은 제공 transcript와 '동일한 스키마'라 그대로 loader/pipeline에 투입된다.
  - LLM 호출은 우리 provider 추상화 + 429 백오프 재사용 (mock/gemini/ollama 모두 가능).

사용:
  make stt                                  # 기본 샘플 mp3
  make stt FILE=data/raw/다른회의.mp3        # 다른 음성 파일
  (직접)  PYTHONPATH=src python scripts/run_local_stt.py <audio.mp3> [--advertiser 이름]
이후:    PYTHONPATH=src python -m meeting_ai.pipeline data/stt/<파일명>.json
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import ssl
import sys

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../src")))
ssl._create_default_https_context = ssl._create_unverified_context  # mac 모델 다운로드 SSL 우회

# Whisper는 ffmpeg 실행파일이 필요. make 하위 프로세스에 brew 경로가 안 넘어오는
# 경우가 있어 Homebrew 기본 경로(Apple Silicon / Intel)를 PATH에 직접 추가.
for _p in ("/opt/homebrew/bin", "/usr/local/bin"):
    if _p not in os.environ.get("PATH", ""):
        os.environ["PATH"] = _p + os.pathsep + os.environ.get("PATH", "")

from meeting_ai import config  # noqa: E402
from meeting_ai.extract import _complete_with_backoff, _strip_code_fence  # noqa: E402

DEFAULT_AUDIO = "data/raw/ko_meeting_3speakers_4min_faster.mp3"

SYSTEM = (
    "너는 한국어 회의 받아쓰기의 화자 분리(diarization) 전문가다. "
    "타임스탬프와 말투·문맥을 근거로 발화를 화자별로 구분하고, 각 화자의 역할을 추론한다. "
    "반드시 지정된 JSON 스키마로만 답한다."
)


def _diar_prompt(numbered: str, hint_speakers: list[dict] | None) -> str:
    # 핵심: 텍스트를 다시 출력시키지 않고 'id→화자' 매핑만 받는다(출력↓·속도↑·원문 보존).
    if hint_speakers:
        names = [s["name"] for s in hint_speakers]
        guide = (f"참석자: {', '.join(names)}\n"
                 f"각 발화 번호(id)를 말한 사람의 이름으로 매핑하라. "
                 f"speaker는 반드시 참석자 중 하나여야 한다.\n")
    else:
        guide = ("참석자 명단이 없다. 말투·문맥으로 서로 다른 화자를 구분해 "
                 "이름이 드러나면 그 이름을, 아니면 '화자1','화자2'…로 매핑하라.\n")
    schema = '{"assignments": [{"id": 1, "speaker": "이름"}]}'
    return (
        f"{guide}"
        f"아래 JSON 스키마로만, 모든 id에 대해 답하라(텍스트는 다시 쓰지 마라):\n{schema}\n\n"
        f"[발화 목록]\n{numbered}"
    )


def _parse_speakers(spec: str) -> list[dict]:
    """--speakers "재현:마케팅 팀장,유나:퍼포먼스 마케터" → [{"name","role"}]"""
    roster = []
    for part in spec.split(","):
        if ":" in part:
            name, role = part.split(":", 1)
            roster.append({"name": name.strip(), "role": role.strip()})
        elif part.strip():
            roster.append({"name": part.strip(), "role": "참석자"})
    return roster


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("audio", nargs="?", default=DEFAULT_AUDIO,
                    help="음성 파일(mp3) 또는 Whisper 받아쓰기 텍스트(.txt)")
    ap.add_argument("--advertiser", default="", help="광고주명(메타데이터)")
    ap.add_argument("--title", default="", help="회의 제목(메타데이터)")
    ap.add_argument("--speakers", default="", help='참석자 명단 "이름:역할,이름:역할"')
    args = ap.parse_args()

    audio = args.audio
    if not os.path.exists(audio):
        print(f"❌ 입력 파일 없음: {audio}")
        return
    stem = re.sub(r"[^0-9A-Za-z가-힣_-]", "_", os.path.splitext(os.path.basename(audio))[0])
    meeting_id = f"{stem}-stt"
    out = f"data/stt/{stem}.json"
    os.makedirs("data/stt", exist_ok=True)

    # 참석자 명단(담당자 후보): --speakers 우선, 없으면 기본 샘플만 알려진 명단, 그 외엔 LLM 추론
    hint = _parse_speakers(args.speakers) if args.speakers else None
    if hint is None and os.path.abspath(audio) == os.path.abspath(DEFAULT_AUDIO):
        hint = [{"name": "지훈", "role": "마케팅 팀장"},
                {"name": "수아", "role": "퍼포먼스 마케터"},
                {"name": "채린", "role": "콘텐츠 디자이너"}]

    # 입력이 Whisper 원본 JSON이면(segments에 text/start 있고 speaker 없음) 받아쓰기로 사용.
    # → Whisper가 만든 JSON에 우리가 speaker/role 필드를 채워 넣는 흐름을 그대로 재현.
    if audio.endswith(".json"):
        wj = json.load(open(audio, encoding="utf-8"))
        segs_in = wj.get("segments", [])
        result = {"segments": [{"start": float(s.get("start", i)),
                                "text": str(s.get("text", "")).strip()}
                               for i, s in enumerate(segs_in)]}
        lines = "\n".join(f"[{s['start']:.1f}s] {s['text']}" for s in result["segments"])
        print(f"▶ 1/2 Whisper JSON 입력 사용: {os.path.basename(audio)} ({len(segs_in)} 세그먼트)")
    # 입력이 .txt면 Whisper를 건너뛰고 raw 받아쓰기로 간주 (mp3 없이 diarization 테스트용)
    elif audio.endswith(".txt"):
        raw_lines = [ln.strip() for ln in open(audio, encoding="utf-8") if ln.strip()]
        seg_texts = [re.sub(r"^\[?[0-9.]+s?\]?\s*", "", ln) for ln in raw_lines]
        result = {"segments": [{"start": float(i), "text": t} for i, t in enumerate(seg_texts)]}
        lines = "\n".join(f"[{i}.0s] {t}" for i, t in enumerate(seg_texts))
        print(f"▶ 1/2 텍스트 입력 사용(가상 STT 결과): {os.path.basename(audio)} ({len(seg_texts)} 줄)")
    else:
        import whisper
        print(f"▶ 1/3 Whisper('{config.WHISPER_MODEL}') 로드…")
        model = whisper.load_model(config.WHISPER_MODEL)
        print(f"▶ 2/3 음성 → 텍스트 추출… ({os.path.basename(audio)})")
        result = model.transcribe(audio, language="ko")
        lines = "\n".join(
            f"[{s['start']:.1f}s] {s['text'].strip()}" for s in result["segments"]
        )

    meta = {
        "meeting_id": meeting_id,
        "title": args.title or f"{stem} (Whisper STT)",
        "advertiser": args.advertiser,
        "date": dt.date.today().isoformat(),
        "language": "ko",
    }

    if config.LLM_PROVIDER == "mock":
        print("💡 mock 모드 — 화자 매핑 생략, 단일 화자로 저장(시연용).")
        segs = [{"id": i + 1, "speaker": "화자1", "role": "참석자",
                 "text": s["text"].strip()} for i, s in enumerate(result["segments"])]
        _save(out, {**meta, "speakers": [{"name": "화자1", "role": "참석자"}], "segments": segs})
        return

    print(f"▶ 3/3 화자 정규화 (provider={config.LLM_PROVIDER})…")
    from meeting_ai.llm import get_provider
    provider = get_provider()
    texts = [s["text"].strip() for s in result["segments"]]
    numbered = "\n".join(f"#{i+1} {t}" for i, t in enumerate(texts))
    role_by_name = {s["name"]: s["role"] for s in (hint or [])}
    try:
        raw = _complete_with_backoff(provider, SYSTEM, _diar_prompt(numbered, hint))
        data = json.loads(_strip_code_fence(raw))
        # id→speaker 매핑만 받아서 우리 텍스트와 합친다 (원문 보존)
        spk_by_id = {int(a["id"]): str(a.get("speaker", "")).strip()
                     for a in data.get("assignments", []) if "id" in a}
        segs = []
        for i, t in enumerate(texts):
            spk = spk_by_id.get(i + 1) or "화자1"
            segs.append({"id": i + 1, "speaker": spk,
                         "role": role_by_name.get(spk, "참석자"), "text": t})
        speakers = (hint or _infer_speakers(segs))
        _save(out, {**meta, "speakers": speakers, "segments": segs})
        mapped = sum(1 for s in segs if s["speaker"] != "화자1")
        print(f"🎉 STT + 화자 매핑 완료 ({len(segs)} 발화, 매핑 {mapped}건, 화자 {len(speakers)}명)")
    except Exception as e:
        print(f"⚠️ 화자 매핑 실패({e}) — 받아쓰기 원문만 단일 화자로 저장(폴백).")
        segs = [{"id": i + 1, "speaker": "화자1", "role": "참석자", "text": t}
                for i, t in enumerate(texts)]
        _save(out, {**meta, "speakers": [{"name": "화자1", "role": "참석자"}], "segments": segs})


def _infer_speakers(segs: list[dict]) -> list[dict]:
    seen = {}
    for s in segs:
        seen.setdefault(s.get("speaker", "화자1"), s.get("role", "참석자"))
    return [{"name": n, "role": r} for n, r in seen.items()]


def _save(out: str, obj: dict) -> None:
    with open(out, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
    print(f"✅ 저장 → {out}")
    print(f"   적재: PYTHONPATH=src python -m meeting_ai.pipeline {out}")


if __name__ == "__main__":
    main()
