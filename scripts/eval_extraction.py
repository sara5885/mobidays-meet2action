"""추출 품질 평가 (precision / recall / F1 + 담당자 정확도).

방법:
  - gold(정답): mock.FIXTURES — 사람이 transcript를 직접 읽고 정리한 액션아이템.
  - candidate(예측): 실제 LLM(gemini/ollama)이 같은 transcript에서 추출한 결과.
  - 매칭: 제목 토큰 유사도(Jaccard) + 근거 발화(source_seg_ids) 겹침의 최대값이 임계 이상이면
    같은 액션아이템으로 간주(그리디 1:1 매칭).
  - 지표: precision=맞춘수/예측수, recall=맞춘수/정답수, F1. 매칭쌍에 대해 담당자 일치율.

  *주의*: gold가 1인분(저자 1명) 기준이라 절대값보다 '프롬프트/모델 변경 시 상대 비교'에 의미가 있다.
          4주 운영 1주차의 '기준선 측정' 도구로 그대로 재사용 가능.

실행: LLM_PROVIDER=ollama PYTHONPATH=src python scripts/eval_extraction.py
      (또는 --provider gemini)
"""
from __future__ import annotations

import argparse

from meeting_ai import config
from meeting_ai.keywords import tokenize
from meeting_ai.llm.mock import FIXTURES
from meeting_ai.preprocess import abbrev_glossary, to_chunks
from meeting_ai.schemas import ActionItem
from meeting_ai.transcript_loader import load_transcript

MATCH_THRESHOLD = 0.3


def _title_sim(a: str, b: str) -> float:
    ta, tb = set(tokenize(a)), set(tokenize(b))
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def _seg_overlap(a: list[int], b: list[int]) -> float:
    sa, sb = set(a), set(b)
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


def _score(g: dict, c: ActionItem) -> float:
    return max(_title_sim(g["title"], c.title),
               _seg_overlap(g.get("source_seg_ids", []), c.source_seg_ids))


def _match(gold: list[dict], cand: list[ActionItem]):
    """그리디 1:1 매칭. returns list of (gold, cand, score)."""
    pairs = []
    for gi, g in enumerate(gold):
        for ci, c in enumerate(cand):
            s = _score(g, c)
            if s >= MATCH_THRESHOLD:
                pairs.append((s, gi, ci))
    pairs.sort(reverse=True)
    used_g, used_c, matched = set(), set(), []
    for s, gi, ci in pairs:
        if gi in used_g or ci in used_c:
            continue
        used_g.add(gi); used_c.add(ci)
        matched.append((gold[gi], cand[ci], s))
    return matched


def evaluate(provider_name: str) -> None:
    config.LLM_PROVIDER = provider_name
    # 지연 import: provider 선택 후 생성
    from meeting_ai.extract import extract_from_chunk
    from meeting_ai.llm import get_provider
    provider = get_provider()

    tot_gold = tot_cand = tot_match = tot_owner_ok = 0
    print(f"=== 추출 품질 평가 (provider={provider_name}) ===\n")
    for mid in FIXTURES:
        path = config.RAW_DIR / f"{mid}.json"
        if mid == "nova-2026-05-28":
            path = config.RAW_DIR / "ko_meeting_3speakers.json"
        if not path.exists():
            continue
        meta, utts = load_transcript(path)
        chunks = to_chunks(utts)
        valid = {u.seg_id for u in utts}
        gloss = abbrev_glossary(utts)
        # 참석자 명단(담당자 후보)도 프롬프트에 주입 → owner 정확도 평가에 반영
        seen, roster = set(), []
        for u in utts:
            k = u.speaker_role or u.speaker_code
            if k and k not in seen:
                seen.add(k); roster.append({"name": u.speaker_code, "role": u.speaker_role})
        cand: list[ActionItem] = []
        for ch in chunks:
            cand.extend(extract_from_chunk(ch, valid, provider, gloss, roster))

        gold = FIXTURES[mid]
        matched = _match(gold, cand)
        owner_ok = sum(1 for g, c, _ in matched if (g.get("owner_role") or None) == c.owner_role)

        p = len(matched) / len(cand) if cand else 0.0
        r = len(matched) / len(gold) if gold else 0.0
        f1 = 2 * p * r / (p + r) if (p + r) else 0.0
        print(f"[{mid}] gold={len(gold)} cand={len(cand)} matched={len(matched)} "
              f"| P={p:.2f} R={r:.2f} F1={f1:.2f} | owner일치={owner_ok}/{len(matched)}")

        tot_gold += len(gold); tot_cand += len(cand)
        tot_match += len(matched); tot_owner_ok += owner_ok

    P = tot_match / tot_cand if tot_cand else 0.0
    R = tot_match / tot_gold if tot_gold else 0.0
    F1 = 2 * P * R / (P + R) if (P + R) else 0.0
    OA = tot_owner_ok / tot_match if tot_match else 0.0
    print(f"\n=== 전체 ===")
    print(f"gold={tot_gold} cand={tot_cand} matched={tot_match}")
    print(f"Precision={P:.3f}  Recall={R:.3f}  F1={F1:.3f}  담당자 정확도={OA:.3f}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--provider", default=config.LLM_PROVIDER,
                    help="평가에 쓸 provider (gemini|ollama). 기본 .env값")
    args = ap.parse_args()
    evaluate(args.provider)
