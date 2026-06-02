"""DuckDB 적재 레이어. 멱등성(idempotency)과 '추출/트래킹 분리'가 핵심.

레이어 분리:
  - action_items: AI 추출 결과. 파이프라인 재실행마다 delete-replace (멱등 재생성).
  - action_status: 사람이 관리하는 진행상황(open/in_progress/done/blocked + 지연사유).
    파이프라인이 절대 건드리지 않음 → 재실행해도 사람의 상태 변경이 보존된다.
  - status_history: 상태 변경 이력(감사 추적).

두 레이어는 안정 식별자 action_key(= meeting_id + 정규화 제목 해시)로 연결된다.
LLM 출력은 비결정적이라 제목이 미세하게 흔들릴 수 있어, meeting_id를 멱등 단위로
삼고 회의 단위로 추출을 통째 교체하되, 상태는 action_key로 따로 보존한다.
"""
from __future__ import annotations

import hashlib
import re

import duckdb

from . import config
from .schemas import ActionItem, Chunk, Utterance

VALID_STATUSES = ("open", "in_progress", "done", "blocked")

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
-- 직원 마스터 (정규화: 직원 정보는 여기 한 곳에만)
-- is_adhoc: STT 미상 화자 등 임시로 생성된 행 표시 → 정식 직원과 구분(리포트에서 제외 가능)
CREATE TABLE IF NOT EXISTS employees (
    employee_id VARCHAR PRIMARY KEY,
    name        VARCHAR,
    role        VARCHAR,
    is_adhoc    BOOLEAN DEFAULT FALSE
);
-- 회의 참석자 (회의↔직원 다대다 연결, FK만 저장). 담당자 매핑의 '정답 후보'.
CREATE TABLE IF NOT EXISTS meeting_participants (
    meeting_id  VARCHAR,
    employee_id VARCHAR,
    PRIMARY KEY (meeting_id, employee_id)
);
-- 추출 레이어 (재실행마다 갱신)
CREATE TABLE IF NOT EXISTS action_items (
    meeting_id    VARCHAR,
    action_id     INTEGER,
    action_key    VARCHAR,
    title         VARCHAR,
    owner_role    VARCHAR,
    due           VARCHAR,
    confidence    DOUBLE,
    source_seg_ids VARCHAR,
    source_quote  VARCHAR,
    PRIMARY KEY (meeting_id, action_id)
);
-- 트래킹 레이어 (사람 소유, 재실행에도 보존)
CREATE TABLE IF NOT EXISTS action_status (
    action_key     VARCHAR PRIMARY KEY,
    meeting_id     VARCHAR,
    status         VARCHAR DEFAULT 'open',
    delay_reason   VARCHAR,
    owner_override VARCHAR,   -- 사람이 직접 고친 담당자 (재실행에도 보존)
    updated_at     TIMESTAMP,
    updated_by     VARCHAR
);
CREATE SEQUENCE IF NOT EXISTS seq_status_history START 1;
CREATE TABLE IF NOT EXISTS status_history (
    id         INTEGER DEFAULT nextval('seq_status_history'),
    action_key VARCHAR,
    old_status VARCHAR,
    new_status VARCHAR,
    reason     VARCHAR,
    changed_at TIMESTAMP,
    changed_by VARCHAR
);
"""


def make_action_key(meeting_id: str, title: str) -> str:
    """제목 정규화 후 해시 → 재추출 시에도 같은 액션아이템을 같은 키로 식별."""
    norm = re.sub(r"\s+", "", title.lower())
    h = hashlib.md5(f"{meeting_id}|{norm}".encode("utf-8")).hexdigest()[:16]
    return h


# 직원 마스터 시드 (가상 광고대행사 조직도). 실서비스에선 HR 시스템 연동으로 대체.
SEED_EMPLOYEES = [
    ("E01", "지훈", "마케팅 팀장"),
    ("E02", "수아", "퍼포먼스 마케터"),
    ("E03", "채린", "콘텐츠 디자이너"),
    ("E04", "민준", "퍼포먼스 마케터"),
    ("E05", "서연", "콘텐츠 디자이너"),
    ("E06", "도윤", "데이터 분석가"),
    ("E07", "지아", "광고기획자(AE)"),
    ("E08", "현우", "미디어 플래너"),
]


def seed_employees(con) -> None:
    for eid, name, role in SEED_EMPLOYEES:
        con.execute(
            "INSERT INTO employees VALUES (?, ?, ?, FALSE) ON CONFLICT (employee_id) DO UPDATE "
            "SET name=excluded.name, role=excluded.role, is_adhoc=FALSE", [eid, name, role])


def connect() -> duckdb.DuckDBPyConnection:
    con = duckdb.connect(str(config.DB_PATH))
    con.execute(SCHEMA)
    # 기존 DB 호환: 컬럼이 없으면 추가 (스키마 진화)
    con.execute("ALTER TABLE action_status ADD COLUMN IF NOT EXISTS owner_override VARCHAR")
    seed_employees(con)
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


def _resolve_employee(con, name: str, role: str) -> str:
    """이름으로 직원 마스터에서 employee_id를 찾고, 없으면 ad-hoc 직원을 생성.
    (STT로 잡힌 '화자1' 등 명단에 없는 화자도 일관되게 식별)."""
    row = con.execute("SELECT employee_id FROM employees WHERE name=?", [name]).fetchone()
    if row:
        return row[0]
    eid = "AD" + hashlib.md5(name.encode("utf-8")).hexdigest()[:6]  # 이름 기준 결정적 id
    con.execute(
        "INSERT INTO employees VALUES (?, ?, ?, TRUE) ON CONFLICT (employee_id) DO NOTHING",
        [eid, name, role or "참석자"])  # is_adhoc=TRUE → 임시 화자로 표시(마스터 오염 방지)
    return eid


def upsert_participants(con, meeting_id: str, roster: list[dict]) -> None:
    """회의 참석자를 직원 FK로 연결 적재(멱등). roster: [{"name","role"}]."""
    _replace(con, "meeting_participants", meeting_id)
    seen = set()
    for p in roster or []:
        if not p:
            continue
        name = str(p.get("name") or p.get("role") or "참석자")
        role = str(p.get("role") or p.get("name") or "참석자")
        eid = _resolve_employee(con, name, role)
        if eid in seen:
            continue
        seen.add(eid)
        con.execute(
            "INSERT INTO meeting_participants VALUES (?, ?) ON CONFLICT DO NOTHING",
            [meeting_id, eid])


def get_participants(con, meeting_id: str) -> list[dict]:
    """회의↔직원 조인으로 명단 복원(이름·역할)."""
    rows = con.execute(
        "SELECT e.name, e.role FROM meeting_participants mp "
        "JOIN employees e USING (employee_id) WHERE mp.meeting_id=? ORDER BY e.name",
        [meeting_id]).fetchall()
    return [{"name": n, "role": r} for n, r in rows]


def upsert_utterances(con, meeting_id: str, items: list[Utterance]) -> None:
    _replace(con, "utterances", meeting_id)
    if not items:
        return
    con.executemany(
        "INSERT INTO utterances VALUES (?, ?, ?, ?, ?, ?, ?)",
        [[u.meeting_id, u.seg_id, u.speaker_code, u.speaker_role,
          u.start, u.end, u.text] for u in items],
    )


def upsert_chunks(con, meeting_id: str, items: list[Chunk]) -> None:
    _replace(con, "chunks", meeting_id)
    if not items:
        return
    con.executemany(
        "INSERT INTO chunks VALUES (?, ?, ?, ?)",
        [[c.meeting_id, c.chunk_id, ",".join(map(str, c.seg_ids)), c.text]
         for c in items],
    )


def upsert_action_items(con, meeting_id: str, items: list[ActionItem]) -> None:
    """추출 결과를 멱등 교체하고, 신규 action_key에 대해서만 상태행을 초기화한다.
    기존 action_status(사람 수정분)는 보존된다."""
    _replace(con, "action_items", meeting_id)
    rows = []
    keys = []
    for idx, a in enumerate(items):
        key = make_action_key(meeting_id, a.title)
        keys.append((key, a.status.value))
        rows.append([
            a.meeting_id, idx, key, a.title, a.owner_role, a.due,
            a.confidence, ",".join(map(str, a.source_seg_ids)), a.source_quote,
        ])
    if rows:
        con.executemany(
            "INSERT INTO action_items VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", rows
        )
    # 신규 액션아이템만 상태행 생성 (기존 사람 수정분은 건드리지 않음 → 보존)
    for key, init_status in keys:
        exists = con.execute(
            "SELECT 1 FROM action_status WHERE action_key = ?", [key]).fetchone()
        if not exists:
            con.execute(
                "INSERT INTO action_status (action_key, meeting_id, status, updated_at, updated_by)"
                " VALUES (?, ?, ?, now(), 'pipeline')",
                [key, meeting_id, init_status])


_KEEP = object()  # owner 인자 미지정 표시 (None=담당자 비우기와 구분)


def update_status(con, action_key: str, new_status: str,
                  reason: str | None = None, owner=_KEEP, by: str = "dashboard") -> None:
    """상태 변경 + (선택)담당자 override + 이력 기록. blocked일 때만 delay_reason 저장.

    owner: 미지정(_KEEP)이면 담당자 유지, 문자열이면 그 값으로 덮어씀, ''/None이면 AI값 사용으로 되돌림.
    """
    if new_status not in VALID_STATUSES:
        raise ValueError(f"허용되지 않는 상태: {new_status}")
    row = con.execute(
        "SELECT status FROM action_status WHERE action_key = ?", [action_key]).fetchone()
    old = row[0] if row else None
    reason = reason if new_status == "blocked" else None
    owner_norm = None if (owner in (None, "")) else str(owner).strip() if owner is not _KEEP else _KEEP

    if row:
        if owner_norm is _KEEP:
            con.execute(
                "UPDATE action_status SET status=?, delay_reason=?, updated_at=now(), updated_by=?"
                " WHERE action_key=?", [new_status, reason, by, action_key])
        else:
            con.execute(
                "UPDATE action_status SET status=?, delay_reason=?, owner_override=?,"
                " updated_at=now(), updated_by=? WHERE action_key=?",
                [new_status, reason, owner_norm, by, action_key])
    else:
        con.execute(
            "INSERT INTO action_status (action_key, status, delay_reason, owner_override, updated_at, updated_by)"
            " VALUES (?, ?, ?, ?, now(), ?)",
            [action_key, new_status, reason, (None if owner_norm is _KEEP else owner_norm), by])
    con.execute(
        "INSERT INTO status_history (action_key, old_status, new_status, reason, changed_at, changed_by)"
        " VALUES (?, ?, ?, ?, now(), ?)", [action_key, old, new_status, reason, by])
