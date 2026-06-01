"""DuckDB 적재 레이어. 멱등성(idempotency) 보장이 핵심.

전략: 모든 테이블에 PRIMARY KEY를 두고, 적재 전 해당 meeting_id 행을 DELETE 후 INSERT.
→ 파이프라인을 몇 번을 재실행해도 같은 결과(중복 없음, 깨짐 없음)를 보장한다.
"""
from __future__ import annotations

import duckdb

from . import config
from .schemas import ActionItem, Chunk, Utterance

SCHEMA = """
CREATE TABLE IF NOT EXISTS meetings (
    meeting_id   VARCHAR PRIMARY KEY,
    title        VARCHAR,
    advertiser   VARCHAR,
    date         DATE,
    duration_sec INTEGER
);
CREATE TABLE IF NOT EXISTS utterances (
    meeting_id   VARCHAR,
    seg_id       INTEGER,
    speaker_code VARCHAR,
    speaker_role VARCHAR,
    start_sec    DOUBLE,
    end_sec      DOUBLE,
    text         VARCHAR,
    PRIMARY KEY (meeting_id, seg_id)
);
CREATE TABLE IF NOT EXISTS chunks (
    meeting_id VARCHAR,
    chunk_id   INTEGER,
    seg_ids    VARCHAR,
    text       VARCHAR,
    PRIMARY KEY (meeting_id, chunk_id)
);
CREATE TABLE IF NOT EXISTS action_items (
    meeting_id    VARCHAR,
    action_id     INTEGER,
    title         VARCHAR,
    owner_role    VARCHAR,
    due           VARCHAR,
    status        VARCHAR,
    confidence    DOUBLE,
    source_seg_ids VARCHAR,
    source_quote  VARCHAR,
    PRIMARY KEY (meeting_id, action_id)
);
"""


def connect() -> duckdb.DuckDBPyConnection:
    con = duckdb.connect(str(config.DB_PATH))
    con.execute(SCHEMA)
    return con


def _replace(con, table: str, meeting_id: str) -> None:
    """멱등성: 같은 meeting_id를 지우고 새로 넣는다."""
    con.execute(f"DELETE FROM {table} WHERE meeting_id = ?", [meeting_id])


def upsert_meeting(con, meta: dict) -> None:
    _replace(con, "meetings", meta["meeting_id"])
    con.execute(
        "INSERT INTO meetings VALUES (?, ?, ?, ?, ?)",
        [meta["meeting_id"], meta.get("title", ""), meta.get("advertiser", ""),
         meta.get("date") or None, int(meta.get("duration_sec") or 0)],
    )


def upsert_utterances(con, meeting_id: str, items: list[Utterance]) -> None:
    _replace(con, "utterances", meeting_id)
    con.executemany(
        "INSERT INTO utterances VALUES (?, ?, ?, ?, ?, ?, ?)",
        [[u.meeting_id, u.seg_id, u.speaker_code, u.speaker_role,
          u.start, u.end, u.text] for u in items],
    )


def upsert_chunks(con, meeting_id: str, items: list[Chunk]) -> None:
    _replace(con, "chunks", meeting_id)
    con.executemany(
        "INSERT INTO chunks VALUES (?, ?, ?, ?)",
        [[c.meeting_id, c.chunk_id, ",".join(map(str, c.seg_ids)), c.text]
         for c in items],
    )


def upsert_action_items(con, meeting_id: str, items: list[ActionItem]) -> None:
    _replace(con, "action_items", meeting_id)
    rows = []
    for idx, a in enumerate(items):
        rows.append([
            a.meeting_id, idx, a.title, a.owner_role, a.due, a.status.value,
            a.confidence, ",".join(map(str, a.source_seg_ids)), a.source_quote,
        ])
    con.executemany(
        "INSERT INTO action_items VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", rows
    )
