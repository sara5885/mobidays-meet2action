"""화자 매핑(diarization) 정확도 측정.

정답: 동봉 transcript(ko_meeting_3speakers.json)의 발화별 화자(name)를 ground-truth로 사용.
방법: 화자 라벨을 가린 발화를 LLM에 매핑시키고 정답과 발화별로 비교 → 정확도(%).

두 조건 비교 (가설: 덩어리가 클수록 화자 매핑이 쉬워진다):
  ① sentence  — 정답 발화를 그대로(문장 단위) 매핑
  ② fragment  — 각 발화를 쉼표/연결어미로 잘게 쪼갠 뒤 매핑 (Whisper 과분할 흉내)
fragment 조각은 부모 발화의 화자를 정답으로 상속 → 동일 ground-truth로 공정 비교.

실행: LLM_PROVIDER=ollama PYTHONPATH=src python scripts/eval_diarization.py [--provider ollama]
"""
from __future__ import annotations

import argparse
import re

from meeting_ai import config
from meeting_ai.transcript_loader import load_transcript

GOLD = "data/raw/ko_meeting_3speakers.json"


def _fragment(text: str) -> list[str]:
    """발화를 쉼표/연결어미 경계로 잘게 분할 (Whisper 과분할 흉내)."""
    parts = re.split(r"(?<=[,])\s+|(?<=고)\s+|(?<=서)\s+|(?<=는데)\s+", text)
    return [p.strip() for p in parts if len(p.strip()) >= 2] or [text]


def _accuracy(pred: list[str], gold: list[str]) -> float:
    ok = sum(1 for p, g in zip(pred, gold) if p == g)
    return ok / len(gold) if gold else 0.0


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--provider", default=config.LLM_PROVIDER)
    args = ap.parse_args()
    config.LLM_PROVIDER = args.provider
    from meeting_ai.diarize import diarize_texts
    from meeting_ai.llm import get_provider
    provider = get_provider()

    meta, utts = load_transcript(GOLD)
    roster = meta.get("participants") or []
    gold_speakers = [u.speaker_code for u in utts]
    texts = [u.text for u in utts]
    print(f"=== 화자 매핑 정확도 (provider={args.provider}) ===")
    print(f"참석자: {[r['name'] for r in roster]} | 발화 {len(texts)}개\n")

    # ① 문장 단위
    pred_s = diarize_texts(texts, roster, provider)
    acc_s = _accuracy(pred_s, gold_speakers)
    print(f"① 문장 단위(병합 상태)  : {acc_s*100:.1f}%  ({sum(p==g for p,g in zip(pred_s,gold_speakers))}/{len(texts)})")

    # ② 조각 단위 (각 발화를 쪼개고, 화자는 부모 발화 상속)
    frag_texts, frag_gold = [], []
    for t, g in zip(texts, gold_speakers):
        for piece in _fragment(t):
            frag_texts.append(piece); frag_gold.append(g)
    pred_f = diarize_texts(frag_texts, roster, provider)
    acc_f = _accuracy(pred_f, frag_gold)
    print(f"② 조각 단위(과분할)     : {acc_f*100:.1f}%  ({sum(p==g for p,g in zip(pred_f,frag_gold))}/{len(frag_texts)})")

    print(f"\n→ 문장 단위가 {(acc_s-acc_f)*100:+.1f}%p "
          f"{'높음 → 병합 후 매핑이 유리(가설 지지)' if acc_s>acc_f else '차이 미미/낮음'}")

    # 틀린 케이스 일부
    print("\n[문장 단위 오답 예시]")
    n = 0
    for t, p, g in zip(texts, pred_s, gold_speakers):
        if p != g and n < 5:
            print(f"  정답 {g} / 예측 {p}: {t[:40]}"); n += 1


if __name__ == "__main__":
    main()
