"""End-to-end 오케스트레이터.

  transcript JSON → 정제 → 청크 → DuckDB 적재 → LLM 추출 → 적재 → Slack 페이로드

멱등성: 같은 meeting_id 재실행 시 DELETE 후 INSERT 라 중복이 생기지 않는다.
사용: python -m meeting_ai.pipeline data/raw/ko_meeting_3speakers.json
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

from . import config, db
from .extract import extract_from_chunk
from .llm import get_provider
from .preprocess import abbrev_glossary, to_chunks
from .schemas import ActionItem
from .slack_payload import build_slack_payload
from .transcript_loader import load_transcript

DEFAULT_INPUT = "data/raw/ko_meeting_3speakers.json"


def _dedup(items: list[ActionItem]) -> list[ActionItem]:
    """여러 청크에서 같은 액션아이템이 중복 추출될 수 있어 정규화 제목 기준 제거.
    동일 제목이면 confidence 높은 쪽을 남긴다."""
    best: dict[str, ActionItem] = {}
    for a in items:
        key = re.sub(r"\s+", "", a.title.lower())
        if key not in best or a.confidence > best[key].confidence:
            best[key] = a
    return list(best.values())


def run(transcript_path: str | Path) -> dict:
    meta, utterances = load_transcript(transcript_path)
    chunks = to_chunks(utterances)
    valid_seg_ids = {u.seg_id for u in utterances}
    glossary = abbrev_glossary(utterances)

    provider = get_provider()
    all_items: list[ActionItem] = []
    for ch in chunks:
        all_items.extend(
            extract_from_chunk(ch, valid_seg_ids, provider, glossary)
        )
    all_items = _dedup(all_items)

    con = db.connect()
    db.upsert_meeting(con, meta)
    db.upsert_utterances(con, meta["meeting_id"], utterances)
    db.upsert_chunks(con, meta["meeting_id"], chunks)
    db.upsert_action_items(con, meta["meeting_id"], all_items)
    con.close()

    # Slack 페이로드 샘플 저장
    payload = build_slack_payload(meta.get("title") or meta["meeting_id"], all_items)
    out = config.DATA_DIR / "slack_payload_sample.json"
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    n_low = sum(1 for a in all_items if a.confidence < 0.6)
    print(f"✅ provider={config.LLM_PROVIDER} | meeting={meta['meeting_id']} | "
          f"utterances={len(utterances)} chunks={len(chunks)} "
          f"action_items={len(all_items)} (낮은신뢰 {n_low}건)")
    print(f"   약어 용어집: {list(glossary.keys())}")
    print(f"   Slack 페이로드 → {out}")
    return {"meta": meta, "n_action_items": len(all_items)}


if __name__ == "__main__":
    path = sys.argv[1] if len(sys.argv) > 1 else str(config.ROOT / DEFAULT_INPUT)
    run(path)
